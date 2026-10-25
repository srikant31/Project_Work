"""Explicit safe serialization for the admin's DB-only review interface."""

from datetime import UTC

from backend.repositories.admin_review_repository import AdminReviewRepository
from backend.services.document_service import DocumentNotFoundError
from backend.utils.date_ranges import utc_bounds


def utc(value):
    if value is None:
        return None
    return (value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)).isoformat()


def public_user(user):
    # Do not serialize ORM dictionaries: they contain password hashes.
    return {"user_id": str(user.user_id), "email": user.email,
            "role": user.role, "department": user.department}


def safe_citations(value):
    keys = {"source_id", "doc_id", "doc_name", "version", "source_hash", "chunk_id",
            "page_number", "section_heading", "quote", "start_char", "end_char"}
    if not isinstance(value, list):
        return []
    return [{key: item for key, item in citation.items()
             if key in keys and (item is None or isinstance(item, (str, int, float, bool)))}
            for citation in value if isinstance(citation, dict)]


def safe_metadata(value):
    """Only persisted diagnostic fields; never expose arbitrary legacy config."""
    if not isinstance(value, dict):
        return None
    keys = {"prompt_version", "prompt_sha256", "context_bytes", "context_tokens",
            "context_byte_budget", "max_output_tokens", "retrieval_top_k",
            "token_count_method", "answer_mode", "model_called", "provider"}
    result = {key: item for key, item in value.items()
              if key in keys and (item is None or isinstance(item, (str, int, float, bool)))}
    if "citations" in value:
        result["citations"] = safe_citations(value["citations"])
    grounding = value.get("grounding")
    if isinstance(grounding, dict):
        result["grounding"] = {key: item for key, item in grounding.items()
            if key in {"validated_quotes", "rejected_claims", "reason"}
            and (item is None or isinstance(item, (str, int, float, bool)))}
    return result


class AdminReviewService:
    def __init__(self, session_factory):
        self.session_factory = session_factory

    def users(self, search="", limit=25, offset=0):
        with self.session_factory() as session:
            rows, total = AdminReviewRepository(session).users(search.strip(), limit, offset)
            return {"users": [{**public_user(user), "conversation_count": count,
                "negative_count": negative, "last_activity_at": utc(last)}
                for user, count, negative, last in rows],
                "total": total, "limit": limit, "offset": offset}

    def conversations(self, user_id, limit=50, offset=0):
        with self.session_factory() as session:
            repository = AdminReviewRepository(session)
            if not repository.user_exists(user_id):
                raise DocumentNotFoundError("User not found")
            rows, total = repository.conversations(user_id, limit, offset)
            return {"conversations": [{"conversation_id": str(conversation.id),
                "user_id": str(conversation.user_id), "title": conversation.title,
                "started_at": utc(conversation.started_at), "last_activity_at": utc(last),
                "message_count": count} for conversation, count, last in rows],
                "total": total, "limit": limit, "offset": offset}

    @staticmethod
    def _serialize(row, categories):
        review_id, linkage, status, conversation, user, question, answer, feedback = row
        metadata = safe_metadata(answer.generation_metadata) if answer else None
        citations = metadata.get("citations", []) if metadata else []
        return {"review_id": str(review_id), "conversation_id": str(conversation.id),
            "conversation_title": conversation.title, "user": public_user(user),
            "question": {"message_id": str(question.message_id), "content": question.content,
                "created_at": utc(question.created_at)} if question else None,
            "answer": {"message_id": str(answer.message_id), "content": answer.content,
                "created_at": utc(answer.created_at), "model_name": answer.model_name,
                "response_time_ms": answer.response_time_ms, "top_k_used": answer.top_k_used,
                "insufficient_context": answer.insufficient_context,
                "generation_metadata": metadata} if answer else None,
            "linkage": linkage, "status": status,
            "feedback": {"rating": feedback.rating, "created_at": utc(feedback.created_at),
                "updated_at": utc(feedback.updated_at)} if feedback else None,
            "citations": citations if isinstance(citations, list) else [],
            "categories": categories.get(answer.message_id, []) if answer else []}

    def reviews(self, *, user_id=None, conversation_id=None, feedback="all", status="all",
                search="", limit=20, offset=0, question_from=None, question_to=None):
        question_lower, question_upper = utc_bounds(question_from, question_to)
        with self.session_factory() as session:
            repository = AdminReviewRepository(session)
            if user_id is not None and not repository.user_exists(user_id):
                raise DocumentNotFoundError("User not found")
            if conversation_id is not None and not repository.conversation_exists(conversation_id):
                raise DocumentNotFoundError("Conversation not found")
            rows, total = repository.reviews(user_id=user_id, conversation_id=conversation_id,
                feedback=feedback, status=status, search=search.strip(), limit=limit, offset=offset,
                question_lower=question_lower, question_upper=question_upper)
            categories = repository.categories([row[6].message_id for row in rows if row[6]])
            return {"reviews": [self._serialize(row, categories) for row in rows],
                    "total": total, "limit": limit, "offset": offset}

    def review(self, message_id):
        with self.session_factory() as session:
            repository = AdminReviewRepository(session)
            row = repository.review(message_id)
            if row is None:
                raise DocumentNotFoundError("Review not found")
            answer = row[6]
            categories = repository.categories([answer.message_id] if answer else [])
            context = repository.context(answer.message_id) if answer else []
            return {"review": self._serialize(row, categories), "context": [
                {"chunk_id": str(chunk.chunk_id), "rank": retained.rank,
                 "similarity_score": retained.similarity_score, "text": chunk.chunk_text,
                 "doc_id": str(document.doc_id), "doc_name": document.doc_name,
                 "version": document.version, "category_name": category.category_name,
                 "page_number": chunk.page_number, "section_heading": chunk.section_heading,
                 "start_char": chunk.start_char, "end_char": chunk.end_char,
                 "is_online": document.is_online, "status": document.status}
                for retained, chunk, document, category in context]}
