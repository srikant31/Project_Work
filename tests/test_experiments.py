"""Gold-metric tests plus real, isolated corpus/configuration comparisons."""

import copy
from types import SimpleNamespace
import uuid

import numpy as np
import pytest
from pydantic import ValidationError

from backend.models.experiment_models import ExperimentQuestion, ExperimentRunRequest
from backend.repositories.experiment_repository import ExperimentNotFoundError, ExperimentRepository
from backend.services.experiment_service import (
    ExperimentService, aggregate_metrics, cosine_hits, evidence_is_covered,
    evidence_occurrences, normalized_embedding_matrix, score_question,
)


def valid_request():
    identifier = str(uuid.uuid4())
    return {
        "document_ids": [identifier],
        "questions": [{"question_id": "q1", "question": "What is the VPN requirement?",
                       "answerable": True, "expected_doc_id": identifier,
                       "expected_source_key": None, "evidence_quote": None}],
        "configurations": [{"chunk_size": 100, "overlap": 20, "top_k": 3}],
    }


@pytest.mark.parametrize("case", [
    "duplicate_documents", "duplicate_questions", "duplicate_configurations",
    "cross_source_id", "missing_source", "overlap_equal_size", "bool_chunk_size",
    "unsupported_with_source", "no_answerable", "too_many_documents",
    "too_many_questions", "too_many_configurations", "blank_question", "unknown_field",
])
def test_strict_request_validation(case):
    payload = valid_request()
    if case == "duplicate_documents":
        payload["document_ids"] *= 2
    elif case == "duplicate_questions":
        payload["questions"] *= 2
    elif case == "duplicate_configurations":
        payload["configurations"] *= 2
    elif case == "cross_source_id":
        payload["questions"][0]["expected_doc_id"] = str(uuid.uuid4())
    elif case == "missing_source":
        payload["questions"][0]["expected_doc_id"] = None
    elif case == "overlap_equal_size":
        payload["configurations"][0]["overlap"] = 100
    elif case == "bool_chunk_size":
        payload["configurations"][0]["chunk_size"] = True
    elif case == "unsupported_with_source":
        payload["questions"][0]["answerable"] = False
    elif case == "no_answerable":
        payload["questions"][0].update(answerable=False, expected_doc_id=None)
    elif case == "too_many_documents":
        payload["document_ids"] += [str(uuid.uuid4()) for _ in range(10)]
    elif case == "too_many_questions":
        payload["questions"] = [{**payload["questions"][0], "question_id": f"q{i}"} for i in range(101)]
    elif case == "too_many_configurations":
        payload["configurations"] = [{"chunk_size": 100, "overlap": 20, "top_k": i} for i in range(1, 14)]
    elif case == "blank_question":
        payload["questions"][0]["question"] = "   "
    elif case == "unknown_field":
        payload["feedback_rating"] = "up"
    with pytest.raises(ValidationError):
        ExperimentRunRequest.model_validate(payload)


def test_evidence_union_respects_exact_source_offsets_and_document_identity():
    text = "Approval is required.\n\nClaims are due within 21 calendar days."
    quote = "Claims are due within 21 calendar days."
    spans = evidence_occurrences(text, quote)
    start, end = spans[0]
    boundary = text.index("21")
    intervals = [{"start_char": start, "end_char": boundary}, {"start_char": boundary, "end_char": end}]
    assert evidence_is_covered(text, spans, intervals)
    broken = [{"start_char": start, "end_char": boundary}, {"start_char": boundary + 1, "end_char": end}]
    assert not evidence_is_covered(text, spans, broken)
    assert evidence_occurrences(text, "within 30 calendar days") == []
    question = ExperimentQuestion(question_id="q", question="When are claims due?", answerable=True,
                                  expected_source_key="policy.txt", evidence_quote=quote)
    unrelated = [{**interval, "doc_id": "wrong-source", "rank": index + 1} for index, interval in enumerate(intervals)]
    score = score_question(question, unrelated, "expected-source", source_text=text, occurrences=spans)
    assert score == {"source_hit": False, "evidence_hit": False, "reciprocal_rank": 0.0}


