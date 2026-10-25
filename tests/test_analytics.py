"""Real PostgreSQL cohort/aggregation tests with no AI resources or live data."""

from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace

import pytest


pytestmark = pytest.mark.integration
PASSWORD = "Analytics-fixture-2026!"
NOW = datetime(2026, 10, 23, 18, 0, tzinfo=UTC)


class ForbiddenResources:
    def __getattr__(self, name):
        raise AssertionError(f"Analytics must not initialize AI resources: {name}")


@pytest.fixture
def analytics_fixture(db_factory, tmp_path):
    from fastapi.testclient import TestClient
    from backend.api import analytics
    from backend.database.models import (Conversation, Document, DocumentCategory, DocumentChunk,
                                         Feedback, Message, RetrievedContext, User)
    from backend.main import create_app
    from backend.services.analytics_service import AnalyticsService
    from backend.utils.security import hash_password

    ids = {}
    with db_factory() as session:
        password_hash = hash_password(PASSWORD)
        for name, role in (("admin", "admin"), ("owner", "employee"), ("other", "employee")):
            row = User(email=f"analytics-{name}@example.test", role=role, password_hash=password_hash)
            session.add(row)
            session.flush()
            ids[name] = row.user_id
        conversations = {}
        for name in ("owner", "other"):
            row = Conversation(user_id=ids[name], title=f"{name} fixture", started_at=datetime(2026, 10, 20))
            session.add(row)
            session.flush()
            conversations[name] = row

        def message(name, role, stamp, content, owner="owner", **fields):
            row = Message(conversation_id=conversations[owner].id, message_provider=role,
                          created_at=datetime.fromisoformat(stamp), content=content, **fields)
            session.add(row)
            session.flush()
            ids[name] = row.message_id
            return row

        qold = message("old-question", "user", "2026-10-21T23:59:59", "How do I connect?")
        old = message("old-answer", "assistant", "2026-10-22T00:00:00", "Old correct answer",
                      reply_to_message_id=qold.message_id, insufficient_context=False, response_time_ms=100)
        qnew = message("new-question", "user", "2026-10-22T23:59:59", "\t HOW do I\tconnect?  \n")
        new = message("new-answer", "assistant", "2026-10-23T00:00:00", "No eligible sources",
                      reply_to_message_id=qnew.message_id, insufficient_context=True, response_time_ms=300,
                      generation_metadata={"grounding": {"reason": "no_eligible_sources"}, "api_key": "hidden"})
        qnext = message("next-question", "user", "2026-10-23T23:59:59.999999", "how do i connect?")
        next_answer = message("next-answer", "assistant", "2026-10-24T00:00:00", "Outside range",
                              reply_to_message_id=qnext.message_id, insufficient_context=None, response_time_ms=-4)
        message("orphan", "user", "2026-10-23T12:00:00", "No answer yet")
        message("legacy", "assistant", "2026-10-23T12:00:00", "Legacy refusal",
                insufficient_context=True, generation_metadata={"grounding": {"reason": {"api_key": "hidden"}}})
        message("unknown", "assistant", "2026-10-23T10:00:00", "Unknown legacy outcome")
        qother = message("other-question", "user", "2026-10-23T14:00:00", "Payroll", owner="other")
        other = message("other-answer", "assistant", "2026-10-23T15:00:00", "Payroll answer", owner="other",
                        reply_to_message_id=qother.message_id, insufficient_context=False, response_time_ms=0)
        message("future-question", "user", "2026-10-29T00:00:00", "Future")
        vote_stamp = NOW + timedelta(days=20)
        for answer, owner, rating in ((old, "owner", "down"), (new, "owner", "down"),
                                      (new, "other", "up"), (next_answer, "owner", "up"),
                                      (other, "other", "up")):
            session.add(Feedback(message_id=answer.message_id, user_id=ids[owner], rating=rating,
                                 created_at=vote_stamp, updated_at=vote_stamp,
                                 comment="Never expose this legacy comment"))
        category = session.query(DocumentCategory).filter_by(category_name="IT Support").one()
        docs = []
        for name, version in (("A policy", 2), ("B policy", 1)):
            document = Document(doc_name=name, doc_type="txt", category_id=category.category_id,
                                version=version, status="deactivated", is_online=False, doc_path="never-read.txt")
            session.add(document)
            session.flush()
            docs.append(document)
        for index in range(3):
            doc = docs[0] if index < 2 else docs[1]
            chunk = DocumentChunk(document_id=doc.doc_id, chunk_index=index, chunk_text="Evidence", is_active=False)
            session.add(chunk)
            session.flush()
            session.add(RetrievedContext(message_id=new.message_id, chunk_id=chunk.chunk_id, rank=index + 1))
            if index == 0:
                session.add(RetrievedContext(message_id=old.message_id, chunk_id=chunk.chunk_id, rank=1))
        ids["doc-a"], ids["doc-b"] = (doc.doc_id for doc in docs)
        session.commit()
    app = create_app(session_factory=db_factory, upload_root=tmp_path / "uploads")
    app.state.resources = ForbiddenResources()
    app.dependency_overrides[analytics.service] = lambda: AnalyticsService(db_factory, clock=lambda: NOW)
    with TestClient(app) as client:
        client.headers.update({"X-Requested-With": "helpdesk-ui"})
        yield SimpleNamespace(client=client, factory=db_factory, ids=ids, app=app)


