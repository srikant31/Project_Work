import uuid

from backend.repositories.feedback_repository import FeedbackRepository
from backend.services.audit_service import AuditService
from backend.services.document_service import DocumentNotFoundError


class FeedbackService:
    def __init__(self, session_factory):
        self.session_factory = session_factory

    def rate(self, response_id, actor, rating, comment=None):
        if comment is not None and (not isinstance(comment, str) or len(comment) > 600):
            raise ValueError("Feedback explanation must contain at most 600 characters")
        comment = comment.strip() or None if comment else None
        if rating == "up":
            comment = None
        if rating not in {"up", "down"}:
            raise ValueError("Choose thumbs up or thumbs down")
        response_id = uuid.UUID(str(response_id))
        with self.session_factory() as session:
            repository = FeedbackRepository(session)
            result = repository.response_owner(response_id)
            if result is None:
                raise DocumentNotFoundError("Response not found")
            message, owner = result
            if owner != actor.user_id:
                raise PermissionError("You can only rate your own responses")
            if message.message_provider != "assistant":
                raise ValueError("Only assistant responses can receive feedback")
            repository.set_rating(response_id, actor.user_id, rating, comment)
            # Commit the vote and its audit event together. Keep transcript text
            # out of the audit log; IDs allow an authorized admin to inspect it.
            AuditService(session).record(actor, "feedback.submitted", "response", response_id,
                {"rating": rating, "conversation_id": str(message.conversation_id),
                 "question_message_id": repository.valid_question_id(message)})
            session.commit()
        return {"response_id": str(response_id), "rating": rating, "comment": comment}
