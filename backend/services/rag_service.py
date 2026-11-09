"""Question -> retrieval -> bounded prompt -> provider -> verified quotes.

Current-chat history resolves references but is never company-policy evidence. Provider selection is deliberately deferred.
"""

from dataclasses import asdict
from datetime import UTC, datetime, timedelta
import hashlib
import os
from time import perf_counter
import uuid

from backend.database.models import Document, DocumentChunk
from backend.prompts.rag_prompts import PROMPT_VERSION, RESPONSE_SCHEMA
from backend.repositories.conversation_repository import ConversationRepository
from backend.services.context_service import ContextBuilder, serialize_messages
from backend.services.document_service import DocumentNotFoundError
from backend.services.grounding_service import GroundingResult, insufficient_result, quote_boundary_offsets, validate_claims
from backend.services.llm_service import (LLMNotConfiguredError, LLMProviderError, LLMRateLimitError,
                                          LLMResponseError, configured_llm)
from backend.services.search_service import SearchService
from backend.services.table_context import missing_table_header
from backend.services.query_rewrite_service import QueryRewriteService, Rewrite, REWRITE_VERSION
import json


class RAGService:
    def __init__(self, *, session_factory, embedder, retriever, llm=None, context_builder=None,
                 embedding_provider=None, retrieval_provider=None):
        self.session_factory, self.embedder, self.retriever = session_factory, embedder, retriever
        self.embedding_provider, self.retrieval_provider = embedding_provider, retrieval_provider
        self.llm = llm if llm is not None else configured_llm()
        self.context_builder = context_builder or ContextBuilder(max_context_bytes=int(os.getenv("RAG_MAX_CONTEXT_BYTES", "12000")))
        self.max_output_tokens = int(os.getenv("RAG_MAX_OUTPUT_TOKENS", "512"))
        if not 64 <= self.max_output_tokens <= 4096:
            raise ValueError("RAG_MAX_OUTPUT_TOKENS must be between 64 and 4096")

    def config(self):
        return {"provider": getattr(self.llm, "provider_name", "injected"),
                "model": getattr(self.llm, "model_name", None),
                "configured": getattr(self.llm, "configured", True), "prompt_version": PROMPT_VERSION,
                "context_token_budget": None, "context_byte_budget": self.context_builder.max_context_bytes,
                "max_output_tokens": self.max_output_tokens, "token_count_method": "utf8_bytes",
                "retrieval_mode": os.getenv("RETRIEVAL_MODE", "vector"), "answer_mode": "verified_source_quotes",
                "answer_style": os.getenv("RAG_ANSWER_STYLE", "quotes").strip().lower() or "quotes", "conversation_memory": True, "memory_scope": "current_conversation", "history_max_messages": 6, "history_max_bytes": 6000}

    def _context(self, question, top_k):
        if not isinstance(top_k, int) or isinstance(top_k, bool) or not 1 <= top_k <= 20:
            raise ValueError("top_k must be between 1 and 20")
        if self.embedder is None and self.embedding_provider is not None:
            self.embedder = self.embedding_provider()
        if self.retriever is None and self.retrieval_provider is not None:
            self.retriever = self.retrieval_provider()
        result = SearchService(session_factory=self.session_factory, embedder=self.embedder,
                               retriever=self.retriever).search(question, top_k)
        hits = result["hits"]
        with self.session_factory() as session:
            snapshots = ConversationRepository(session).source_snapshots([hit["doc_id"] for hit in hits])
            prepared = []
            for hit in hits:
                snapshot = snapshots.get(str(hit["doc_id"]))
                start, end = hit.get("start_char"), hit.get("end_char")
                if (snapshot is None or snapshot["source_hash"] != hit.get("source_hash")
                    or snapshot["version"] != hit.get("version") or not isinstance(start, int) or not isinstance(end, int)
                    or not 0 <= start < end <= len(snapshot["text"]) or snapshot["text"][start:end] != hit["text"]):
                    continue  # Legacy/changed provenance must be deliberately reindexed.
                prepared.append({**hit, **quote_boundary_offsets(snapshot["text"], start, end)})
                header_hit = self._table_header_chunk(session, hit, snapshot["text"])
                if header_hit is not None:
                    prepared.append(header_hit)
        return self.context_builder.build(result["question"], prepared)

    @staticmethod
    def _table_header_chunk(session, hit, source_text):
        """The stored chunk holding the header row of a table continued in hit.

        It is an ordinary immutable chunk (own chunk_id, chunk_text and
        offsets), so context de-duplication, quote validation and the
        post-generation eligibility recheck all apply to it unchanged.
        """
        header = missing_table_header(source_text, hit["start_char"], hit["end_char"])
        if header is None:
            return None
        header_start, header_end = header
        row = session.query(DocumentChunk).join(Document).filter(
            DocumentChunk.document_id == uuid.UUID(str(hit["doc_id"])),
            DocumentChunk.is_active.is_(True),
            Document.status == "active",
            Document.is_online.is_(True),
            Document.version == hit["version"],
            Document.source_hash == hit["source_hash"],
            DocumentChunk.start_char <= header_start,
            DocumentChunk.end_char >= header_end,
        ).order_by(DocumentChunk.start_char.desc()).first()
        if (row is None or str(row.chunk_id) == str(hit["chunk_id"])
                or not 0 <= row.start_char < row.end_char <= len(source_text)
                or source_text[row.start_char:row.end_char] != row.chunk_text):
            return None
        return {**hit, "chunk_id": str(row.chunk_id), "text": row.chunk_text,
                "start_char": row.start_char, "end_char": row.end_char,
                "page_number": row.page_number, "section_heading": row.section_heading,
                "similarity_score": None, "bm25_score": None, "fusion_score": None,
                "context_role": "table_header_chunk",
                **quote_boundary_offsets(source_text, row.start_char, row.end_char)}

    def _generate_and_validate(self, context):
        """One model call, retried ONCE if the reply breaks the JSON format.

        Structured output occasionally fails (Groq "json_validate_failed", a
        cut-off reply, a missing field). The retry doubles the output budget
        (max 4096), because a reasoning model's hidden thinking can use up the
        budget before the JSON is finished. Rate limits and other provider
        errors are not retried: a retry would only spend more of the allowance.
        Returns (grounding result, number of model calls).
        """
        for attempt in (1, 2):
            budget = self.max_output_tokens if attempt == 1 else min(2 * self.max_output_tokens, 4096)
            try:
                payload = self.llm.generate(context.messages, max_output_tokens=budget,
                                            response_schema=RESPONSE_SCHEMA)
                return validate_claims(payload, context), attempt
            except LLMRateLimitError:
                raise
            except (LLMResponseError, LLMProviderError) as exc:
                format_error = isinstance(exc, LLMResponseError) or "json_validate_failed" in str(exc)
                if attempt == 2 or not format_error:
                    raise

    def preview(self, question, top_k=3):
        context = self._context(question, top_k)
        return {"question": question.strip(), **asdict(context), "token_count_method": "utf8_bytes"}

    @staticmethod
    def _owned_conversation(repository, conversation_id, actor):
        if conversation_id is None:
            return None
        conversation = repository.get(uuid.UUID(str(conversation_id)))
        if conversation is None:
            raise DocumentNotFoundError("Conversation not found")
        if conversation.user_id != actor.user_id:
            raise PermissionError("You can only access your own conversations")
        return conversation

    def answer(self, question, actor, top_k=3, conversation_id=None):
        if not isinstance(question, str) or not question.strip() or len(question) > 4000:
            raise ValueError("Enter a question of 1 to 4000 characters")
        question = question.strip()
        # Ownership is checked before model work, then again in persistence.
        history = []
        history_unavailable = False
        if conversation_id is not None:
            with self.session_factory() as session:
                repository = ConversationRepository(session)
                owned = self._owned_conversation(repository, conversation_id, actor)
                rows = repository.recent_owned_messages(owned.id, actor.user_id)
                for row in reversed(rows):
                    item = {"message_id": str(row.message_id), "role": row.message_provider, "content": row.content}
                    if len(serialize_messages([item, *history]).encode('utf-8')) > 6000:
                        break
                    history.insert(0, item)
                history_unavailable = bool(rows) and not history
        if not getattr(self.llm, "configured", True):
            raise LLMNotConfiguredError("LLM provider is not configured. Preview the context or use Retrieval lab; provider calls are deferred.")
        started = perf_counter()
        llm_attempts = 0
        if not isinstance(top_k, int) or isinstance(top_k, bool) or not 1 <= top_k <= 20:
            raise ValueError("top_k must be between 1 and 20")
        rewrite = (Rewrite('', 'Please restate the relevant details and your question; the recent message exceeds the conversation context limit.', [], [])
                   if history_unavailable else QueryRewriteService().resolve(question, history, self.llm))
        if rewrite.clarification:
            context = self.context_builder.build(question, [])
            grounded = GroundingResult(rewrite.clarification, 'clarification_required', [], 0, 0, 'ambiguous_reference')
        else:
            context = self._context(rewrite.query, top_k)
            if history:
                context = self.context_builder.build(rewrite.query, context.sources, history=history, original_question=question)
            if context.sources:
                grounded, llm_attempts = self._generate_and_validate(context)
            else:
                grounded = insufficient_result("no_eligible_sources")
        model_called = bool(rewrite.messages or context.sources)
        elapsed_ms = round((perf_counter() - started) * 1000, 2)
        with self.session_factory() as session:
            repository = ConversationRepository(session)
            conversation = self._owned_conversation(repository, conversation_id, actor)
            sources = context.sources
            if sources and not repository.sources_still_eligible(sources):
                grounded, sources = insufficient_result("sources_changed"), []
            if conversation is None:
                conversation = repository.create(actor.user_id, question)
            stamp = datetime.now(UTC).replace(tzinfo=None)
            question_message = repository.add_message(conversation.id, "user", question, created_at=stamp)
            metadata = {"answer_status": grounded.status, "retrieval_query": rewrite.query or None,
                        "rewrite_version": REWRITE_VERSION if history else None,
                        "history_message_ids": [item['message_id'] for item in history],
                        "answer_history_message_ids": [item['message_id'] for item in json.loads(context.messages[-1]['content']).get('current_chat_history', [])],
                        "rewrite_prompt_sha256": hashlib.sha256(serialize_messages(rewrite.messages).encode('utf-8')).hexdigest() if rewrite.messages else None,
                        "prompt_version": PROMPT_VERSION,
                        "prompt_sha256": hashlib.sha256(serialize_messages(context.messages).encode("utf-8")).hexdigest(),
                        "context_bytes": context.context_bytes, "context_tokens": None,
                        "context_byte_budget": self.context_builder.max_context_bytes,
                        "max_output_tokens": self.max_output_tokens, "retrieval_top_k": top_k, "retrieval_mode": os.getenv("RETRIEVAL_MODE", "vector"),
                        "token_count_method": "utf8_bytes", "answer_mode": "verified_source_quotes",
                        "citations": grounded.citations,
                        "grounding": {"validated_quotes": grounded.validated_quotes,
                                      "rejected_claims": grounded.rejected_claims, "reason": grounded.reason,
                                      "rejected": list(grounded.rejected_details),
                                      "answer_style": grounded.answer_style,
                                      "sentence": grounded.sentence,
                                      "sentence_check": grounded.sentence_check,
                                      "llm_attempts": llm_attempts},
                        "model_called": model_called, "provider": getattr(self.llm, "provider_name", "injected")}
            message = repository.add_message(conversation.id, "assistant", grounded.answer,
                reply_to_message_id=question_message.message_id,
                created_at=stamp + timedelta(microseconds=1),
                model_name=getattr(self.llm, "model_name", None) if model_called else None,
                response_time_ms=round(elapsed_ms), top_k_used=top_k,
                insufficient_context=None if grounded.status == "clarification_required" else grounded.status != "answered", generation_metadata=metadata)
            repository.record_context(message.message_id, sources)
            response_id, result_conversation_id = str(message.message_id), str(conversation.id)
            session.commit()
        return {"response_id": response_id, "conversation_id": result_conversation_id,
                "answer": grounded.answer, "status": grounded.status, "citations": grounded.citations,
                "elapsed_ms": elapsed_ms, "model": getattr(self.llm, "model_name", None) if model_called else None,
                "prompt_version": PROMPT_VERSION, "context_tokens": None, "context_bytes": context.context_bytes,
                "token_count_method": "utf8_bytes", "grounding": metadata["grounding"]}

    def conversations(self, actor):
        with self.session_factory() as session:
            rows = ConversationRepository(session).list_for_user(actor.user_id)
            return {"conversations": [{"conversation_id": str(row.id), "title": row.title,
                "started_at": row.started_at.replace(tzinfo=UTC) if row.started_at else None} for row in rows]}

    def conversation(self, conversation_id, actor):
        with self.session_factory() as session:
            repository = ConversationRepository(session)
            conversation = self._owned_conversation(repository, conversation_id, actor)
            rows = repository.messages(conversation.id)
            # Historical records are a user-owned record; current source-file
            # access remains separately protected by online/version checks.
            return {"conversation_id": str(conversation.id), "title": conversation.title,
                    "messages": [{"message_id": str(row.message_id), "role": row.message_provider,
                        "content": row.content, "created_at": row.created_at.replace(tzinfo=UTC) if row.created_at else None,
                        "generation": row.generation_metadata} for row in rows]}