def test_answerable_and_evidence_denominators_exclude_unsupported_diagnostics():
    rows = [
        {"answerable": True, "source_hit": True, "evidence_hit": True, "reciprocal_rank": 0.5, "latency_ms": 10},
        {"answerable": True, "source_hit": False, "evidence_hit": None, "reciprocal_rank": 0.0, "latency_ms": 30},
        {"answerable": False, "source_hit": None, "evidence_hit": None, "reciprocal_rank": None, "latency_ms": 1000},
    ]
    result = aggregate_metrics(rows)
    assert result == {"answerable_count": 2, "evidence_question_count": 1, "unsupported_count": 1,
                      "source_hit_rate": 0.5, "evidence_hit_rate": 1.0,
                      "mean_reciprocal_rank": 0.25, "mean_latency_ms": 20.0}
    assert aggregate_metrics([rows[1]])["evidence_hit_rate"] is None
    unsupported = ExperimentQuestion(question_id="out", question="What is the bonus percentage?", answerable=False)
    diagnostic = score_question(unsupported, [{"doc_id": "any", "rank": 1}], None)
    assert diagnostic["source_hit"] is None and diagnostic["evidence_hit"] is None
    assert "refusal" in diagnostic["diagnostic"]


def test_cosine_ranking_uses_norms_and_rejects_invalid_embedding_outputs():
    matrix = normalized_embedding_matrix([[3, 0], [0, 4], [-3, 0]], 3, 2)
    hits = cosine_hits([{"doc_id": "east"}, {"doc_id": "north"}, {"doc_id": "west"}],
                       matrix, np.array([1.0, 0.0]), 2)
    assert [hit["doc_id"] for hit in hits] == ["east", "north"]
    assert [hit["similarity_score"] for hit in hits] == [1.0, 0.0]
    for vectors in ([[0, 0]], [[float("nan"), 1]], [[1]], [[1, 0], [0, 1]]):
        with pytest.raises(ValueError):
            normalized_embedding_matrix(vectors, 1, 2)


def test_saved_reports_are_atomic_and_uuid_scoped(tmp_path):
    repository = ExperimentRepository(lambda: None, tmp_path / "reports")
    identifier = str(uuid.uuid4())
    report = {"run_id": identifier, "status": "completed", "corpus": [], "note": "₹2,500"}
    repository.save(report)
    assert repository.get(identifier) == report
    with pytest.raises(ValueError):
        repository.save({**report, "invalid": float("nan")})
    assert repository.get(identifier) == report
    assert not list(repository.output_root.glob("*.tmp"))
    with pytest.raises(ExperimentNotFoundError):
        repository.get("../../private.json")
    with pytest.raises(ExperimentNotFoundError):
        repository.get(uuid.uuid4())


def test_source_key_labels_must_be_unambiguous_and_agree_with_ids():
    identifiers = [str(uuid.uuid4()), str(uuid.uuid4())]
    corpus = [{"snapshot": {"doc_id": identifier, "source_key": "shared.txt"},
               "loaded": SimpleNamespace(source_text="The leave cap is 18 days.")} for identifier in identifiers]
    question = ExperimentQuestion(question_id="q", question="What is the cap?", answerable=True,
                                  expected_source_key="shared.txt")
    with pytest.raises(ValueError, match="ambiguous"):
        ExperimentService._labels([question], corpus)
    question = question.model_copy(update={"expected_doc_id": uuid.UUID(identifiers[0])})
    assert ExperimentService._labels([question], corpus)[0]["expected_doc_id"] == identifiers[0]
    question = question.model_copy(update={"expected_source_key": "other.txt"})
    with pytest.raises(ValueError, match="disagree"):
        ExperimentService._labels([question], corpus)


