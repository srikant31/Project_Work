"""Authenticated HTTP integration in disposable PostgreSQL/Chroma/upload stores.

Run explicitly with --run-integration. Application imports are deferred so the
ordinary unit suite never initializes a web app or connects to live stores.
"""

import json
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest


pytestmark = pytest.mark.integration
PROJECT_ROOT = Path(__file__).resolve().parents[1]
PASSWORDS = {"admin": "Admin-validation-2026!", "employee": "Employee-validation-2026!"}
EMAILS = {"admin": "admin.validation@example.test", "employee": "employee.validation@example.test"}
MAX_UPLOAD_BYTES = 65_536


@pytest.fixture
def console(db_factory, real_embedder, real_retriever, tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from backend.database.models import DocumentCategory, User
    from backend.main import create_app
    from backend.utils.security import hash_password

    monkeypatch.setenv("AUTH_COOKIE_SECURE", "false")
    monkeypatch.setenv("FRONTEND_ORIGINS", "http://localhost:5173")
    with db_factory() as session:
        users = {}
        for role in ("admin", "employee"):
            user = User(email=EMAILS[role], role=role, password_hash=hash_password(PASSWORDS[role]))
            session.add(user)
            session.flush()
            users[role] = str(user.user_id)
        category = session.query(DocumentCategory).filter_by(category_name="IT Support").one()
        category_id = str(category.category_id)
        session.commit()
    upload_root = tmp_path / "managed_uploads"
    app = create_app(session_factory=db_factory, embedder=real_embedder, retriever=real_retriever,
                     upload_root=upload_root, max_upload_bytes=MAX_UPLOAD_BYTES)
    with TestClient(app) as client:
        client.headers.update({"X-Requested-With": "helpdesk-ui"})
        yield SimpleNamespace(client=client, factory=db_factory, embedder=real_embedder,
                              retriever=real_retriever, upload_root=upload_root,
                              category_id=category_id, users=users, app=app)


def login(console, role="admin"):
    console.client.cookies.clear()
    response = console.client.post("/api/auth/login", json={"email": EMAILS[role], "password": PASSWORDS[role]})
    assert response.status_code == 200, response.text
    assert response.json()["user"]["role"] == role
    return response


def upload(console, *, filename="policy.txt", content=b"VPN access requires a company laptop and MFA.", online=True, name=None):
    data = {"category_id": console.category_id, "is_online": str(online).lower()}
    if name is not None:
        data["doc_name"] = name
    return console.client.post("/api/documents/upload", data=data,
                               files={"file": (filename, content, "application/octet-stream")})


def uploaded(console, **kwargs):
    response = upload(console, **kwargs)
    assert response.status_code in {200, 201}, response.text
    return response.json()["document"]


def snapshot(console):
    from backend.database.models import Document, DocumentChunk, IngestionJob

    with console.factory() as session:
        counts = (session.query(Document).count(), session.query(DocumentChunk).count(),
                  session.query(IngestionJob).count(), console.retriever.count())
    files = {str(path.resolve()): path.read_bytes() for path in console.upload_root.rglob("*") if path.is_file()}
    return counts, files


def search(console, question="How do I get VPN access?"):
    response = console.client.post("/api/search", json={"question": question, "top_k": 5})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["retrieval_only"] is True
    assert "elapsed_ms" in body
    return body["hits"]


def test_login_identity_cookie_hash_logout_and_replay_revocation(console):
    from backend.database.models import AuthSession

    for credentials in ({"email": EMAILS["admin"], "password": "incorrect-password"},
                        {"email": "unknown@example.test", "password": PASSWORDS["admin"]}):
        assert console.client.post("/api/auth/login", json=credentials).status_code == 401
    response = login(console)
    assert "httponly" in response.headers.get("set-cookie", "").lower()
    raw_token = console.client.cookies.get("helpdesk_session")
    assert raw_token
    me = console.client.get("/api/auth/me")
    assert me.status_code == 200
    assert me.json()["user"]["user_id"] == console.users["admin"]
    assert "password_hash" not in me.json()["user"]
    with console.factory() as session:
        stored = session.query(AuthSession).one()
        assert stored.token_hash != raw_token
        assert len(stored.token_hash) == 64
    assert console.client.post("/api/auth/logout").status_code == 204
    assert console.client.get("/api/auth/me").status_code == 401
    console.client.cookies.set("helpdesk_session", raw_token)
    assert console.client.get("/api/auth/me").status_code == 401


def test_expired_cookie_cannot_authenticate(console):
    from backend.database.models import AuthSession

    login(console)
    with console.factory() as session:
        stored = session.query(AuthSession).one()
        stored.expires_at = datetime.now(UTC) - timedelta(minutes=1)
        session.commit()
    assert console.client.get("/api/auth/me").status_code == 401
    assert console.client.get("/api/documents").status_code == 401


def test_unauthenticated_requests_cannot_read_mutate_or_search(console):
    identifier = str(uuid.uuid4())
    before = snapshot(console)
    for endpoint in ("/api/auth/me", "/api/documents", "/api/documents/categories",
                     f"/api/documents/{identifier}", "/api/audit-logs"):
        assert console.client.get(endpoint).status_code == 401, endpoint
    assert upload(console).status_code == 401
    assert console.client.patch(f"/api/documents/{identifier}/availability", json={"is_online": False}).status_code == 401
    assert console.client.post(f"/api/documents/{identifier}/replace", files={"file": ("replacement.txt", b"New policy")}).status_code == 401
    assert console.client.post(f"/api/documents/{identifier}/reindex", json={"chunk_size": 200, "overlap": 40}).status_code == 401
    assert console.client.post("/api/search", json={"question": "VPN", "top_k": 3}).status_code == 401
    assert snapshot(console) == before


def test_employee_denied_admin_reads_uploads_and_mutations_before_writes(console):
    login(console)
    document = uploaded(console)
    login(console, "employee")
    before = snapshot(console)
    identifier = document["doc_id"]
    for endpoint in ("/api/documents", "/api/documents/categories", f"/api/documents/{identifier}", "/api/audit-logs"):
        assert console.client.get(endpoint).status_code == 403, endpoint
    assert upload(console).status_code == 403
    assert console.client.patch(f"/api/documents/{identifier}/availability", json={"is_online": False}).status_code == 403
    assert console.client.post(f"/api/documents/{identifier}/replace", files={"file": ("changed.txt", b"Changed policy")}).status_code == 403
    assert console.client.post(f"/api/documents/{identifier}/reindex", json={"chunk_size": 200, "overlap": 40}).status_code == 403
    assert snapshot(console) == before
    assert search(console), "Employee must retain ordinary online retrieval"


def test_role_in_login_body_cannot_escalate_employee(console):
    response = console.client.post("/api/auth/login", json={"email": EMAILS["employee"],
                                   "password": PASSWORDS["employee"], "role": "admin"})
    assert response.status_code in {200, 422}
    if response.status_code == 200:
        assert response.json()["user"]["role"] == "employee"
    else:
        login(console, "employee")
    assert console.client.get("/api/documents").status_code == 403


@pytest.mark.parametrize("filename", ["sample_password_policy.txt", "sample_vpn_guide.md", "sample_equipment_manual.pdf", "sample_network_faq.docx"])
def test_admin_upload_all_formats_preserves_file_provenance_and_detail(console, filename):
    from backend.database.models import Document

    login(console)
    original = (PROJECT_ROOT / "data" / "documents" / filename).read_bytes()
    document = uploaded(console, filename=filename, content=original)
    assert document["doc_type"] == Path(filename).suffix[1:]
    assert document["version"] == 1 and document["is_online"] is True
    listing = console.client.get("/api/documents")
    assert listing.status_code == 200
    assert document["doc_id"] in {d["doc_id"] for d in listing.json()["documents"]}
    detail = console.client.get(f"/api/documents/{document['doc_id']}")
    assert detail.status_code == 200
    assert detail.json()["chunks"] and detail.json()["source_characters"] > 0
    assert detail.json()["versions"] and detail.json()["pipeline"]
    with console.factory() as session:
        row = session.get(Document, uuid.UUID(document["doc_id"]))
        stored = Path(row.doc_path).resolve()
        assert stored.is_relative_to(console.upload_root.resolve())
        assert stored.read_bytes() == original
        assert row.doc_size == len(original)
        assert str(row.uploaded_by) == console.users["admin"]
        assert len(row.source_hash) == 64
        assert row.page_count is None if row.doc_type != "pdf" else row.page_count > 0
    login(console, "employee")
    assert {hit["doc_id"] for hit in search(console, "What does the policy require?")} == {document["doc_id"]}


def test_offline_upload_and_live_toggles_control_both_roles_search(console):
    login(console)
    document = uploaded(console, online=False)
    identifier = document["doc_id"]
    assert document["is_online"] is False
    assert search(console) == []
    login(console, "employee")
    assert search(console) == []
    login(console)
    turned_on = console.client.patch(f"/api/documents/{identifier}/availability", json={"is_online": True})
    assert turned_on.status_code == 200, turned_on.text
    assert turned_on.json()["document"]["is_online"] is True
    assert {h["doc_id"] for h in search(console)} == {identifier}
    login(console, "employee")
    assert {h["doc_id"] for h in search(console)} == {identifier}
    login(console)
    turned_off = console.client.patch(f"/api/documents/{identifier}/availability", json={"is_online": False})
    assert turned_off.status_code == 200
    assert search(console) == []
    login(console, "employee")
    assert search(console) == []


def test_replace_reindex_and_stale_version_guards_preserve_immutable_files(console):
    from backend.database.models import Document

    login(console)
    original_bytes = b"VPN access requires a company laptop and MFA."
    original = uploaded(console, content=original_bytes)
    with console.factory() as session:
        original_path = Path(session.get(Document, uuid.UUID(original["doc_id"])).doc_path)
    changed = b"VPN access now requires a hardware security key and a managed company laptop."
    response = console.client.post(f"/api/documents/{original['doc_id']}/replace",
                                    files={"file": ("replacement.txt", changed, "text/plain")})
    assert response.status_code in {200, 201}, response.text
    replacement = response.json()["document"]
    assert replacement["version"] == 2 and replacement["doc_id"] != original["doc_id"]
    assert replacement["is_online"] is True
    assert original_path.read_bytes() == original_bytes
    with console.factory() as session:
        old = session.get(Document, uuid.UUID(original["doc_id"]))
        new = session.get(Document, uuid.UUID(replacement["doc_id"]))
        assert old.status == "deactivated" and new.status == "active"
        assert old.source_key == new.source_key
        assert Path(new.doc_path).read_bytes() == changed
        assert new.doc_path != old.doc_path
    stale = console.client.patch(f"/api/documents/{original['doc_id']}/availability", json={"is_online": False})
    assert stale.status_code == 409
    reindexed = console.client.post(f"/api/documents/{replacement['doc_id']}/reindex", json={"chunk_size": 200, "overlap": 40})
    assert reindexed.status_code in {200, 201}, reindexed.text
    latest = reindexed.json()["document"]
    assert latest["version"] == 3 and latest["doc_id"] != replacement["doc_id"]
    assert original_path.read_bytes() == original_bytes
    login(console, "employee")
    assert {h["doc_id"] for h in search(console)} == {latest["doc_id"]}


def test_invalid_replacement_keeps_old_online_version_and_history(console):
    login(console)
    original = uploaded(console)
    before = snapshot(console)
    response = console.client.post(f"/api/documents/{original['doc_id']}/replace",
                                    files={"file": ("corrupt.pdf", b"not a PDF", "application/pdf")})
    assert response.status_code == 400, response.text
    assert snapshot(console) == before
    login(console, "employee")
    assert {h["doc_id"] for h in search(console)} == {original["doc_id"]}


def test_reindex_and_replacement_inherit_offline_state(console):
    login(console)
    original = uploaded(console, online=False)
    reindexed = console.client.post(f"/api/documents/{original['doc_id']}/reindex", json={"chunk_size": 200, "overlap": 40})
    assert reindexed.status_code in {200, 201}, reindexed.text
    second = reindexed.json()["document"]
    assert second["version"] == 2 and second["is_online"] is False
    replacement = console.client.post(f"/api/documents/{second['doc_id']}/replace",
                                       files={"file": ("changed.txt", b"VPN access now requires a hardware security key.", "text/plain")})
    assert replacement.status_code in {200, 201}, replacement.text
    third = replacement.json()["document"]
    assert third["version"] == 3 and third["is_online"] is False
    assert search(console) == []
    login(console, "employee")
    assert search(console) == []


@pytest.mark.parametrize("filename,content,status", [("bad.exe", b"unsupported content", 400),
                       ("empty.txt", b"  \n ", 400), ("huge.txt", b"x" * (MAX_UPLOAD_BYTES + 1), 413)],
                       ids=["unsupported", "empty", "oversize"])
def test_rejected_uploads_leave_no_documents_vectors_or_original_files(console, filename, content, status):
    login(console)
    before = snapshot(console)
    response = upload(console, filename=filename, content=content)
    assert response.status_code == status, response.text
    assert snapshot(console) == before


@pytest.mark.parametrize("filename", ["../../escaped.txt", "..\\..\\escaped.txt"])
def test_upload_filename_traversal_cannot_escape_managed_storage(console, filename):
    from backend.database.models import Document

    login(console)
    document = uploaded(console, filename=filename)
    with console.factory() as session:
        row = session.get(Document, uuid.UUID(document["doc_id"]))
        assert Path(row.doc_path).resolve().is_relative_to(console.upload_root.resolve())
        assert Path(row.doc_path).is_file()
    assert not (console.upload_root.parent / "escaped.txt").exists()


def test_mutations_record_actor_and_document_audit_events_without_credentials(console):
    from backend.database.models import AuditLog

    login(console)
    document = uploaded(console)
    identifier = document["doc_id"]
    assert console.client.patch(f"/api/documents/{identifier}/availability", json={"is_online": False}).status_code == 200
    replacement = console.client.post(f"/api/documents/{identifier}/replace",
                                       files={"file": ("changed.txt", b"Updated VPN policy requires a security key.", "text/plain")})
    assert replacement.status_code in {200, 201}, replacement.text
    replacement_id = replacement.json()["document"]["doc_id"]
    reindexed = console.client.post(f"/api/documents/{replacement_id}/reindex", json={"chunk_size": 200, "overlap": 40})
    assert reindexed.status_code in {200, 201}, reindexed.text
    events = console.client.get("/api/audit-logs")
    assert events.status_code == 200 and len(events.json()["events"]) >= 4
    with console.factory() as session:
        rows = session.query(AuditLog).all()
        assert len(rows) >= 4
        assert all(str(row.user_id) == console.users["admin"] for row in rows)
        assert any(str(row.entity_id) == identifier for row in rows)
        assert len({row.action for row in rows}) >= 4
    serialized = json.dumps(events.json())
    assert PASSWORDS["admin"] not in serialized
    assert console.client.cookies.get("helpdesk_session") not in serialized


def test_source_download_respects_online_state_and_replaced_versions(console):
    login(console)
    original = b"VPN access requires a managed laptop and MFA."
    document = uploaded(console, content=original)
    endpoint = f"/api/documents/{document['doc_id']}/file"
    assert console.client.get(endpoint).content == original
    login(console, "employee")
    downloaded = console.client.get(endpoint)
    assert downloaded.status_code == 200 and downloaded.content == original
    login(console)
    assert console.client.patch(f"/api/documents/{document['doc_id']}/availability", json={"is_online": False}).status_code == 200
    login(console, "employee")
    assert console.client.get(endpoint).status_code == 403
    login(console)
    assert console.client.get(endpoint).status_code == 200
    assert console.client.patch(f"/api/documents/{document['doc_id']}/availability", json={"is_online": True}).status_code == 200
    replaced = console.client.post(f"/api/documents/{document['doc_id']}/replace",
                                    files={"file": ("changed.txt", b"Updated VPN policy.", "text/plain")})
    assert replaced.status_code == 200, replaced.text
    login(console, "employee")
    assert console.client.get(endpoint).status_code == 403


def test_browser_mutations_require_trusted_origin_and_verification_header(console):
    login(console)
    document = uploaded(console)
    endpoint = f"/api/documents/{document['doc_id']}/availability"
    before = snapshot(console)
    hostile = console.client.patch(endpoint, json={"is_online": False}, headers={"Origin": "https://untrusted.example"})
    assert hostile.status_code == 403
    del console.client.headers["X-Requested-With"]
    missing_header = console.client.patch(endpoint, json={"is_online": False}, headers={"Origin": "http://localhost:5173"})
    assert missing_header.status_code == 403
    assert snapshot(console) == before
    permitted = console.client.patch(endpoint, json={"is_online": False},
                  headers={"Origin": "http://localhost:5173", "X-Requested-With": "helpdesk-ui"})
    assert permitted.status_code == 200, permitted.text


def test_admin_migration_upgrade_and_repeat_preserve_credentials_documents_and_sessions(db_factory):
    from sqlalchemy import text
    from backend.database.models import AuditLog, AuthSession, Document, DocumentCategory, DocumentChunk, User
    from backend.services.audit_service import AuditService
    from backend.services.auth_service import AuthService
    from backend.utils.security import hash_password

    password_hash = hash_password(PASSWORDS["admin"])
    with db_factory() as session:
        engine = session.get_bind()
        assert session.scalar(text("SELECT current_schema()")).startswith("codex_ingest_test_")
        user = User(email=EMAILS["admin"], role="admin", password_hash=password_hash)
        session.add(user)
        session.flush()
        category = session.query(DocumentCategory).filter_by(category_name="IT Support").one()
        document = Document(doc_name="Legacy policy", doc_type="txt", category_id=category.category_id,
                            doc_path="legacy/policy.txt", uploaded_by=user.user_id, status="active")
        session.add(document)
        session.flush()
        chunk = DocumentChunk(document_id=document.doc_id, chunk_text="Original legacy evidence", chunk_index=0,
                              embedding_vector_id="legacy_collection:original-vector", is_active=True)
        session.add(chunk)
        session.flush()
        user_id, document_id, chunk_id = user.user_id, document.doc_id, chunk.chunk_id
        session.commit()
    # Remove only migration-004 additions in this generated schema to exercise
    # a real upgrade, rather than merely running CREATE IF NOT EXISTS twice.
    with engine.begin() as connection:
        connection.exec_driver_sql("DROP TABLE authsessions")
        connection.exec_driver_sql("ALTER TABLE documents DROP COLUMN is_online")
        connection.exec_driver_sql("ALTER TABLE auditlogs DROP COLUMN details")
    migration = (PROJECT_ROOT / "backend/database/migrations/004_admin_console.sql").read_text(encoding="utf-8-sig")

    def apply():
        with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
            try:
                connection.exec_driver_sql(migration)
            except Exception:
                connection.exec_driver_sql("ROLLBACK")
                raise

    apply()
    with db_factory() as session:
        document = session.get(Document, document_id)
        assert document.is_online is True
        document.is_online = False
        actor = session.get(User, user_id)
        AuditService(session).record(actor, "migration.smoke", "document", document_id, {"retained": True})
        session.commit()
    with AuthService(session_factory=db_factory) as auth:
        logged_in, raw_token = auth.login(EMAILS["admin"], PASSWORDS["admin"])
        assert logged_in.user_id == user_id
    apply()
    with db_factory() as session:
        user = session.get(User, user_id)
        document = session.get(Document, document_id)
        chunk = session.get(DocumentChunk, chunk_id)
        assert user.password_hash == password_hash and user.role == "admin"
        assert document.status == "active" and document.is_online is False
        assert document.doc_path == "legacy/policy.txt"
        assert chunk.chunk_text == "Original legacy evidence"
        assert chunk.embedding_vector_id == "legacy_collection:original-vector"
        assert session.query(AuditLog).one().details == {"retained": True}
        assert session.query(AuthSession).count() == 1
    with AuthService(session_factory=db_factory) as auth:
        assert auth.current_user(raw_token).user_id == user_id
        assert auth.logout(raw_token) is True


def test_identical_replacement_skips_without_leaking_new_unreferenced_file(console):
    login(console)
    content = b"VPN access requires a company laptop and MFA."
    document = uploaded(console, content=content)
    before = snapshot(console)
    response = console.client.post(f"/api/documents/{document['doc_id']}/replace",
                                    files={"file": ("identical.txt", content, "text/plain")})
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["skipped"] is True
    assert result["document"]["doc_id"] == document["doc_id"]
    assert result["document"]["version"] == document["version"]
    assert snapshot(console) == before


def test_legacy_reindex_preserves_explicit_unknown_provenance_warning(console):
    from backend.database.models import Document, DocumentChunk
    from scripts.verify_stores import audit_stores

    login(console)
    document = uploaded(console)
    identifier = uuid.UUID(document["doc_id"])
    # Keep the original indexed evidence/UUID references, but remove fields
    # historical versions never captured. Reindexing must not fabricate them.
    with console.factory() as session:
        row = session.get(Document, identifier)
        for field in ("source_key", "source_hash", "text_hash", "pipeline_fingerprint", "pipeline_config", "extracted_text", "extraction_warnings"):
            setattr(row, field, None)
        for chunk in session.query(DocumentChunk).filter_by(document_id=identifier):
            chunk.start_char = chunk.end_char = None
        session.commit()
    assert console.client.patch(f"/api/documents/{identifier}/availability", json={"is_online": False}).status_code == 200
    response = console.client.post(f"/api/documents/{identifier}/reindex", json={"chunk_size": 200, "overlap": 40})
    assert response.status_code == 200, response.text
    replacement = response.json()["document"]
    assert replacement["doc_id"] != str(identifier) and replacement["is_online"] is False
    with console.factory() as session:
        old = session.get(Document, identifier)
        assert old.status == "deactivated"
        assert old.source_hash is old.pipeline_fingerprint is old.extracted_text is None
        report = audit_stores(session, console.retriever.collection, embedder=console.embedder)
    assert not [check for check in report["checks"] if check["status"] == "FAIL"], report
    warning = next(check for check in report["checks"] if check["name"] == "Legacy provenance is explicitly unknown")
    assert warning["status"] == "WARN" and str(identifier) in warning["ids"]


def test_reindex_selection_race_cannot_restore_superseded_file(console, monkeypatch):
    from backend.database.models import Document
    from backend.services.document_processing_service import DocumentProcessingService
    from backend.services.document_service import DocumentService

    login(console)
    original = uploaded(console, content=b"The previous VPN policy requires a laptop and password.")
    selected = original["doc_id"]
    identity = DocumentService._current_identity
    injected = {}
    new_text = "The current VPN policy requires a managed laptop and hardware security key."

    def replace_after_selection(service, document_id):
        data = identity(service, document_id)
        if str(document_id) == selected and not injected:
            path = console.upload_root / "concurrent_replacement.txt"
            path.write_text(new_text, encoding="utf-8")
            with DocumentProcessingService(session_factory=console.factory, embedder=console.embedder,
                                           retriever=console.retriever) as processing:
                injected.update(processing.process_file(path, category_id=data["category_id"],
                    source_key=data["source_key"], doc_name=data["doc_name"],
                    uploaded_by=uuid.UUID(console.users["admin"]), expected_current_doc_id=selected))
        return data

    monkeypatch.setattr(DocumentService, "_current_identity", replace_after_selection)
    response = console.client.post(f"/api/documents/{selected}/reindex", json={"chunk_size": 200, "overlap": 40})
    assert response.status_code == 409, response.text
    assert injected["version"] == 2
    with console.factory() as session:
        versions = session.query(Document).order_by(Document.version).all()
        assert len(versions) == 2
        assert [row.status for row in versions] == ["deactivated", "active"]
        assert versions[-1].extracted_text == new_text
    login(console, "employee")
    assert {hit["doc_id"] for hit in search(console)} == {injected["doc_id"]}


def test_audit_failure_rolls_back_replacement_publication_and_preserves_old_version(console, monkeypatch):
    from backend.database.models import AuditLog, Document, IngestionJob
    from backend.services.audit_service import AuditService

    login(console)
    original = uploaded(console)
    record = AuditService.record

    def fail_replacement_audit(service, actor, action, entity_type, entity_id, details=None):
        if action == "document.replace":
            raise RuntimeError("Simulated audit write failure")
        return record(service, actor, action, entity_type, entity_id, details)

    monkeypatch.setattr(AuditService, "record", fail_replacement_audit)
    response = console.client.post(f"/api/documents/{original['doc_id']}/replace",
                                    files={"file": ("changed.txt", b"The revised VPN policy requires a hardware key.", "text/plain")})
    assert response.status_code == 503, response.text
    with console.factory() as session:
        versions = session.query(Document).order_by(Document.version).all()
        assert len(versions) == 2
        assert str(versions[0].doc_id) == original["doc_id"]
        assert [row.status for row in versions] == ["active", "failed"]
        assert versions[0].is_online is True
        assert session.query(IngestionJob).filter_by(document_id=versions[1].doc_id).one().status == "failed"
        assert session.query(AuditLog).filter_by(action="document.replace").count() == 0
    login(console, "employee")
    assert {hit["doc_id"] for hit in search(console)} == {original["doc_id"]}


def test_offline_toggle_during_staging_is_inherited_at_final_publication(console, monkeypatch):
    from backend.database.models import Document, User
    from backend.repositories.document_repository import DocumentRepository
    from backend.services.document_service import DocumentService

    login(console)
    original = uploaded(console)
    with console.factory() as session:
        actor = session.get(User, uuid.UUID(console.users["admin"]))
        session.expunge(actor)
    acquire = DocumentRepository.lock_source
    switched = {"done": False}

    def toggle_before_final_lock(repository, category_id, source_key):
        if source_key == original["source_key"] and not switched["done"]:
            with console.factory() as session:
                staged = session.query(Document).filter_by(source_key=source_key, status="staging").first()
            if staged is not None:
                # Reservation has committed and released its lock; publication
                # has not acquired the next lock yet. No sleeps or timing race.
                switched["done"] = True
                with DocumentService(session_factory=console.factory, embedder=console.embedder,
                                     retriever=console.retriever, storage=console.app.state.storage) as manager:
                    result = manager.set_availability(original["doc_id"], False, actor)
                    assert result["document"]["is_online"] is False
        return acquire(repository, category_id, source_key)

    monkeypatch.setattr(DocumentRepository, "lock_source", toggle_before_final_lock)
    response = console.client.post(f"/api/documents/{original['doc_id']}/reindex", json={"chunk_size": 200, "overlap": 40})
    assert response.status_code == 200, response.text
    assert switched["done"] is True
    assert response.json()["document"]["is_online"] is False
    assert search(console) == []
    login(console, "employee")
    assert search(console) == []
