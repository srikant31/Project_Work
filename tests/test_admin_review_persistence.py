"""Exact turn links and truthful vote timestamps in isolated PostgreSQL schemas.

These tests never infer historical question/answer links and never modify public
tables. The injected no-evidence context makes no model or embedding calls.
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError


pytestmark = pytest.mark.integration


@pytest.fixture
def saved_turn(db_factory):
    from backend.database.models import Conversation, Message, User

    with db_factory() as session:
        user = User(email="review-persistence@example.test", role="employee")
        session.add(user)
        session.flush()
        conversation = Conversation(user_id=user.user_id, title="Review persistence")
        session.add(conversation)
        session.flush()
        question = Message(conversation_id=conversation.id, message_provider="user", content="Exact saved question")
        session.add(question)
        session.flush()
        answer = Message(conversation_id=conversation.id, message_provider="assistant",
                         content="Exact saved answer", reply_to_message_id=question.message_id,
                         insufficient_context=False)
        session.add(answer)
        session.commit()
        return SimpleNamespace(user_id=user.user_id, conversation_id=conversation.id,
                               question_id=question.message_id, answer_id=answer.message_id)


def set_clock(monkeypatch, stamp):
    from backend.repositories import feedback_repository

    class FixedDatetime:
        @staticmethod
        def now(zone):
            assert zone is UTC
            return stamp

    monkeypatch.setattr(feedback_repository, "datetime", FixedDatetime)


def test_new_vote_and_change_have_utc_times_and_preserve_first_submission(db_factory, saved_turn, monkeypatch):
    from backend.database.models import Feedback, Message
    from backend.repositories.feedback_repository import FeedbackRepository

    first = datetime(2026, 10, 23, 10, 0, tzinfo=UTC)
    second = first + timedelta(minutes=7)
    set_clock(monkeypatch, first)
    with db_factory() as session:
        FeedbackRepository(session).set_rating(saved_turn.answer_id, saved_turn.user_id, "down")
        session.commit()
    set_clock(monkeypatch, second)
    with db_factory() as session:
        vote = session.get(Feedback, (saved_turn.answer_id, saved_turn.user_id))
        assert vote.rating == "down" and vote.created_at == first and vote.updated_at == first
        assert vote.created_at.utcoffset() == timedelta(0) and vote.comment is None
        FeedbackRepository(session).set_rating(saved_turn.answer_id, saved_turn.user_id, "up")
        session.commit()
    with db_factory() as session:
        votes = session.query(Feedback).all()
        assert len(votes) == 1
        assert votes[0].rating == "up" and votes[0].created_at == first and votes[0].updated_at == second
        assert session.get(Message, votes[0].message_id).content == "Exact saved answer"


def test_updating_legacy_vote_preserves_unknown_creation_and_historical_comment(db_factory, saved_turn, monkeypatch):
    from backend.database.models import Feedback
    from backend.repositories.feedback_repository import FeedbackRepository

    now = datetime(2026, 10, 23, 11, 0, tzinfo=UTC)
    with db_factory() as session:
        session.add(Feedback(message_id=saved_turn.answer_id, user_id=saved_turn.user_id,
                             rating="up", comment="Historical text remains historical"))
        session.commit()
    set_clock(monkeypatch, now)
    with db_factory() as session:
        FeedbackRepository(session).set_rating(saved_turn.answer_id, saved_turn.user_id, "down")
        session.commit()
    with db_factory() as session:
        vote = session.get(Feedback, (saved_turn.answer_id, saved_turn.user_id))
        assert vote.rating == "down" and vote.created_at is None and vote.updated_at == now
        assert vote.comment == "Historical text remains historical"


@pytest.mark.parametrize("case", ["different_conversation", "assistant_target", "missing_target", "user_reply"])
def test_repository_rejects_wrong_turn_links(db_factory, saved_turn, case):
    from backend.database.models import Conversation, Message
    from backend.repositories.conversation_repository import ConversationRepository

    with db_factory() as session:
        conversation_id, target, role = saved_turn.conversation_id, saved_turn.question_id, "assistant"
        if case == "different_conversation":
            other = Conversation(user_id=saved_turn.user_id, title="Different conversation")
            session.add(other)
            session.flush()
            conversation_id = other.id
        elif case == "assistant_target":
            target = saved_turn.answer_id
        elif case == "missing_target":
            target = uuid.uuid4()
        else:
            role = "user"
        before = session.query(Message).count()
        with pytest.raises(ValueError, match="same conversation"):
            ConversationRepository(session).add_message(conversation_id, role, "Invalid association",
                                                       reply_to_message_id=target)
        assert session.query(Message).count() == before


def test_rag_records_exact_link_for_each_turn_without_ordering_inference(db_factory, saved_turn, monkeypatch):
    from backend.database.models import Message, User
    from backend.services.context_service import ContextBuilder
    from backend.services.rag_service import RAGService

    class NoCallProvider:
        configured = True
        provider_name = "explicit-no-evidence-test-provider"
        model_name = None

        def generate(self, *args, **kwargs):
            raise AssertionError("No evidence means there must be no model call")

    with db_factory() as session:
        actor = session.get(User, saved_turn.user_id)
        session.expunge(actor)
    service = RAGService(session_factory=db_factory, embedder=None, retriever=None, llm=NoCallProvider())
    monkeypatch.setattr(service, "_context", lambda question, top_k: ContextBuilder().build(question, []))
    first = service.answer("First unanswered question", actor, conversation_id=saved_turn.conversation_id)
    second = service.answer("Second unanswered question", actor, conversation_id=saved_turn.conversation_id)
    with db_factory() as session:
        first_answer = session.get(Message, uuid.UUID(first["response_id"]))
        second_answer = session.get(Message, uuid.UUID(second["response_id"]))
        assert first_answer.reply_to_message_id != second_answer.reply_to_message_id
        assert first_answer.reply_to_message_id != saved_turn.question_id
        for answer, expected_question in ((first_answer, "First unanswered question"),
                                          (second_answer, "Second unanswered question")):
            question = session.get(Message, answer.reply_to_message_id)
            assert question.content == expected_question and question.message_provider == "user"
            assert question.conversation_id == answer.conversation_id == saved_turn.conversation_id
            assert answer.insufficient_context is True


def test_admin_review_upgrade_is_repeatable_and_never_invents_legacy_data(db_factory, saved_turn):
    from backend.database.models import Feedback, Message

    with db_factory() as session:
        session.add(Feedback(message_id=saved_turn.answer_id, user_id=saved_turn.user_id,
                             rating="down", comment="Existing vote"))
        session.commit()
    engine = db_factory.kw["bind"]
    with engine.begin() as connection:
        connection.exec_driver_sql("ALTER TABLE messages DROP COLUMN reply_to_message_id")
        connection.exec_driver_sql("ALTER TABLE feedback DROP COLUMN created_at")
        connection.exec_driver_sql("ALTER TABLE feedback DROP COLUMN updated_at")
    sql = (Path(__file__).resolve().parents[1] /
           "backend/database/migrations/006_admin_review_dashboard.sql").read_text(encoding="utf-8")

    def apply():
        with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
            connection.exec_driver_sql(sql)

    apply()
    with db_factory() as session:
        answer = session.get(Message, saved_turn.answer_id)
        vote = session.get(Feedback, (saved_turn.answer_id, saved_turn.user_id))
        assert answer.content == "Exact saved answer" and answer.reply_to_message_id is None
        assert vote.rating == "down" and vote.comment == "Existing vote"
        assert vote.created_at is None and vote.updated_at is None
        # Reapplication must preserve data written after the first upgrade too.
        answer.reply_to_message_id = saved_turn.question_id
        vote.updated_at = datetime(2026, 10, 23, 12, 0, tzinfo=UTC)
        session.commit()
    apply()
    with db_factory() as session:
        assert session.get(Message, saved_turn.answer_id).reply_to_message_id == saved_turn.question_id
        vote = session.get(Feedback, (saved_turn.answer_id, saved_turn.user_id))
        assert vote.created_at is None and vote.updated_at == datetime(2026, 10, 23, 12, 0, tzinfo=UTC)
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM pg_indexes WHERE schemaname=current_schema() "
                                      "AND indexname='idx_messages_reply_to_message_id'")) == 1
    with pytest.raises(IntegrityError):
        with engine.begin() as connection:
            connection.execute(text("UPDATE messages SET reply_to_message_id=:missing WHERE message_id=:answer"),
                               {"missing": uuid.uuid4(), "answer": saved_turn.answer_id})
