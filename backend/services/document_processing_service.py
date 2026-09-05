from backend.services.embedding_service import EmbeddingService
from backend.services.retrieval_service import RetrievalService
from backend.repositories.document_repository import DocumentRepository
from backend.repositories.chunk_repository import ChunkRepository


def chunk_text(text: str, chunk_size: int = 50) -> list[str]:
    """Naive word-count chunking — placeholder only, not the real §11 algorithm yet.
    Just enough to prove the pipeline works end to end."""
    words = text.split()
    return [" ".join(words[i:i + chunk_size]) for i in range(0, len(words), chunk_size)]


class DocumentProcessingService:
    def __init__(self, db_session):
        self.embedder = EmbeddingService()
        self.retriever = RetrievalService()
        self.doc_repo = DocumentRepository(db_session)
        self.chunk_repo = ChunkRepository(db_session)

    def process_document(self, doc_name: str, doc_type: str, uploaded_by: str, doc_path: str, raw_text: str) -> dict:
        # 1. Create the Documents row via the ORM repository
        document = self.doc_repo.create_document(
            doc_name=doc_name,
            doc_type=doc_type,
            uploaded_by=uploaded_by,
            doc_path=doc_path,
        )
        doc_id = document.doc_id

        # 2. Chunk the text
        chunks = chunk_text(raw_text)

        created_chunk_ids = []
        for index, chunk_text_value in enumerate(chunks):
            # 3. Embed the chunk
            embedding = self.embedder.embed_text(chunk_text_value)

            # 4. Insert the Postgres row first — need the real chunk_id before storing in Chroma
            chunk = self.chunk_repo.create_chunk(
                document_id=doc_id,
                chunk_text=chunk_text_value,
                chunk_index=index,
                token_count=len(chunk_text_value.split()),
                embedding_model=self.embedder.model_name,
                embedding_vector_id=None,  # not yet populated — known gap, see project report
            )
            chunk_id = str(chunk.chunk_id)

            # 5. Store the vector in Chroma, using the REAL Postgres chunk_id
            self.retriever.add_chunk(chunk_id, embedding, chunk_text_value, str(doc_id))

            created_chunk_ids.append(chunk_id)

        return {"doc_id": str(doc_id), "chunk_ids": created_chunk_ids}