@pytest.mark.parametrize("role, expected", [(None, 401), ("employee", 403)])
def test_experiment_routes_are_admin_only_without_loading_the_model(role, expected):
    from fastapi import FastAPI, HTTPException
    from fastapi.testclient import TestClient
    from backend.api.dependencies import current_user
    from backend.api.experiments import router

    app = FastAPI()
    app.include_router(router, prefix="/api")
    def actor():
        if role is None:
            raise HTTPException(status_code=401, detail="Please sign in")
        return SimpleNamespace(role=role)
    app.dependency_overrides[current_user] = actor
    with TestClient(app) as client:
        assert client.get("/api/experiments/config").status_code == expected
        assert client.post("/api/experiments/run", json=valid_request()).status_code == expected
        assert client.get(f"/api/experiments/{uuid.uuid4()}").status_code == expected


@pytest.fixture
def real_experiment(db_factory, real_embedder, real_retriever, tmp_path):
    from backend.services.document_processing_service import DocumentProcessingService
    from backend.utils.file_storage import FileStorage

    source_root = tmp_path / "sources"
    source_root.mkdir()
    vpn = source_root / "vpn.txt"
    vpn.write_text(
        "VPN access requires a company-managed laptop and multifactor authentication. "
        "Personal laptops cannot connect to the corporate VPN.\n\n"
        "A missing authenticator must be reported to the Security Desk before requesting an enrollment reset. "
        "The employee presents an identity badge and obtains manager approval.\n\n"
        "The helpdesk records the exact authentication error and its local time. It checks the endpoint agent "
        "before reinstalling the VPN client. Never copy credentials or recovery codes into a support ticket.\n\n"
        "An analyst closes an incident after the employee disconnects, reconnects, and confirms that the internal "
        "application can be opened. A new access request is reviewed by the designated application owner.",
        encoding="utf-8",
    )
    leave = source_root / "leave.md"
    leave.write_text("# Leave policy\n\nEligible staff receive 18 days of annual leave.\n\n"
                     "Planned leave requires manager approval before the first day away.", encoding="utf-8")
    with DocumentProcessingService(session_factory=db_factory, embedder=real_embedder,
                                   retriever=real_retriever) as pipeline:
        vpn_result = pipeline.process_file(vpn, category_name="IT Support", source_key="experiments/vpn.txt")
        leave_result = pipeline.process_file(leave, category_name="HR / Leave", source_key="experiments/leave.md")
    service = ExperimentService(session_factory=db_factory, storage=FileStorage(source_root),
                                embedder=real_embedder, output_root=tmp_path / "experiments")
    payload = {
        "document_ids": [vpn_result["doc_id"], leave_result["doc_id"]],
        "questions": [
            {"question_id": "vpn", "question": "What does VPN access require?", "answerable": True,
             "expected_doc_id": vpn_result["doc_id"], "evidence_quote": "VPN access requires a company-managed laptop and multifactor authentication."},
            {"question_id": "leave", "question": "How many annual leave days do eligible staff receive?", "answerable": True,
             "expected_source_key": "experiments/leave.md", "evidence_quote": "Eligible staff receive 18 days of annual leave."},
            {"question_id": "unsupported", "question": "What is the annual bonus percentage?", "answerable": False},
        ],
        "configurations": [{"chunk_size": 64, "overlap": 8, "top_k": 20},
                           {"chunk_size": 128, "overlap": 16, "top_k": 20},
                           {"chunk_size": 64, "overlap": 8, "top_k": 1}],
    }
    return SimpleNamespace(service=service, factory=db_factory, retriever=real_retriever,
                           payload=payload, vpn=vpn, leave=leave, vpn_id=vpn_result["doc_id"])


def _live_snapshot(environment):
    from backend.database.models import Document, DocumentChunk, IngestionJob

    with environment.factory() as session:
        return {
            "documents": [(str(row.doc_id), row.status, row.is_online, row.source_hash)
                          for row in session.query(Document).order_by(Document.doc_id)],
            "chunks": [(str(row.chunk_id), row.chunk_text, row.is_active)
                       for row in session.query(DocumentChunk).order_by(DocumentChunk.chunk_id)],
            "jobs": [(str(row.id), row.status) for row in session.query(IngestionJob).order_by(IngestionJob.id)],
            "vectors": environment.retriever.collection.get(include=["documents", "metadatas"]),
        }


