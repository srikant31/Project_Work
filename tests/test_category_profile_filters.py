"""Admin taxonomy, current profile and historical document filters in isolated PG."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
import uuid

import pytest


pytestmark = pytest.mark.integration


@pytest.fixture
def controls(db_factory, tmp_path):
    from fastapi.testclient import TestClient
    from backend.database.models import AuthSession, User
    from backend.main import create_app
    from backend.utils.security import hash_session_token, new_session_token

    users, tokens = {}, {}
    with db_factory() as session:
        for role in ("admin", "employee"):
            row = User(email=f"{role}.controls@example.test", role=role, department="Engineering",
                       password_hash="not-returned-or-used")
            session.add(row)
            session.flush()
            tokens[role] = new_session_token()
            session.add(AuthSession(user_id=row.user_id, token_hash=hash_session_token(tokens[role]),
                                    expires_at=datetime.now(UTC).replace(tzinfo=None) + timedelta(hours=7)))
            users[role] = row
        session.commit()
    app = create_app(session_factory=db_factory, upload_root=tmp_path)
    def forbidden():
        pytest.fail("Read-only controls must not initialize AI resources")

    app.state.resources.get_embedder = forbidden
    app.state.resources.get_retriever = forbidden
    app.state.resources.get_llm = forbidden
    with TestClient(app) as client:
        client.headers.update({"X-Requested-With": "helpdesk-ui"})
        yield SimpleNamespace(client=client, app=app, factory=db_factory, users=users, tokens=tokens)


def sign_in(controls, role="admin"):
    controls.client.cookies.clear()
    controls.client.cookies.set("helpdesk_session", controls.tokens[role])


def create_category(controls, name="Facilities", description="Room access"):
    return controls.client.post("/api/categories", json={"category_name": name, "description": description})


def add_document(session, category_id, *, version=1, status="active", created=None, source="source-a"):
    from backend.database.models import Document
    row = Document(doc_name="Policy", doc_type="txt", category_id=category_id,
                   doc_path="unused.txt", version=version, status=status, source_key=source,
                   created_at=created or datetime(2026, 10, 23), is_online=True)
    session.add(row)
    session.flush()
    return row


def test_controls_auth_and_admin_permissions(controls):
    identifier = uuid.uuid4()
    endpoints = [("get", "/api/categories", None), ("post", "/api/categories", {"category_name": "Test"}),
                 ("patch", f"/api/categories/{identifier}", {"description": None}),
                 ("delete", f"/api/categories/{identifier}", None)]
    for method, url, payload in endpoints:
        assert controls.client.request(method, url, json=payload).status_code == 401
    assert controls.client.get("/api/profile").status_code == 401
    sign_in(controls, "employee")
    for method, url, payload in endpoints:
        assert controls.client.request(method, url, json=payload).status_code == 403
    assert controls.client.get("/api/profile").status_code == 200


def test_category_create_rename_clear_description_delete_and_atomic_audit(controls):
    from backend.database.models import AuditLog, DocumentCategory
    sign_in(controls)
    response = create_category(controls, "  Facilities  ")
    assert response.status_code == 201, response.text
    category = response.json()["category"]
    assert category["category_name"] == "Facilities" and category["document_count"] == 0
    identifier = category["category_id"]
    renamed = controls.client.patch(f"/api/categories/{identifier}",
                                    json={"category_name": "Site facilities", "description": None})
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["category"] == {**category, "category_name": "Site facilities", "description": None}
    choices = controls.client.get("/api/documents/categories")
    assert choices.status_code == 200
    assert {"category_id": identifier, "category_name": "Site facilities"} in choices.json()["categories"]
    assert controls.client.delete(f"/api/categories/{identifier}").status_code == 204
    with controls.factory() as session:
        assert session.get(DocumentCategory, uuid.UUID(identifier)) is None
        logs = session.query(AuditLog).filter(AuditLog.entity_id == uuid.UUID(identifier)).all()
        assert {log.action for log in logs} == {"category.created", "category.updated", "category.deleted"}
        assert all(log.user_id == controls.users["admin"].user_id for log in logs)


@pytest.mark.parametrize("body", [
    {"category_name": ""}, {"category_name": "   "}, {"category_name": "A\nB"},
    {"category_name": "x" * 101}, {"category_name": None},
    {"category_name": "Test", "description": "x" * 1001},
    {"category_name": "Test", "role": "admin"},
])
def test_invalid_category_inputs_are_422_without_writes(controls, body):
    from backend.database.models import AuditLog, DocumentCategory
    sign_in(controls)
    assert controls.client.post("/api/categories", json=body).status_code == 422
    with controls.factory() as session:
        assert session.query(DocumentCategory).count() == 2
        assert session.query(AuditLog).count() == 0


def test_category_patch_requires_valid_nonempty_changes_and_unknown_404(controls):
    sign_in(controls)
    identifier = uuid.uuid4()
    for body in ({}, {"category_name": None}, {"category_name": ""}, {"extra": True}):
        assert controls.client.patch(f"/api/categories/{identifier}", json=body).status_code == 422
    assert controls.client.patch(f"/api/categories/{identifier}", json={"description": None}).status_code == 404
    assert controls.client.delete(f"/api/categories/{identifier}").status_code == 404


def test_category_duplicate_case_insensitive_and_historical_references_block_delete(controls):
    from backend.database.models import AuditLog
    sign_in(controls)
    category = create_category(controls).json()["category"]
    identifier = uuid.UUID(category["category_id"])
    assert create_category(controls, "fACILITIES").status_code == 409
    assert controls.client.patch(f"/api/categories/{identifier}", json={"category_name": "it support"}).status_code == 409
    with controls.factory() as session:
        add_document(session, identifier, status="deactivated", version=1)
        add_document(session, identifier, status="failed", version=2)
        session.commit()
    listing = controls.client.get("/api/categories").json()["categories"]
    row = next(row for row in listing if row["category_id"] == str(identifier))
    assert row["document_count"] == 2
    assert controls.client.delete(f"/api/categories/{identifier}").status_code == 409
    renamed = controls.client.patch(f"/api/categories/{identifier}", json={"category_name": "Renamed"})
    assert renamed.status_code == 200 and renamed.json()["category"]["document_count"] == 2
    with controls.factory() as session:
        assert session.query(AuditLog).filter(AuditLog.entity_id == identifier).count() == 2


def test_category_writes_serialize_duplicate_concurrency(controls):
    from fastapi import HTTPException
    from backend.models.category_models import CategoryCreate
    from backend.services.category_service import CategoryService

    def attempt(name):
        try:
            CategoryService(controls.factory).create(CategoryCreate(category_name=name), controls.users["admin"])
            return 201
        except HTTPException as exc:
            return exc.status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, ["Concurrent category", "CONCURRENT CATEGORY"]))
    assert sorted(results) == [201, 409]


def test_category_audit_failure_rolls_back_mutation(controls, monkeypatch):
    from backend.database.models import DocumentCategory
    from backend.models.category_models import CategoryCreate
    from backend.services.audit_service import AuditService
    from backend.services.category_service import CategoryService

    def fail(*args, **kwargs):
        raise RuntimeError("Deliberate isolated audit failure")

    monkeypatch.setattr(AuditService, "record", fail)
    with pytest.raises(RuntimeError, match="Deliberate"):
        CategoryService(controls.factory).create(CategoryCreate(category_name="Must rollback"), controls.users["admin"])
    with controls.factory() as session:
        assert session.query(DocumentCategory).filter_by(category_name="Must rollback").first() is None


@pytest.mark.parametrize("operation", ["update", "delete"])
def test_category_existing_mutation_rolls_back_if_audit_fails(controls, monkeypatch, operation):
    from backend.database.models import AuditLog, DocumentCategory
    from backend.models.category_models import CategoryUpdate
    from backend.services.audit_service import AuditService
    from backend.services.category_service import CategoryService
    sign_in(controls)
    identifier = uuid.UUID(create_category(controls).json()["category"]["category_id"])

    def fail(*args, **kwargs):
        raise RuntimeError("Deliberate isolated audit failure")

    monkeypatch.setattr(AuditService, "record", fail)
    service = CategoryService(controls.factory)
    with pytest.raises(RuntimeError, match="Deliberate"):
        if operation == "update":
            service.update(identifier, CategoryUpdate(category_name="Should rollback"), controls.users["admin"])
        else:
            service.delete(identifier, controls.users["admin"])
    with controls.factory() as session:
        assert session.get(DocumentCategory, identifier).category_name == "Facilities"
        assert session.query(AuditLog).filter_by(entity_id=identifier).count() == 1


def test_category_fk_conflict_is_sanitized_and_delete_audit_rolls_back(controls, monkeypatch):
    from backend.database.models import AuditLog, DocumentCategory
    from backend.repositories.category_management_repository import CategoryManagementRepository
    sign_in(controls)
    identifier = uuid.UUID(create_category(controls).json()["category"]["category_id"])
    with controls.factory() as session:
        add_document(session, identifier, status="deactivated")
        session.commit()
    # Simulate the application-level check missing a concurrent reference. The
    # actual PG FK still rejects deletion, and the service must sanitize it.
    monkeypatch.setattr(CategoryManagementRepository, "count_documents", lambda *args: 0)
    response = controls.client.delete(f"/api/categories/{identifier}")
    assert response.status_code == 409
    assert "referenced by document versions" in response.json()["detail"]
    assert not any(word in response.text for word in ("psycopg", "DELETE FROM", "IntegrityError"))
    with controls.factory() as session:
        assert session.get(DocumentCategory, identifier) is not None
        assert session.query(AuditLog).filter_by(entity_id=identifier).count() == 1


def test_document_filters_return_stored_versions_with_utc_inclusive_dates(controls):
    from backend.database.models import DocumentCategory
    sign_in(controls)
    with controls.factory() as session:
        category = session.query(DocumentCategory).first()
        first = add_document(session, category.category_id, status="deactivated", version=1,
                             created=datetime(2026, 10, 20, 0, 0, 0))
        second = add_document(session, category.category_id, version=2,
                              created=datetime(2026, 10, 21, 23, 59, 59, 999999))
        failed = add_document(session, category.category_id, status="failed", version=3,
                              created=datetime(2026, 10, 22, 0, 0, 0))
        ids = [str(row.doc_id) for row in (first, second, failed)]
        session.commit()
    latest = controls.client.get("/api/documents").json()
    assert latest["scope"] == "latest_sources"
    assert [row["doc_id"] for row in latest["documents"]] == [ids[1]]
    historical = controls.client.get("/api/documents", params={"version": 1}).json()
    assert historical["scope"] == "matching_versions"
    assert [row["doc_id"] for row in historical["documents"]] == [ids[0]]
    assert historical["summary"]["documents"] == 1
    dated = controls.client.get("/api/documents", params={"uploaded_from": "2026-10-20", "uploaded_to": "2026-10-21"}).json()
    assert {row["doc_id"] for row in dated["documents"]} == set(ids[:2])
    single_day = controls.client.get("/api/documents", params={"uploaded_from": "2026-10-21", "uploaded_to": "2026-10-21"}).json()
    assert [row["doc_id"] for row in single_day["documents"]] == [ids[1]]
    combined = controls.client.get("/api/documents", params={"version": 1, "uploaded_from": "2026-10-21"}).json()
    assert combined["documents"] == [] and combined["summary"]["documents"] == 0


@pytest.mark.parametrize("params", [
    {"version": 0}, {"version": -1}, {"version": "abc"}, {"version": "1.5"},
    {"uploaded_from": "invalid"}, {"uploaded_to": "2026-02-30"},
    {"uploaded_from": "20261023"}, {"uploaded_to": "1792713600"},
    {"uploaded_from": "2026-10-22", "uploaded_to": "2026-10-20"}, {"uploaded_to": "9999-12-31"},
])
def test_document_invalid_filters_are_422(controls, params):
    sign_in(controls)
    assert controls.client.get("/api/documents", params=params).status_code == 422


def test_profile_is_own_safe_readonly_activity_and_current_session(controls):
    from backend.database.models import AuthSession, Conversation, Feedback, Message
    from backend.utils.security import hash_session_token, new_session_token
    with controls.factory() as session:
        for role, count in (("admin", 1), ("employee", 2)):
            actor = controls.users[role]
            for number in range(count):
                conversation = Conversation(user_id=actor.user_id, title=f"{role} private")
                session.add(conversation)
                session.flush()
                question = Message(conversation_id=conversation.id, message_provider="user", content="Question")
                session.add(question)
                session.flush()
                answer = Message(conversation_id=conversation.id, message_provider="assistant", content="Answer",
                                 reply_to_message_id=question.message_id)
                session.add(answer)
                session.flush()
                session.add(Feedback(message_id=answer.message_id, user_id=actor.user_id, rating="down"))
        # Another valid session for this user must not be returned as current.
        session.add(AuthSession(user_id=controls.users["admin"].user_id,
                                token_hash=hash_session_token(new_session_token()),
                                expires_at=datetime.now(UTC).replace(tzinfo=None) + timedelta(hours=1)))
        session.commit()
    for role, count in (("admin", 1), ("employee", 2)):
        sign_in(controls, role)
        response = controls.client.get("/api/profile", params={"user_id": str(controls.users["admin"].user_id)})
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["user"] == {"user_id": str(controls.users[role].user_id), "email": controls.users[role].email,
                                "role": role, "department": "Engineering"}
        assert body["activity"] == {"conversations": count, "questions": count, "responses": count,
                                    "positive_feedback": 0, "negative_feedback": count}
        assert body["session"]["mechanism"] == "opaque_cookie" and body["session"]["duration_hours"] == 8
        expiry = datetime.fromisoformat(body["session"]["expires_at"].replace("Z", "+00:00"))
        assert 6.9 < (expiry - datetime.now(UTC)).total_seconds() / 3600 < 7.1
        assert not any(secret in response.text for secret in ("password_hash", "token_hash", controls.tokens[role]))
        assert controls.client.patch("/api/profile", json={"role": "admin"}).status_code == 405
    with controls.factory() as session:
        assert session.query(AuthSession).count() == 3
