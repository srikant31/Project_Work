"""Owned history reads require PostgreSQL, not a model or vector index."""

from datetime import UTC
import uuid

from backend.repositories.conversation_repository import ConversationRepository
from backend.services.document_service import DocumentNotFoundError


class ConversationService:
    def __init__(self, session_factory):
        self.session_factory = session_factory

    def list(self, actor):
        with self.session_factory() as session:
            rows = ConversationRepository(session).list_for_user(actor.user_id)
            return {"conversations": [{"conversation_id": str(row.id), "title": row.title,
                "started_at": row.started_at.replace(tzinfo=UTC) if row.started_at else None} for row in rows]}

    def get(self, conversation_id, actor):
        with self.session_factory() as session:
            repository = ConversationRepository(session)
            conversation = repository.get(uuid.UUID(str(conversation_id)))
            if conversation is None:
                raise DocumentNotFoundError("Conversation not found")
            if conversation.user_id != actor.user_id:
                raise PermissionError("You can only access your own conversations")
            return {"conversation_id": str(conversation.id), "title": conversation.title,
                "messages": [{"message_id": str(row.message_id), "role": row.message_provider,
                    "content": row.content, "created_at": row.created_at.replace(tzinfo=UTC) if row.created_at else None,
                    "generation": row.generation_metadata} for row in repository.messages(conversation.id)]}
