"""Feedback and its secret-free audit record share one real DB transaction."""

import json
from types import SimpleNamespace
import uuid

import pytest

pytestmark = pytest.mark.integration


@pytest.fixture
def audited_feedback(db_factory):
    from backend.database.models import Conversation, Message, User
    from backend.services.feedback_service import FeedbackService

    with db_factory() as session:
        owner = User(email="feedback-owner@example.test", role="employee")
        other = User(email="feedback-other@example.test", role="employee")
        session.add_all([owner, other])
        session.flush()
        conversation = Conversation(user_id=owner.user_id, title="Private fixture history")
        session.add(conversation)
        session.flush()
        question = Message(conversation_id=conversation.id, message_provider="user",
                           content="Private fixture question text must not be copied to audit")
        session.add(question)
        session.flush()
        answer = Message(conversation_id=conversation.id, message_provider="assistant",
                         reply_to_message_id=question.message_id,
                         content="Private fixture answer text must not be copied to audit",
                         insufficient_context=False)
        session.add(answer)
        session.commit()
        return SimpleNamespace(factory=db_factory, owner=owner, other=other,
            question=question, answer=answer, conversation=conversation,
            service=FeedbackService(db_factory))


def test_each_feedback_submission_records_actor_response_rating_and_links(audited_feedback):
    from backend.database.models import AuditLog, Feedback

    fixture = audited_feedback
    for rating in ("up", "down"):
        fixture.service.rate(fixture.answer.message_id, fixture.owner, rating)
    with fixture.factory() as session:
        rows = session.query(AuditLog).order_by(AuditLog.created_at).all()
        assert len(rows) == 2
        assert {row.details["rating"] for row in rows} == {"up", "down"}
        for row in rows:
            assert row.action == "feedback.submitted"
            assert row.user_id == fixture.owner.user_id
            assert row.entity_type == "response" and row.entity_id == fixture.answer.message_id
            assert row.created_at is not None
            assert row.details["conversation_id"] == str(fixture.conversation.id)
            assert row.details["question_message_id"] == str(fixture.question.message_id)
            payload = json.dumps(row.details)
            assert fixture.question.content not in payload and fixture.answer.content not in payload
            assert not any(secret in payload for secret in ("password", "cookie", "token"))
        vote = session.query(Feedback).one()
        assert vote.rating == "down" and vote.created_at and vote.updated_at


@pytest.mark.parametrize("kind", ["other_user", "invalid_rating", "unknown_response", "question_target"])
def test_rejected_feedback_creates_neither_vote_nor_audit_event(audited_feedback, kind):
    from backend.database.models import AuditLog, Feedback
    from backend.services.document_service import DocumentNotFoundError

    fixture = audited_feedback
    actor = fixture.other if kind == "other_user" else fixture.owner
    response_id = uuid.uuid4() if kind == "unknown_response" else (
        fixture.question.message_id if kind == "question_target" else fixture.answer.message_id)
    rating = "invalid" if kind == "invalid_rating" else "down"
    with pytest.raises((PermissionError, ValueError, DocumentNotFoundError)):
        fixture.service.rate(response_id, actor, rating)
    with fixture.factory() as session:
        assert session.query(Feedback).count() == 0
        assert session.query(AuditLog).count() == 0


def test_audit_failure_rolls_back_existing_vote_change_and_times(audited_feedback, monkeypatch):
    from backend.database.models import AuditLog, Feedback
    from backend.repositories.audit_repository import AuditRepository

    fixture = audited_feedback
    fixture.service.rate(fixture.answer.message_id, fixture.owner, "up")
    with fixture.factory() as session:
        previous = session.query(Feedback).one()
        times = previous.created_at, previous.updated_at

    def fail_audit(*args, **kwargs):
        raise RuntimeError("Simulated audit storage failure")

    monkeypatch.setattr(AuditRepository, "create", fail_audit)
    with pytest.raises(RuntimeError, match="Simulated audit storage failure"):
        fixture.service.rate(fixture.answer.message_id, fixture.owner, "down")
    with fixture.factory() as session:
        vote = session.query(Feedback).one()
        assert vote.rating == "up"
        assert (vote.created_at, vote.updated_at) == times
        assert session.query(AuditLog).count() == 1


@pytest.mark.parametrize("kind", ["cross_conversation", "assistant_target"])
def test_invalid_historical_question_link_is_not_asserted_in_audit(audited_feedback, kind):
    from backend.database.models import AuditLog, Conversation, Message

    fixture = audited_feedback
    with fixture.factory() as session:
        target_conversation = fixture.conversation.id
        if kind == "cross_conversation":
            other = Conversation(user_id=fixture.other.user_id, title="Other private history")
            session.add(other)
            session.flush()
            target_conversation = other.id
        target = Message(conversation_id=target_conversation,
            message_provider="user" if kind == "cross_conversation" else "assistant",
            content="A historical target that must not be treated as the question")
        session.add(target)
        session.flush()
        session.get(Message, fixture.answer.message_id).reply_to_message_id = target.message_id
        session.commit()
    fixture.service.rate(fixture.answer.message_id, fixture.owner, "down")
    with fixture.factory() as session:
        event = session.query(AuditLog).one()
        assert event.details["question_message_id"] is None
        assert event.details["conversation_id"] == str(fixture.conversation.id)
