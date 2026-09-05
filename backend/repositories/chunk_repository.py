from backend.database.models import DocumentChunk


class ChunkRepository:
    def __init__(self, session):
        self.session = session

    def create_chunk(self, document_id, chunk_text: str, chunk_index: int,
                      token_count: int, embedding_model: str, embedding_vector_id: str = None) -> DocumentChunk:
        chunk = DocumentChunk(
            document_id=document_id,
            chunk_text=chunk_text,
            chunk_index=chunk_index,
            token_count=token_count,
            embedding_model=embedding_model,
            embedding_vector_id=embedding_vector_id,
        )
        self.session.add(chunk)
        self.session.commit()
        self.session.refresh(chunk)
        return chunk

    def get_chunks_by_document(self, document_id) -> list[DocumentChunk]:
        return (
            self.session.query(DocumentChunk)
            .filter(DocumentChunk.document_id == document_id, DocumentChunk.is_active == True)
            .all()
        )