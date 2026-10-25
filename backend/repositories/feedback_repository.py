"""Thumb votes are operational feedback, never golden evaluation labels."""

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from backend.database.models import Conversation, Feedback, Message


class FeedbackRepository:
    def __init__(self, session):
        self.session = session

    def response_owner(self, message_id):
        return self.session.execute(select(Message, Conversation.user_id).join(
            Conversation, Conversation.id == Message.conversation_id).where(Message.message_id == message_id)).first()

    def valid_question_id(self, response):
        """Do not turn an invalid historical link into an asserted audit trace."""
        question = self.session.get(Message, response.reply_to_message_id) if response.reply_to_message_id else None
        if (question is not None and question.message_provider == "user"
            and question.conversation_id == response.conversation_id):
            return str(question.message_id)
        return None

    def set_rating(self, message_id, user_id, rating):
        stamp = datetime.now(UTC)
        statement = insert(Feedback).values(message_id=message_id, user_id=user_id,
            rating=rating, comment=None, created_at=stamp, updated_at=stamp)
        self.session.execute(statement.on_conflict_do_update(
            index_elements=[Feedback.message_id, Feedback.user_id],
            set_={"rating": rating, "updated_at": stamp}))
        self.session.flush()
