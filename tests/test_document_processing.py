import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest

from backend.database.models import Document, DocumentChunk, IngestionJob
from backend.services.document_processing_service import (
    ConcurrentIngestionError,
    DocumentProcessingService,
    RecoveryPendingError,
)

pytestmark = pytest.mark.integration


@pytest.fixture
def service(db_factory, real_embedder, real_retriever):
    with DocumentProcessingService(session_factory=db_factory, embedder=real_embedder,
                                   retriever=real_retriever) as instance:
        yield instance


def ingest(service, text="VPN access requires a company laptop and MFA.", **kwargs):
    return service.process_document(
        doc_name="Network policy", doc_type="txt", raw_text=text,
        category_name="IT Support", source_key="network.txt", **kwargs,
    )


def test_file_provenance_idempotency_and_configuration(service, db_factory):
    first = service.process_file("data/documents/sample_password_policy.txt", category_name="IT Support")
    repeat = service.process_file("data/documents/sample_password_policy.txt", category_name="IT Support")
    assert repeat["skipped"] and repeat["doc_id"] == first["doc_id"]
    changed_config = service.process_file("data/documents/sample_password_policy.txt", category_name="IT Support", chunk_size=150, overlap=25)
    assert changed_config["version"] == 2 and not changed_config["skipped"]
    with db_factory() as session:
        old = session.get(Document, uuid.UUID(first["doc_id"]))
        new = session.get(Document, uuid.UUID(changed_config["doc_id"]))
        assert old.status == "deactivated" and new.status == "active"
        assert old.extracted_text == new.extracted_text
        assert len(new.source_hash) == 64
        for row in session.query(DocumentChunk).filter_by(document_id=new.doc_id):
            assert new.extracted_text[row.start_char:row.end_char] == row.chunk_text
            assert row.page_number is None
    hits = service.search("How do I reset my password?", top_k=3)
    assert hits and all(h["doc_id"] == changed_config["doc_id"] for h in hits)


def test_changed_content_safe_replacement_and_source_identity(service, db_factory):
    first = ingest(service)
    second = ingest(service, "VPN support is now available from the IT desk.")
    assert second["version"] == 2
    other = service.process_document(doc_name="Network policy", doc_type="txt", raw_text="A different policy.",
                                     category_name="IT Support", source_key="different/network.txt")
    assert other["version"] == 1
    with db_factory() as session:
        assert session.query(Document).filter_by(status="active").count() == 2
        assert session.get(Document, uuid.UUID(first["doc_id"])).status == "deactivated"


@pytest.mark.parametrize("failure", ["partial_vectors", "verification", "completion", "publication"])
def test_failure_preserves_old_searchable_version(service, db_factory, monkeypatch, failure):
    old = ingest(service)
    if failure == "partial_vectors":
        original = service.retriever.add_chunks
        def fail(records, doc_id, **kwargs):
            original(records[:1], doc_id, **kwargs)
            raise RuntimeError("Partial vector outage")
        monkeypatch.setattr(service.retriever, "add_chunks", fail)
    elif failure == "verification":
        def fail(*args, **kwargs):
            raise RuntimeError("Verification outage")
        monkeypatch.setattr(service.retriever, "verify_document", fail)
    elif failure == "completion":
        def fail(*args, **kwargs):
            raise RuntimeError("Completion update outage")
        monkeypatch.setattr(service.job_repo, "mark_completed", fail)
    else:
        original = service.session.commit
        calls = 0
        def fail():
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError("Publication commit outage")
            original()
        monkeypatch.setattr(service.session, "commit", fail)
    with pytest.raises(RuntimeError):
        ingest(service, "The replacement policy says to call the service desk.")
    with db_factory() as session:
        docs = session.query(Document).order_by(Document.version).all()
        assert [d.status for d in docs] == ["active", "failed"]
        assert session.query(IngestionJob).filter_by(document_id=docs[-1].doc_id).one().status == "failed"
        assert not docs[-1].cleanup_pending
    assert set(service.retriever.get_by_doc(old["doc_id"])["ids"]) == set(old["chunk_ids"])
    assert all(h["doc_id"] == old["doc_id"] for h in service.search("VPN access"))


def test_bad_embedding_and_parameters_do_not_stage(service, db_factory, monkeypatch):
    ingest(service)
    with pytest.raises(ValueError):
        ingest(service, "New policy", overlap=-1)
    monkeypatch.setattr(service.embedder, "embed_batch", lambda texts: [])
    with pytest.raises(ValueError, match="count"):
        ingest(service, "A replacement that must not be lost")
    with db_factory() as session:
        assert session.query(Document).count() == 1