def login(fixture, who="admin"):
    fixture.client.cookies.clear()
    response = fixture.client.post("/api/auth/login", json={
        "email": f"analytics-{who}@example.test", "password": PASSWORD})
    assert response.status_code == 200, response.text


def report(fixture, **params):
    response = fixture.client.get("/api/analytics", params=params)
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.parametrize("who,expected", [(None, 401), ("owner", 403)])
def test_analytics_is_admin_only(analytics_fixture, who, expected):
    if who:
        login(analytics_fixture, who)
    assert analytics_fixture.client.get("/api/analytics").status_code == expected


def test_cohorts_current_owner_votes_and_unknowns(analytics_fixture):
    login(analytics_fixture)
    data = report(analytics_fixture, date_from="2026-10-22", date_to="2026-10-23")
    assert data["summary"] == {
        "total_questions": 4, "questions_today": 3, "questions_this_week": 4,
        "total_responses": 5, "positive_feedback": 1, "negative_feedback": 2,
        "unrated_responses": 2, "average_response_time_ms": pytest.approx(400 / 3),
        "timed_response_count": 3, "unsupported_responses": 2,
        "no_useful_retrieval_responses": 1, "unknown_outcome_responses": 1,
    }
    # The votes were submitted outside this date range; current ratings still
    # belong to their response cohort. The non-owner up vote is not counted.
    assert data["daily_activity"] == [
        {"date": "2026-10-22", "questions": 1, "responses": 1, "positive_feedback": 0, "negative_feedback": 1},
        {"date": "2026-10-23", "questions": 3, "responses": 4, "positive_feedback": 1, "negative_feedback": 1},
    ]
    assert data["range"] == {"date_from": "2026-10-22", "date_to": "2026-10-23", "timezone": "UTC",
                             "generated_at": NOW.isoformat(), "daily_from": "2026-10-22", "daily_to": "2026-10-23"}
    from backend.database.models import Feedback

    with analytics_fixture.factory() as session:
        vote = session.get(Feedback, (analytics_fixture.ids["new-answer"], analytics_fixture.ids["owner"]))
        vote.rating = "up"
        session.commit()
    changed = report(analytics_fixture, date_from="2026-10-22", date_to="2026-10-23")
    assert changed["summary"]["negative_feedback"] == 1
    assert changed["summary"]["positive_feedback"] == 2


def test_document_usage_is_distinct_retained_response_document_pairs(analytics_fixture):
    login(analytics_fixture)
    data = report(analytics_fixture, date_from="2026-10-22", date_to="2026-10-23")
    assert data["document_usage"] == [
        {"doc_id": str(analytics_fixture.ids["doc-a"]), "doc_name": "A policy", "version": 2,
         "category_name": "IT Support", "responses": 2},
        {"doc_id": str(analytics_fixture.ids["doc-b"]), "doc_name": "B policy", "version": 1,
         "category_name": "IT Support", "responses": 1},
    ]


def test_faq_normalization_and_legacy_refusal_linkage_safe_fields(analytics_fixture):
    login(analytics_fixture)
    data = report(analytics_fixture, date_from="2026-10-22", date_to="2026-10-23")
    assert data["frequent_questions"][0] == {"question": "how do i connect?", "count": 2}
    unsupported = {row["review_id"]: row for row in data["unsupported_questions"]}
    assert unsupported[str(analytics_fixture.ids["new-answer"])]["question"] == "\t HOW do I\tconnect?  \n"
    assert unsupported[str(analytics_fixture.ids["new-answer"])]["reason"] == "no_eligible_sources"
    assert unsupported[str(analytics_fixture.ids["legacy"])]["question"] is None
    assert unsupported[str(analytics_fixture.ids["legacy"])]["reason"] is None
    serialized = str(data)
    for secret in ("password_hash", "token_hash", "comment", "api_key", "never-read.txt", "hidden"):
        assert secret not in serialized
    assert len(data["definitions"]) >= 10


