"""CLI safety boundaries plus real-store audit corruption detection."""

import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


def test_dry_run_never_imports_database_or_ingestion_service(tmp_path):
    (tmp_path / "policy.txt").write_text("An approved document", encoding="utf-8")
    # Use a fresh interpreter: other test modules legitimately import the DB.
    script = """
import sys
from pathlib import Path
from scripts import ingest_folder
root = Path(sys.argv[1])
ingest_folder.create_embedder = lambda allow: object()
def preview(paths, embedder, size, overlap, root):
    assert [path.name for path in paths] == ['policy.txt']
    return {'failed': 0, 'rejected': 0}
ingest_folder.preview_paths = preview
assert ingest_folder.main(['--folder', str(root), '--dry-run']) == 0
assert 'backend.database.connection' not in sys.modules
assert 'backend.services.document_processing_service' not in sys.modules
"""
    result = subprocess.run([sys.executable, "-c", script, str(tmp_path)], cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_ingestion_uses_relative_source_identity_without_title_skip(tmp_path, monkeypatch):
    from scripts import ingest_folder

    nested = tmp_path / "network"
    nested.mkdir()
    (tmp_path / "policy.txt").write_text("First policy", encoding="utf-8")
    (nested / "policy.md").write_text("# Second policy", encoding="utf-8")
    calls = []

    class Service:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def process_file(self, path, **kwargs):
            calls.append((path, kwargs))
            return {"doc_id": "id", "version": 1, "chunk_count": 1, "skipped": False, "cleanup_pending": False}

    monkeypatch.setattr(ingest_folder, "create_embedder", lambda allow: object())
    monkeypatch.setitem(sys.modules, "backend.services.document_processing_service", SimpleNamespace(DocumentProcessingService=Service))
    assert ingest_folder.main(["--folder", str(tmp_path), "--recursive"]) == 0
    assert {kwargs["source_key"] for _, kwargs in calls} == {"policy.txt", "network/policy.md"}
    assert {kwargs["doc_name"] for _, kwargs in calls} == {"policy"}
    assert all(kwargs["category_name"] == "IT Support" for _, kwargs in calls)


def test_reconciliation_requires_explicit_scope_before_importing_service(capsys):
    from scripts.reconcile_stores import main

    with pytest.raises(SystemExit) as caught:
        main(["--apply"])
    assert caught.value.code == 2
    assert "--apply requires --doc-id" in capsys.readouterr().err


def test_reconciliation_successful_failed_stage_is_not_command_failure(monkeypatch):
    from scripts.reconcile_stores import main

    class Service:
        def __init__(self, **kwargs):
            assert kwargs["retriever"] is not None

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def reconcile(self, **kwargs):
            assert kwargs["apply"] and kwargs["doc_id"] is not None
            return [{"doc_id": str(kwargs["doc_id"]), "status": "failed", "action": "retire_stale_stage", "detail": "Cleanup complete"}]

    monkeypatch.setitem(sys.modules, "backend.services.document_processing_service", SimpleNamespace(DocumentProcessingService=Service))
    monkeypatch.setitem(sys.modules, "backend.services.retrieval_service", SimpleNamespace(RetrievalService=lambda **kwargs: object()))
    assert main(["--apply", "--doc-id", "12345678-1234-1234-1234-123456789abc", "--stale-minutes", "0"]) == 0


def test_reconciliation_report_selects_existing_collection_without_model_download(monkeypatch, tmp_path):
    from scripts import preview_documents
    from scripts.reconcile_stores import main

    selected, calls = [], []
    retriever = object()

    def retrieval(**kwargs):
        selected.append(kwargs)
        return retriever

    class Service:
        def __init__(self, **kwargs):
            assert kwargs == {"retriever": retriever, "embedder": None}

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def reconcile(self, **kwargs):
            calls.append(kwargs)
            return [{"status": "active", "action": "repair_active_vectors"}]

    def unexpected_download(*args, **kwargs):
        raise AssertionError("Report-only recovery must never load/download a model")

    monkeypatch.setattr(preview_documents, "create_embedder", unexpected_download)
    monkeypatch.setitem(sys.modules, "backend.services.document_processing_service", SimpleNamespace(DocumentProcessingService=Service))
    monkeypatch.setitem(sys.modules, "backend.services.retrieval_service", SimpleNamespace(RetrievalService=retrieval))
    assert main(["--chroma-path", str(tmp_path), "--collection", "original_collection", "--allow-model-download"]) == 0
    assert selected == [{"persist_path": tmp_path, "collection_name": "original_collection", "create": False}]
    assert len(calls) == 1 and not calls[0]["apply"]


def test_reconciliation_missing_store_is_clear_error_without_service_creation(monkeypatch, capsys):
    from scripts.reconcile_stores import main

    def missing(**kwargs):
        assert kwargs["create"] is False
        raise FileNotFoundError("Existing Chroma database required; nothing was created")

    def unexpected_service(**kwargs):
        raise AssertionError("Missing index must fail before any database session")

    monkeypatch.setitem(sys.modules, "backend.services.document_processing_service", SimpleNamespace(DocumentProcessingService=unexpected_service))
    monkeypatch.setitem(sys.modules, "backend.services.retrieval_service", SimpleNamespace(RetrievalService=missing))
    with pytest.raises(SystemExit) as caught:
        main([])
    assert caught.value.code == 2
    assert "Cannot open existing vector store" in capsys.readouterr().err


@pytest.mark.parametrize("action", ["repair_active_vectors", "cleanup_vectors", "retire_stale_stage"])
def test_reconciliation_downloads_only_for_explicit_active_repair(monkeypatch, action):
    from scripts import preview_documents
    from scripts.reconcile_stores import main

    embedder, retriever = object(), object()
    constructors, downloads, calls = [], [], []

    class Service:
        def __init__(self, **kwargs):
            constructors.append(kwargs)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def reconcile(self, **kwargs):
            calls.append(kwargs)
            return [{"status": "active", "action": action, "detail": "Recovery complete"}]

    def download(**kwargs):
        downloads.append(kwargs)
        return embedder

    monkeypatch.setattr(preview_documents, "create_embedder", download)
    monkeypatch.setitem(sys.modules, "backend.services.document_processing_service", SimpleNamespace(DocumentProcessingService=Service))
    monkeypatch.setitem(sys.modules, "backend.services.retrieval_service", SimpleNamespace(RetrievalService=lambda **kwargs: retriever))
    assert main(["--apply", "--doc-id", "12345678-1234-1234-1234-123456789abc", "--allow-model-download"]) == 0
    assert downloads == ([{"allow_model_download": True}] if action == "repair_active_vectors" else [])
    selected_embedder = embedder if action == "repair_active_vectors" else None
    assert constructors == [{"retriever": retriever}, {"retriever": retriever, "embedder": selected_embedder}]
    assert [row["apply"] for row in calls] == [False, True]


@pytest.mark.integration
def test_store_audit_detects_changed_source_offsets_and_token_counts(db_factory, real_embedder, real_retriever):
    from backend.database.models import DocumentChunk
    from backend.services.document_processing_service import DocumentProcessingService
    from scripts.verify_stores import audit_stores

    with DocumentProcessingService(session_factory=db_factory, embedder=real_embedder, retriever=real_retriever) as service:
        service.process_document(doc_name="Audit policy", doc_type="txt", raw_text="VPN access requires multifactor authentication.", category_name="IT Support", source_key="audit.txt")
        row = service.session.query(DocumentChunk).one()
        row.start_char += 1
        row.token_count += 1
        service.session.commit()
        report = audit_stores(service.session, real_retriever.collection, embedder=real_embedder)
        failed = {check["name"] for check in report["checks"] if check["status"] == "FAIL"}
        assert "Managed chunk offsets map to exact source text" in failed
        assert "Managed chunks satisfy tokenizer/model limits" in failed