def test_cleanup_failure_preserves_published_version(service, db_factory, monkeypatch):
    old = ingest(service)
    original = service.retriever.delete_by_doc
    def fail(doc_id):
        if doc_id == old["doc_id"]:
            raise RuntimeError("Cleanup outage")
        original(doc_id)
    monkeypatch.setattr(service.retriever, "delete_by_doc", fail)
    new = ingest(service, "Replacement VPN procedure requires a hardware security key.")
    assert new["cleanup_pending"]
    assert all(h["doc_id"] == new["doc_id"] for h in service.search("VPN"))
    assert service.reconcile(old["doc_id"])[0]["action"] == "cleanup_vectors"
    monkeypatch.setattr(service.retriever, "delete_by_doc", original)
    service.reconcile(old["doc_id"], apply=True)
    assert not service.retriever.get_by_doc(old["doc_id"])["ids"]
    with db_factory() as session:
        assert not session.get(Document, uuid.UUID(old["doc_id"])).cleanup_pending


@pytest.mark.parametrize("unknown", [False, True])
def test_lost_commit_acknowledgement_never_deletes_published_vectors(service, db_factory, monkeypatch, unknown):
    original = service.session.commit
    calls = 0
    def commit():
        nonlocal calls
        calls += 1
        original()
        if calls == 2:
            raise RuntimeError("Connection lost after server committed")
    monkeypatch.setattr(service.session, "commit", commit)
    if unknown:
        monkeypatch.setattr(service, "_publication_state", lambda *a: None)
        with pytest.raises(RecoveryPendingError):
            ingest(service)
    else:
        assert ingest(service)["version"] == 1
    with db_factory() as session:
        doc = session.query(Document).one()
        assert doc.status == "active"
        assert session.query(IngestionJob).one().status == "completed"
        assert service.retriever.get_by_doc(str(doc.doc_id))["ids"]


def test_crash_staging_hidden_and_scoped_recovery(service, db_factory, monkeypatch):
    old = ingest(service)
    original = service.retriever.add_chunks
    def crash(records, doc_id, **kwargs):
        original(records, doc_id, **kwargs)
        raise KeyboardInterrupt("Simulated process interruption")
    monkeypatch.setattr(service.retriever, "add_chunks", crash)
    with pytest.raises(KeyboardInterrupt):
        ingest(service, "A staged replacement that must remain invisible")
    service.session.rollback()  # emulate connection release on process death
    with db_factory() as session:
        stage = session.query(Document).filter_by(status="staging").one()
        stage_id = str(stage.doc_id)
    assert all(h["doc_id"] == old["doc_id"] for h in service.search("replacement"))
    with pytest.raises(ValueError, match="explicit"):
        service.reconcile(apply=True)
    report = service.reconcile(stage_id, apply=True, stale_minutes=0)
    assert report[0]["status"] == "failed"
    assert not service.retriever.get_by_doc(stage_id)["ids"]


def test_same_source_concurrent_imports_preserve_one_active_version(db_factory, real_embedder, real_retriever):
    def run():
        with DocumentProcessingService(session_factory=db_factory, embedder=real_embedder,
                                       retriever=real_retriever) as svc:
            try:
                return ingest(svc)
            except ConcurrentIngestionError:
                return None
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: run(), range(2)))
    assert any(results)
    with db_factory() as session:
        assert session.query(Document).filter_by(status="active").count() == 1
        assert session.query(IngestionJob).filter_by(status="completed").count() == 1


def test_actual_audit_accepts_new_managed_corpus(service):
    from scripts.verify_stores import audit_stores
    ingest(service)
    checks = audit_stores(service.session, service.retriever.collection, embedder=service.embedder)["checks"]
    assert checks
    assert not [c for c in checks if c.get("status") == "FAIL"], checks


def test_repeat_import_detects_corrupt_index_and_scoped_repair(service):
    first = ingest(service)
    service.retriever.delete_by_doc(first["doc_id"])
    with pytest.raises(RecoveryPendingError, match="incomplete"):
        ingest(service)
    assert service.reconcile(first["doc_id"])[0]["action"] == "repair_active_vectors"
    service.reconcile(first["doc_id"], apply=True)
    restored = ingest(service)
    assert restored["skipped"] and restored["doc_id"] == first["doc_id"]


