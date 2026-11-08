"""Owned history reads require PostgreSQL, not a model or vector index."""

from datetime import UTC
import uuid

from backend.database.models import Document, DocumentChunk, Feedback, Message, RetrievedContext
from backend.repositories.conversation_repository import ConversationRepository
from backend.services.document_service import DocumentNotFoundError


class ConversationService:
    def __init__(self, session_factory):
        self.session_factory = session_factory

    def list(self, actor, limit=50, offset=0):
        with self.session_factory() as session:
            repository = ConversationRepository(session)
            rows = repository.list_for_user(actor.user_id, limit, offset)
            return {"total": repository.count_for_user(actor.user_id), "limit": limit, "offset": offset,
                "conversations": [{"conversation_id": str(row.id), "title": row.title,
                "started_at": row.started_at.replace(tzinfo=UTC) if row.started_at else None} for row in rows]}

    def get(self, conversation_id, actor):
        with self.session_factory() as session:
            repository = ConversationRepository(session)
            conversation = repository.get(uuid.UUID(str(conversation_id)))
            if conversation is None:
                raise DocumentNotFoundError("Conversation not found")
            if conversation.user_id != actor.user_id:
                raise PermissionError("You can only access your own conversations")
            ratings = repository.ratings(conversation.id, actor.user_id)
            comments = {str(f.message_id): f.comment for f in session.query(Feedback).join(Message, Message.message_id == Feedback.message_id).filter(Message.conversation_id == conversation.id, Feedback.user_id == actor.user_id)}
            return {"conversation_id": str(conversation.id), "title": conversation.title,
                "messages": [{"message_id": str(row.message_id), "role": row.message_provider,
                    "content": row.content, "created_at": row.created_at.replace(tzinfo=UTC) if row.created_at else None,
                    "reply_to_message_id": str(row.reply_to_message_id) if row.reply_to_message_id else None,
                    "rating": ratings.get(str(row.message_id)),
                    "feedback_comment": comments.get(str(row.message_id)),
                    "insufficient_context": row.insufficient_context,
                    "generation": self.safe_generation(row.generation_metadata)} for row in repository.messages(conversation.id)]}

    def evidence(self, response_id, chunk_id, actor):
        with self.session_factory() as session:
            repository = ConversationRepository(session)
            message = session.get(Message, response_id)
            conversation = repository.get(message.conversation_id) if message else None
            if conversation is None:
                raise DocumentNotFoundError("Response not found")
            if conversation.user_id != actor.user_id:
                raise PermissionError("You can only access evidence from your own responses")
            retained = session.get(RetrievedContext, (response_id, chunk_id))
            chunk = session.get(DocumentChunk, chunk_id) if retained else None
            doc = session.get(Document, chunk.document_id) if chunk else None
            if message.message_provider != "assistant" or doc is None:
                raise DocumentNotFoundError("Saved evidence not found")
            if not doc.is_online or doc.status not in {"active", "deactivated"}:
                raise PermissionError("This source is no longer available")
            citations = self.safe_generation(message.generation_metadata) or {}
            quotes = [c.get('quote', '') for c in citations.get('citations', []) if c.get('chunk_id') == str(chunk_id)]
            return {"chunk_id": str(chunk_id), "doc_name": doc.doc_name, "version": doc.version,
                "page_number": chunk.page_number, "section_heading": chunk.section_heading,
                "text": chunk.chunk_text, "quotes": quotes, "start_char": chunk.start_char, "end_char": chunk.end_char}

    @staticmethod
    def safe_generation(value):
        """History must not expose arbitrary legacy provider configuration/secrets."""
        if not isinstance(value, dict):
            return None
        result = {key: value[key] for key in ("prompt_version", "answer_mode", "answer_status") if isinstance(value.get(key), str)}
        fields = {"source_id", "doc_id", "doc_name", "version", "source_hash", "chunk_id", "page_number",
                  "section_heading", "start_char", "end_char", "quote"}
        raw = value.get("citations")
        result["citations"] = [{key: item[key] for key in fields if key in item and
            (item[key] is None or isinstance(item[key], (str, int)))}
            for item in raw[:20] if isinstance(item, dict)] if isinstance(raw, list) else []
        return result
