from backend.database.models import Document, DocumentChunk


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
        start_char: int | None = None,
        end_char: int | None = None,
        is_active: bool = False,
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
            start_char=start_char,
            end_char=end_char,
            is_active=is_active,
        )
        self.session.add(chunk)
        # flush() sends the INSERT so the server-side UUID default fires and
        # chunk.chunk_id is populated — but stays inside the open transaction.
        self.session.flush()
        return chunk

    def get_chunks_by_document(self, document_id, active_only: bool = True) -> list[DocumentChunk]:
        query = self.session.query(DocumentChunk).filter(DocumentChunk.document_id == document_id)
        if active_only:
            query = query.join(Document).filter(
                DocumentChunk.is_active.is_(True), Document.status == "active", Document.is_online.is_(True)
            )
        return query.order_by(DocumentChunk.chunk_index).all()

    def get_chunks_by_ids(self, chunk_ids: list[str], active_only: bool = True) -> list[DocumentChunk]:
        """Used by the retrieval path: Chroma returns IDs, Postgres returns the
        authoritative row (page number, heading, parent document)."""
        if not chunk_ids:
            return []
        query = self.session.query(DocumentChunk).filter(DocumentChunk.chunk_id.in_(chunk_ids))
        if active_only:
            query = query.join(Document).filter(
                DocumentChunk.is_active.is_(True), Document.status == "active", Document.is_online.is_(True)
            )
        return query.all()

    def get_all_chunk_ids(self, document_id) -> list[str]:
        return [
            str(row[0])
            for row in self.session.query(DocumentChunk.chunk_id)
            .filter(DocumentChunk.document_id == document_id)
            .order_by(DocumentChunk.chunk_index)
            .all()
        ]

    def activate_chunks_for_document(self, document_id) -> int:
        return (
            self.session.query(DocumentChunk)
            .filter(DocumentChunk.document_id == document_id)
            .update({DocumentChunk.is_active: True}, synchronize_session="fetch")
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
            .update({DocumentChunk.is_active: False}, synchronize_session="fetch")
        )
