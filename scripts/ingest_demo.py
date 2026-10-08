"""Run from project root:  python -m scripts.ingest_demo

Proves the pipeline end to end: ingest -> Postgres rows + Chroma vectors ->
retrieve -> join back to Postgres metadata. Also verifies the rollback path.
"""

from backend.database.connection import SessionLocal
from backend.repositories.chunk_repository import ChunkRepository
from backend.services.document_processing_service import DocumentProcessingService

SAMPLE_PAGES = [
    """The DHTI helpdesk handles all IT support requests for the institute.
    To reset your network password, visit the IT Cell in Block B between
    0900 and 1700 hours with your identity card. Password resets cannot be
    processed over the telephone under any circumstances.""",
    """Leave applications must be submitted through the faculty portal at
    least seven working days in advance. Emergency leave may be applied for
    retrospectively but requires written endorsement from the head of
    department within three working days of returning to duty.""",
    """Laptops issued by the institute remain institute property. Any hardware
    fault must be reported to the IT Cell using the asset tag printed on the
    underside of the device. Do not attempt repairs through external vendors,
    as this voids the institute warranty agreement.""",
]


def main():
    session = SessionLocal()
    try:
        service = DocumentProcessingService(db_session=session)

        print("Ingesting...")
        result = service.process_document(
            doc_name="DHTI IT Helpdesk Handbook",
            doc_type="txt",
            uploaded_by=None,
            doc_path="data/documents/handbook.txt",
            pages=SAMPLE_PAGES,
            category_name="IT Support",
            chunk_size=60,
            overlap=15,
        )
        print(f"  doc_id      : {result['doc_id']}")
        print(f"  chunks      : {result['chunk_count']}")
        print(f"  chroma count: {service.retriever.count()}")

        # --- verify embedding_vector_id and page_number are actually populated
        repo = ChunkRepository(session)
        rows = repo.get_chunks_by_document(result["doc_id"])
        print("\nPostgres rows (first 3):")
        for r in rows[:3]:
            print(
                f"  idx={r.chunk_index} page={r.page_number} "
                f"tokens={r.token_count} vec_id={r.embedding_vector_id}"
            )
        assert all(r.embedding_vector_id for r in rows), "embedding_vector_id still NULL"
        assert all(r.page_number for r in rows), "page_number still NULL"

        # --- retrieval
        for q in ["how do I reset my password?", "what if my laptop breaks?"]:
            print(f"\nQ: {q}")
            for hit in service.search(q, top_k=2):
                print(
                    f"  #{hit['rank']} score={hit['similarity_score']:.3f} "
                    f"page={hit['page_number']} :: {hit['text'][:80]}..."
                )

        # --- rollback path: force a Chroma failure, confirm no orphan rows
        print("\nTesting failure path (expect a caught exception)...")
        broken = DocumentProcessingService(db_session=session)

        def boom(*a, **k):
            raise RuntimeError("simulated vector store outage")

        broken.retriever.add_chunks = boom
        try:
            broken.process_document(
                doc_name="Doomed Doc",
                doc_type="txt",
                uploaded_by=None,
                doc_path="data/documents/doomed.txt",
                raw_text="this ingestion is designed to fail halfway through",
                category_name="Uncategorized",
            )
        except RuntimeError as e:
            print(f"  caught as expected: {e}")
            doomed = broken.doc_repo.get_latest_version_by_name("Doomed Doc")
            orphans = ChunkRepository(session).get_chunks_by_document(doomed.doc_id)
            print(f"  orphaned chunk rows: {len(orphans)}  (must be 0)")
            assert len(orphans) == 0

        print("\nAll checks passed.")
    finally:
        session.close()


if __name__ == "__main__":
    main()
