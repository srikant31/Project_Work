from backend.database.connection import get_session
from backend.services.document_processing_service import DocumentProcessingService
from backend.repositories.chunk_repository import ChunkRepository


def test_synthetic_document_pipeline():
    session = get_session()
    service = DocumentProcessingService(session)

    synthetic_text = (
        "Employees can request annual leave through the HR portal. "
        "Leave requests must be submitted at least five business days in advance. "
        "VPN access requires a company-issued laptop and multi-factor authentication."
    )

    result = service.process_document(
        doc_name="synthetic_leave_policy.txt",
        doc_type="txt",
        uploaded_by=None,
        doc_path="synthetic/leave_policy.txt",
        raw_text=synthetic_text,
    )

    assert result["doc_id"] is not None
    assert len(result["chunk_ids"]) > 0

    # prove the link: fetch the chunks back from Postgres using the doc_id the pipeline returned
    chunk_repo = ChunkRepository(session)
    chunks_in_db = chunk_repo.get_chunks_by_document(result["doc_id"])
    assert len(chunks_in_db) == len(result["chunk_ids"])

    session.close()