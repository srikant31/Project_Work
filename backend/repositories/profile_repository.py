"""Current-user activity aggregates and current owned session lookup."""

from sqlalchemy import func

from backend.database.models import Conversation, Feedback, Message
from backend.repositories.auth_session_repository import AuthSessionRepository


class ProfileRepository:
    def __init__(self, session):
        self.session = session

    def activity(self, user_id):
        conversation_count = self.session.query(func.count(Conversation.id)).filter(
            Conversation.user_id == user_id).scalar()
        messages = dict(self.session.query(Message.message_provider, func.count(Message.message_id)).join(
            Conversation, Conversation.id == Message.conversation_id
        ).filter(Conversation.user_id == user_id).group_by(Message.message_provider).all())
        # Only the current user's votes on their own assistant responses count.
        votes = dict(self.session.query(Feedback.rating, func.count(Feedback.message_id)).join(
            Message, Message.message_id == Feedback.message_id
        ).join(Conversation, Conversation.id == Message.conversation_id).filter(
            Feedback.user_id == user_id, Conversation.user_id == user_id,
            Message.message_provider == "assistant"
        ).group_by(Feedback.rating).all())
        return {"conversations": conversation_count, "questions": messages.get("user", 0),
                "responses": messages.get("assistant", 0), "positive_feedback": votes.get("up", 0),
                "negative_feedback": votes.get("down", 0)}

    def current_session(self, user_id, digest):
        row = AuthSessionRepository(self.session).get_valid(digest)
        return row if row is not None and row.user_id == user_id else None