@pytest.mark.integration
def test_real_configuration_comparison_is_saved_without_live_corpus_changes(real_experiment):
    environment = real_experiment
    before = _live_snapshot(environment)
    files_before = {path: path.read_bytes() for path in (environment.vpn, environment.leave)}
    report = environment.service.run(environment.payload)
    assert report["status"] == "completed" and report["retrieval_only"]
    assert len(report["results"]) == 3 and len(report["corpus"]) == 2
    assert report["results"][0]["chunk_count"] > report["results"][1]["chunk_count"]
    assert report["results"][2]["reused_index"] is True
    for result in report["results"][:2]:
        assert result["source_hit_rate"] == 1.0 and result["evidence_hit_rate"] == 1.0
        assert result["answerable_count"] == 2 and result["evidence_question_count"] == 2
        assert result["unsupported_count"] == 1
        diagnostic = result["questions"][-1]
        assert diagnostic["source_hit"] is None and diagnostic["evidence_hit"] is None
    assert environment.service.get(report["run_id"]) == report
    assert _live_snapshot(environment) == before
    assert {path: path.read_bytes() for path in files_before} == files_before


@pytest.mark.integration
def test_unavailable_offline_stale_and_invalid_gold_sources_fail_without_index_changes(real_experiment):
    from backend.database.models import Document

    environment = real_experiment
    before = _live_snapshot(environment)
    bad_gold = copy.deepcopy(environment.payload)
    bad_gold["questions"][0]["evidence_quote"] = "This is not in the source."
    with pytest.raises(ValueError, match="gold evidence"):
        environment.service.run(bad_gold)
    assert _live_snapshot(environment) == before
    environment.vpn.write_text("Changed outside the managed pipeline.", encoding="utf-8")
    with pytest.raises(ValueError, match="changed since indexing"):
        environment.service.run(environment.payload)
    assert _live_snapshot(environment) == before
    with environment.factory() as session:
        session.get(Document, uuid.UUID(environment.vpn_id)).is_online = False
        session.commit()
    config = environment.service.config()
    assert environment.vpn_id not in {row["doc_id"] for row in config["documents"]}
    offline_snapshot = _live_snapshot(environment)
    with pytest.raises(ValueError, match="current online"):
        environment.service.run(environment.payload)
    assert _live_snapshot(environment) == offline_snapshot
    assert not environment.service.repository.output_root.exists()


@pytest.mark.integration
def test_pdf_corpus_keeps_physical_blank_page_boundaries(db_factory, real_embedder, real_retriever, tmp_path):
    from pathlib import Path
    from pypdf import PdfReader, PdfWriter
    from backend.services.document_processing_service import DocumentProcessingService
    from backend.utils.file_storage import FileStorage

    source_root = tmp_path / "sources"
    source_root.mkdir()
    source = source_root / "with_blank_page.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=595, height=842)
    fixture = Path(__file__).resolve().parents[1] / "data/documents/sample_equipment_manual.pdf"
    reader = PdfReader(fixture)
    for page in reader.pages:
        writer.add_page(page)
    with source.open("wb") as handle:
        writer.write(handle)
    with DocumentProcessingService(session_factory=db_factory, embedder=real_embedder,
                                   retriever=real_retriever) as pipeline:
        original = pipeline.process_file(source, category_name="IT Support", source_key="experiment/pdf")
    service = ExperimentService(session_factory=db_factory, storage=FileStorage(source_root),
                                embedder=real_embedder, output_root=tmp_path / "experiments")
    report = service.run({
        "document_ids": [original["doc_id"]],
        "questions": [{"question_id": "pdf", "question": "What does the equipment manual explain?",
                       "answerable": True, "expected_doc_id": original["doc_id"]}],
        "configurations": [{"chunk_size": 100, "overlap": 20, "top_k": 20}],
    })
    assert report["corpus"][0]["page_count"] == len(reader.pages) + 1
    hits = report["results"][0]["questions"][0]["hits"]
    assert hits and all(hit["page_number"] >= 2 for hit in hits)
    assert report["results"][0]["evidence_hit_rate"] is None
