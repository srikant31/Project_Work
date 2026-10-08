from backend.database.models import DocumentChunk


class ChunkRepository:
    """Data access only. NOTHING here commits — the service owns the transaction."""

    def __init__(self, session):
        self.session = session

    def create_chunk(
        self,
        document_id,
        chunk_text: str,
        chunk_index: int,
        token_count: int,
        embedding_model: str,
        embedding_vector_id: str | None = None,
        page_number: int | None = None,
        section_heading: str | None = None,
    ) -> DocumentChunk:
        chunk = DocumentChunk(
            document_id=document_id,
            chunk_text=chunk_text,
            chunk_index=chunk_index,
            token_count=token_count,
            embedding_model=embedding_model,
            embedding_vector_id=embedding_vector_id,
            page_number=page_number,
            section_heading=section_heading,
        )
        self.session.add(chunk)
        # flush() sends the INSERT so the server-side UUID default fires and
        # chunk.chunk_id is populated — but stays inside the open transaction.
        self.session.flush()
        return chunk

    def get_chunks_by_document(self, document_id) -> list[DocumentChunk]:
        return (
            self.session.query(DocumentChunk)
            .filter(
                DocumentChunk.document_id == document_id,
                DocumentChunk.is_active.is_(True),
            )
            .order_by(DocumentChunk.chunk_index)
            .all()
        )

    def get_chunks_by_ids(self, chunk_ids: list[str]) -> list[DocumentChunk]:
        """Used by the retrieval path: Chroma returns IDs, Postgres returns the
        authoritative row (page number, heading, parent document)."""
        if not chunk_ids:
            return []
        return (
            self.session.query(DocumentChunk)
            .filter(DocumentChunk.chunk_id.in_(chunk_ids))
            .all()
        )

    def deactivate_chunks_for_document(self, document_id) -> int:
        """Soft-delete all chunks for a document. Returns rows affected.
        Caller MUST mirror this in the vector store or retrieval goes stale."""
        return (
            self.session.query(DocumentChunk)
            .filter(
                DocumentChunk.document_id == document_id,
                DocumentChunk.is_active.is_(True),
            )
            .update({DocumentChunk.is_active: False}, synchronize_session=False)
        )