def test_empty_range_zero_filled_and_timing_unknown_not_zero(analytics_fixture):
    login(analytics_fixture)
    data = report(analytics_fixture, date_from="2026-09-02", date_to="2026-09-03")
    assert data["summary"]["total_questions"] == data["summary"]["total_responses"] == 0
    assert data["summary"]["average_response_time_ms"] is None
    assert data["summary"]["timed_response_count"] == 0
    assert len(data["daily_activity"]) == 2
    assert all(row["questions"] == row["responses"] == 0 for row in data["daily_activity"])
    assert not data["document_usage"] and not data["frequent_questions"] and not data["unsupported_questions"]


def test_negative_latency_not_averaged_and_inclusive_end(analytics_fixture):
    login(analytics_fixture)
    data = report(analytics_fixture, date_from="2026-10-24", date_to="2026-10-24")
    assert data["summary"]["total_responses"] == 1
    assert data["summary"]["total_questions"] == 0
    assert data["summary"]["average_response_time_ms"] is None
    assert data["summary"]["timed_response_count"] == 0
    assert data["summary"]["unknown_outcome_responses"] == 1
    assert data["summary"]["questions_today"] == data["summary"]["questions_this_week"] == 0


def test_daily_span_bounded_and_one_sided_ranges(analytics_fixture):
    login(analytics_fixture)
    data = report(analytics_fixture, date_from="2026-01-01", date_to="2026-10-23")
    assert len(data["daily_activity"]) == 31
    assert data["range"]["daily_from"] == "2026-09-23"
    assert data["summary"]["total_questions"] == 5
    data = report(analytics_fixture, date_from="2026-10-29")
    assert data["range"]["daily_from"] == data["range"]["daily_to"] == "2026-10-29"
    assert data["summary"]["questions_today"] == 0
    assert data["summary"]["total_questions"] == 1
    data = report(analytics_fixture, date_to="2026-10-21")
    assert len(data["daily_activity"]) == 31 and data["summary"]["total_questions"] == 1
    data = report(analytics_fixture, date_to="0001-01-01")
    assert len(data["daily_activity"]) == 1 and data["range"]["daily_from"] == "0001-01-01"
    data = report(analytics_fixture, date_from="9999-12-30")
    assert len(data["daily_activity"]) == 1 and data["range"]["daily_to"] == "9999-12-30"


@pytest.mark.parametrize("params", [
    {"date_from": "2026-10-24", "date_to": "2026-10-23"},
    {"date_from": "not-a-date"}, {"date_to": "2026-02-30"}, {"date_to": "9999-12-31"},
    {"date_from": "9999-12-31"},
    {"date_from": "1792713600"}, {"date_from": "20261023"},
])
def test_invalid_ranges_return_422(analytics_fixture, params):
    login(analytics_fixture)
    assert analytics_fixture.client.get("/api/analytics", params=params).status_code == 422


def test_lists_bounded_and_no_mutations(analytics_fixture):
    from sqlalchemy import func, select
    from backend.database.models import Conversation, Feedback, Message, RetrievedContext

    login(analytics_fixture)
    with analytics_fixture.factory() as session:
        conversation = session.query(Conversation).filter_by(user_id=analytics_fixture.ids["owner"]).one()
        for index in range(15):
            question = Message(conversation_id=conversation.id, message_provider="user", content=f"Extra {index}",
                               created_at=datetime(2026, 10, 23, 13))
            session.add(question)
            session.flush()
            session.add(Message(conversation_id=conversation.id, reply_to_message_id=question.message_id,
                                message_provider="assistant", content=f"Refusal {index}",
                                insufficient_context=True, created_at=datetime(2026, 10, 23, 14)))
        session.commit()

    def counts():
        with analytics_fixture.factory() as session:
            return tuple(session.scalar(select(func.count()).select_from(model))
                         for model in (Conversation, Message, Feedback, RetrievedContext))

    before = counts()
    data = report(analytics_fixture)
    assert len(data["frequent_questions"]) == len(data["unsupported_questions"]) == 10
    assert len(data["daily_activity"]) == 31
    assert counts() == before