def test_nonfinite_or_wrong_dimension_embeddings_cannot_publish(service, db_factory, monkeypatch):
    first = ingest(service)
    monkeypatch.setattr(service.embedder, "embed_batch", lambda texts: [[float("nan")] * 384 for _ in texts])
    with pytest.raises(ValueError, match="nonfinite"):
        ingest(service, "A changed document")
    monkeypatch.setattr(service.embedder, "embed_batch", lambda texts: [[0.1] * 3 for _ in texts])
    with pytest.raises(ValueError, match="dimensions"):
        ingest(service, "A changed document")
    with db_factory() as session:
        assert session.query(Document).one().status == "active"


def test_invalid_file_does_not_touch_document_history(service, db_factory):
    ingest(service)
    with pytest.raises(ValueError):
        service.process_file("data/documents/sample_unsupported.xyz", category_name="IT Support")
    with db_factory() as session:
        assert session.query(Document).count() == 1


def test_retired_stage_cannot_be_resurrected_before_worker_reacquires_lock(service, db_factory, monkeypatch):
    old = ingest(service)
    original = service.doc_repo.lock_source
    calls = 0
    def lock(category_id, source_key):
        nonlocal calls
        calls += 1
        if calls == 3:
            with db_factory() as fresh:
                stage = fresh.query(Document).filter_by(status="staging").one()
                stage.status = "failed"
                fresh.query(IngestionJob).filter_by(document_id=stage.doc_id).one().status = "failed"
                fresh.commit()
        original(category_id, source_key)
    monkeypatch.setattr(service.doc_repo, "lock_source", lock)
    with pytest.raises(ConcurrentIngestionError, match="retired"):
        ingest(service, "An attempt retired before publication")
    assert all(h["doc_id"] == old["doc_id"] for h in service.search("VPN"))
    with db_factory() as session:
        assert [d.status for d in session.query(Document).order_by(Document.version)] == ["active", "failed"]


def test_file_default_identity_distinguishes_same_basename(service, tmp_path):
    a, b = tmp_path / "a" / "policy.txt", tmp_path / "b" / "policy.txt"
    a.parent.mkdir()
    b.parent.mkdir()
    a.write_text("First policy text", encoding="utf-8")
    b.write_text("Second policy text", encoding="utf-8")
    first = service.process_file(a, category_name="IT Support")
    second = service.process_file(b, category_name="IT Support")
    assert first["doc_id"] != second["doc_id"] and first["version"] == second["version"] == 1


@pytest.mark.parametrize("filename", ["sample_password_policy.txt", "sample_vpn_guide.md", "sample_equipment_manual.pdf", "sample_network_faq.docx"])
def test_each_supported_file_real_store_round_trip(service, db_factory, filename):
    from scripts.verify_stores import audit_stores
    result = service.process_file("data/documents/" + filename, category_name="IT Support")
    assert result["chunk_count"] > 0
    hits = service.search("What does this policy say about support?", top_k=2)
    assert hits and all(h["doc_id"] == result["doc_id"] for h in hits)
    if filename.endswith(".pdf"):
        assert all(h["page_number"] in {1, 2, 3} for h in hits)
    else:
        assert all(h["page_number"] is None for h in hits)
    report = audit_stores(service.session, service.retriever.collection, embedder=service.embedder)
    assert all(c["status"] == "PASS" for c in report["checks"]), report


def test_same_bytes_different_format_is_not_incorrectly_skipped(service):
    args = dict(doc_name="Policy", raw_text="# VPN policy\n\nUse MFA.",
                category_name="IT Support", source_key="policy")
    first = service.process_document(doc_type="txt", **args)
    second = service.process_document(doc_type="md", **args)
    assert second["version"] == 2 and not second["skipped"]
    assert first["source_hash"] == second["source_hash"]


def test_offline_toggle_during_staging_is_inherited_at_publication(service, db_factory, monkeypatch):
    old = ingest(service)
    original = service.doc_repo.lock_source
    calls = 0
    def lock(category_id, source_key):
        nonlocal calls
        calls += 1
        if calls == 3:
            with db_factory() as session:
                session.get(Document, uuid.UUID(old["doc_id"])).is_online = False
                session.commit()
        original(category_id, source_key)
    monkeypatch.setattr(service.doc_repo, "lock_source", lock)
    new = ingest(service, "Changed VPN policy while an administrator takes the source offline")
    with db_factory() as session:
        assert session.get(Document, uuid.UUID(new["doc_id"])).is_online is False
    assert service.search("VPN policy") == []
