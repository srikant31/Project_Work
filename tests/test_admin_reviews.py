"""Admin reads use only disposable PostgreSQL, never a model or vector index."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
import uuid

import pytest


pytestmark = pytest.mark.integration
PASSWORD = "Admin-review-validation-2026!"


class ForbiddenResources:
    def __getattr__(self, name):
        raise AssertionError(f"Admin review must not initialize AI resources: {name}")


@pytest.fixture
def dashboard(db_factory, tmp_path):
    from fastapi.testclient import TestClient
    from backend.database.models import (
        Conversation, Document, DocumentCategory, DocumentChunk, Feedback, Message,
        RetrievedContext, User,
    )
    from backend.main import create_app
    from backend.utils.security import hash_password

    ids, emails = {}, {}
    stamp = datetime(2026, 10, 23, 10, 0, 0)
    with db_factory() as session:
        password_hash = hash_password(PASSWORD)
        for name, role in (("admin", "admin"), ("owner", "employee"), ("other", "employee"), ("empty", "employee")):
            emails[name] = f"review-{name}@example.test"
            user = User(email=emails[name], role=role, password_hash=password_hash,
                        department="Engineering" if name == "owner" else None)
            session.add(user)
            session.flush()
            ids[name] = user.user_id

        def conversation(name, owner="owner"):
            row = Conversation(user_id=ids[owner], title=name, started_at=stamp)
            session.add(row)
            session.flush()
            ids[name] = row.id
            return row

        current = conversation("current")
        legacy = conversation("legacy")
        other = conversation("other-conversation", "other")

        def message(name, conv, role, content, **fields):
            row = Message(conversation_id=conv.id, message_provider=role, content=content,
                          created_at=stamp + timedelta(seconds=len(ids)), **fields)
            session.add(row)
            session.flush()
            ids[name] = row.message_id
            return row

        question = message("vpn-question", current, "user", "How do I connect to VPN?")
        answer = message("vpn-answer", current, "assistant", "VPN requires MFA. [S1]",
                         reply_to_message_id=question.message_id, insufficient_context=False,
                         model_name="scripted-fixture", response_time_ms=15, top_k_used=3)
        payroll = message("payroll-question", current, "user", "How do I see my payroll?")
        message("payroll-answer", current, "assistant", "Open the payroll portal.",
                reply_to_message_id=payroll.message_id, insufficient_context=False)
        refusal = message("refusal-question", current, "user", "What is the moon policy?")
        message("refusal-answer", current, "assistant", "Insufficient evidence.",
                reply_to_message_id=refusal.message_id, insufficient_context=True)
        message("orphan-question", current, "user", "Unanswered new question")
        message("legacy-question", legacy, "user", "Old question without a reliable link")
        message("legacy-answer", legacy, "assistant", "Old retained answer", insufficient_context=None)
        other_question = message("other-question", other, "user", "Another person's question")
        message("other-answer", other, "assistant", "Another person's answer",
                reply_to_message_id=other_question.message_id, insufficient_context=False)
        message("cross-conversation-answer", legacy, "assistant", "Bad cross-conversation link",
                reply_to_message_id=other_question.message_id, insufficient_context=False)
        message("wrong-role-answer", legacy, "assistant", "Bad role link",
                reply_to_message_id=ids["legacy-answer"], insufficient_context=False)

        category = session.query(DocumentCategory).filter_by(category_name="IT Support").one()
        document = Document(doc_name="Historical VPN policy", doc_type="txt", category_id=category.category_id,
                            doc_path="not-read-by-admin-dashboard.txt", status="deactivated", is_online=False,
                            version=2, extracted_text="VPN requires MFA.")
        session.add(document)
        session.flush()
        chunk = DocumentChunk(document_id=document.doc_id, chunk_text="VPN requires MFA.", chunk_index=0,
                              token_count=5, page_number=3, section_heading="Remote access",
                              start_char=0, end_char=17, is_active=False)
        session.add(chunk)
        session.flush()
        ids["document"], ids["chunk"] = document.doc_id, chunk.chunk_id
        citation = {"source_id": "S1", "doc_id": str(document.doc_id), "chunk_id": str(chunk.chunk_id),
                    "doc_name": document.doc_name, "version": 2, "quote": "VPN requires MFA.",
                    "start_char": 0, "end_char": 17, "page_number": 3, "source_hash": "a" * 64}
        answer.generation_metadata = {"citations": [{**citation, "api_key": "hidden-citation-secret"}],
                                      "provider": "explicit-test-only", "api_key": "hidden-top-level-secret",
                                      "config": {"password_hash": "hidden-nested-secret"},
                                      "grounding": {"validated_quotes": 1, "sessiontoken": "hidden-grounding-secret"},
                                      "model_called": True}
        session.add(RetrievedContext(message_id=answer.message_id, chunk_id=chunk.chunk_id,
                                     rank=1, similarity_score=0.81))
        feedback_time = stamp.replace(tzinfo=UTC) + timedelta(hours=1)
        session.add(Feedback(message_id=answer.message_id, user_id=ids["owner"], rating="down",
                             comment="Historical comment must not be returned",
                             created_at=feedback_time, updated_at=feedback_time + timedelta(minutes=5)))
        session.add(Feedback(message_id=ids["payroll-answer"], user_id=ids["owner"], rating="up"))
        # A historical non-owner vote must not impersonate the owner's rating.
        session.add(Feedback(message_id=answer.message_id, user_id=ids["other"], rating="up"))
        session.commit()
    app = create_app(session_factory=db_factory, upload_root=tmp_path / "uploads")
    app.state.resources = ForbiddenResources()
    with TestClient(app) as client:
        client.headers.update({"X-Requested-With": "helpdesk-ui"})
        fixture = SimpleNamespace(client=client, factory=db_factory, ids=ids, emails=emails, app=app,
                                  citation=citation, password_hash=password_hash)
        yield fixture


def login(dashboard, name="admin"):
    dashboard.client.cookies.clear()
    response = dashboard.client.post("/api/auth/login", json={
        "email": dashboard.emails[name], "password": PASSWORD})
    assert response.status_code == 200, response.text


def review_list(dashboard, **params):
    response = dashboard.client.get("/api/admin/reviews", params=params)
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.parametrize("role,code", [(None, 401), ("owner", 403)])
def test_admin_routes_require_real_admin(dashboard, role, code):
    if role:
        login(dashboard, role)
    for url in ("/api/admin/users", f"/api/admin/conversations?user_id={dashboard.ids['owner']}",
                "/api/admin/reviews", f"/api/admin/reviews/{dashboard.ids['vpn-answer']}"):
        assert dashboard.client.get(url).status_code == code


def test_users_show_counts_safe_fields_search_and_stable_pages(dashboard):
    login(dashboard)
    first = dashboard.client.get("/api/admin/users?limit=2").json()
    second = dashboard.client.get("/api/admin/users?limit=2&offset=2").json()
    assert first["total"] == second["total"] == 4
    all_users = first["users"] + second["users"]
    assert len({row["user_id"] for row in all_users}) == 4
    owner = next(row for row in all_users if row["user_id"] == str(dashboard.ids["owner"]))
    assert owner["conversation_count"] == 2 and owner["negative_count"] == 1
    assert owner["last_activity_at"].endswith("+00:00")
    assert set(owner) == {"user_id", "email", "role", "department", "conversation_count",
                          "negative_count", "last_activity_at"}
    search = dashboard.client.get("/api/admin/users?search=ENGINEERING").json()
    assert [row["user_id"] for row in search["users"]] == [str(dashboard.ids["owner"])]
    empty = next(row for row in all_users if row["user_id"] == str(dashboard.ids["empty"]))
    assert empty["conversation_count"] == 0 and empty["last_activity_at"] is None


def test_conversations_are_user_scoped_paginated_and_empty_is_valid(dashboard):
    login(dashboard)
    params = {"user_id": str(dashboard.ids["owner"]), "limit": 1}
    first = dashboard.client.get("/api/admin/conversations", params=params).json()
    second = dashboard.client.get("/api/admin/conversations", params={**params, "offset": 1}).json()
    assert first["total"] == second["total"] == 2
    assert first["conversations"][0]["conversation_id"] != second["conversations"][0]["conversation_id"]
    assert all(row["user_id"] == str(dashboard.ids["owner"]) for row in first["conversations"])
    empty = dashboard.client.get("/api/admin/conversations", params={"user_id": str(dashboard.ids["empty"])}).json()
    assert empty["total"] == 0 and empty["conversations"] == []


def test_exact_question_answer_owner_vote_citations_and_context(dashboard):
    login(dashboard)
    response = dashboard.client.get(f"/api/admin/reviews/{dashboard.ids['vpn-answer']}")
    assert response.status_code == 200, response.text
    detail = response.json()
    row = detail["review"]
    assert row["question"]["message_id"] == str(dashboard.ids["vpn-question"])
    assert row["answer"]["content"] == "VPN requires MFA. [S1]"
    assert row["linkage"] == "explicit" and row["status"] == "answered"
    assert row["feedback"]["rating"] == "down"
    assert row["feedback"]["created_at"] == "2026-10-23T11:00:00+00:00"
    assert row["feedback"]["updated_at"] == "2026-10-23T11:05:00+00:00"
    assert row["citations"] == [dashboard.citation]
    assert row["categories"] == ["IT Support"]
    context = detail["context"][0]
    assert context["chunk_id"] == str(dashboard.ids["chunk"])
    assert context["text"] == "VPN requires MFA." and context["similarity_score"] == pytest.approx(0.81)
    assert context["rank"] == 1 and context["version"] == 2 and context["page_number"] == 3
    assert context["status"] == "deactivated" and context["is_online"] is False
    for secret in ("password_hash", "token_hash", "doc_path", "comment", dashboard.password_hash,
                   "api_key", "sessiontoken", "hidden-citation-secret", "hidden-top-level-secret",
                   "hidden-nested-secret", "hidden-grounding-secret"):
        assert secret not in response.text
    listed = review_list(dashboard, feedback="down")
    assert listed["total"] == 1 and listed["reviews"][0] == row
    assert "context" not in listed and "chunk_text" not in listed["reviews"][0]


def test_filters_match_same_turn_not_other_turn_in_conversation(dashboard):
    login(dashboard)
    assert review_list(dashboard, feedback="down", search="payroll")["total"] == 0
    assert review_list(dashboard, feedback="up", search="VPN")["total"] == 0
    assert review_list(dashboard, feedback="down", status="unanswered")["total"] == 0
    result = review_list(dashboard, feedback="up", search="PAYROLL")
    assert result["total"] == 1 and result["reviews"][0]["review_id"] == str(dashboard.ids["payroll-answer"])
    assert result["reviews"][0]["feedback"]["created_at"] is None
    assert result["reviews"][0]["feedback"]["updated_at"] is None
    # Search is a literal substring, not an SQL LIKE wildcard.
    assert review_list(dashboard, search="%")["total"] == 0


def test_refusal_and_unanswered_question_are_distinct_records(dashboard):
    login(dashboard)
    result = review_list(dashboard, status="unanswered", conversation_id=str(dashboard.ids["current"]))
    assert {row["review_id"] for row in result["reviews"]} == {
        str(dashboard.ids["refusal-answer"]), str(dashboard.ids["orphan-question"])}
    orphan = next(row for row in result["reviews"] if row["answer"] is None)
    assert orphan["question"]["content"] == "Unanswered new question"
    assert orphan["linkage"] == "question_without_response"
    detail = dashboard.client.get(f"/api/admin/reviews/{dashboard.ids['orphan-question']}").json()
    assert detail["context"] == [] and detail["review"]["answer"] is None


def test_legacy_ambiguous_and_malformed_links_never_invent_pairs(dashboard):
    login(dashboard)
    result = review_list(dashboard, conversation_id=str(dashboard.ids["legacy"]))
    assert result["total"] == 4
    for row in result["reviews"]:
        if row["answer"]:
            assert row["question"] is None and row["linkage"] == "legacy_unlinked"
        else:
            assert row["question"]["message_id"] == str(dashboard.ids["legacy-question"])
            assert row["status"] == "unknown"
    unknown = review_list(dashboard, status="unknown", conversation_id=str(dashboard.ids["legacy"]))
    assert {row["review_id"] for row in unknown["reviews"]} == {
        str(dashboard.ids["legacy-question"]), str(dashboard.ids["legacy-answer"])}
    assert dashboard.client.get(f"/api/admin/reviews/{dashboard.ids['vpn-question']}").status_code == 404


def test_review_pagination_user_and_conversation_filters(dashboard):
    login(dashboard)
    all_rows = review_list(dashboard, limit=100)
    assert all_rows["total"] == 9
    seen = []
    for offset in range(0, 9, 2):
        page = review_list(dashboard, limit=2, offset=offset)
        assert page["total"] == 9
        seen.extend(row["review_id"] for row in page["reviews"])
    assert seen == [row["review_id"] for row in all_rows["reviews"]]
    assert len(set(seen)) == 9
    other = review_list(dashboard, user_id=str(dashboard.ids["other"]))
    assert other["total"] == 1 and other["reviews"][0]["user"]["user_id"] == str(dashboard.ids["other"])
    assert review_list(dashboard, user_id=str(dashboard.ids["other"]),
                       conversation_id=str(dashboard.ids["current"]))["total"] == 0
    assert review_list(dashboard, offset=99)["reviews"] == []
    unrated = review_list(dashboard, feedback="unrated")
    assert all(row["feedback"] is None or row["feedback"]["rating"] is None for row in unrated["reviews"])


@pytest.mark.parametrize("endpoint", [
    "/api/admin/users?limit=0", "/api/admin/users?limit=101", "/api/admin/users?offset=-1",
    "/api/admin/conversations", "/api/admin/conversations?user_id=bad",
    "/api/admin/reviews?limit=101", "/api/admin/reviews?feedback=comment",
    "/api/admin/reviews?status=broken", "/api/admin/reviews?user_id=bad",
    "/api/admin/reviews/not-a-uuid", "/api/admin/reviews?search=" + "a" * 201,
])
def test_invalid_queries_are_bounded(endpoint, dashboard):
    login(dashboard)
    assert dashboard.client.get(endpoint).status_code == 422


def test_missing_entities_return_404(dashboard):
    login(dashboard)
    unknown = str(uuid.uuid4())
    for url in (f"/api/admin/reviews/{unknown}", f"/api/admin/reviews?user_id={unknown}",
                f"/api/admin/reviews?conversation_id={unknown}",
                f"/api/admin/conversations?user_id={unknown}"):
        assert dashboard.client.get(url).status_code == 404


def test_admin_dashboard_does_not_relax_owner_history_routes(dashboard):
    login(dashboard)
    assert dashboard.client.get(f"/api/conversations/{dashboard.ids['current']}").status_code == 403
    login(dashboard, "owner")
    assert dashboard.client.get(f"/api/conversations/{dashboard.ids['current']}").status_code == 200
    assert dashboard.client.get(f"/api/conversations/{dashboard.ids['other-conversation']}").status_code == 403


def test_reading_dashboard_preserves_all_review_data(dashboard):
    from sqlalchemy import func, select
    from backend.database.models import Conversation, Feedback, Message, RetrievedContext

    def snapshot():
        with dashboard.factory() as session:
            return tuple(session.scalar(select(func.count()).select_from(model))
                         for model in (Conversation, Message, Feedback, RetrievedContext))

    login(dashboard)
    before = snapshot()
    dashboard.client.get("/api/admin/users")
    dashboard.client.get("/api/admin/conversations", params={"user_id": str(dashboard.ids["owner"])})
    review_list(dashboard)
    dashboard.client.get(f"/api/admin/reviews/{dashboard.ids['vpn-answer']}")
    assert snapshot() == before
