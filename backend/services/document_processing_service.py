import traceback

from backend.database.connection import SessionLocal
from backend.repositories.category_repository import CategoryRepository
from backend.repositories.chunk_repository import ChunkRepository
from backend.repositories.document_repository import DocumentRepository
from backend.repositories.ingestion_job_repository import IngestionJobRepository
from backend.services.embedding_service import EmbeddingService
from backend.services.retrieval_service import RetrievalService
from backend.utils.chunking import Chunk, chunk_text


class DocumentProcessingService:
    """Owns the transaction. Repositories only flush.

    Two transactions, deliberately:

      Txn 1 — document row + IngestionJob('processing'), committed immediately.
              Committing here is what makes failure logging possible at all:
              IngestionJobs has an FK to Documents, so if the document row were
              rolled back there'd be nothing for the failure record to point at.

      Txn 2 — chunk rows + vector writes. Vectors go in BEFORE the commit, so a
              Chroma failure rolls Postgres back and leaves zero orphans. If the
              commit itself fails after the vectors landed, the except branch
              deletes them.
    """

    def __init__(
        self,
        db_session=None,
        embedder: EmbeddingService | None = None,
        retriever: RetrievalService | None = None,
    ):
        self.session = db_session or SessionLocal()
        self._owns_session = db_session is None
        self.embedder = embedder or EmbeddingService()
        self.retriever = retriever or RetrievalService()
        self.doc_repo = DocumentRepository(self.session)
        self.chunk_repo = ChunkRepository(self.session)
        self.job_repo = IngestionJobRepository(self.session)
        self.category_repo = CategoryRepository(self.session)

    def close(self):
        if self._owns_session:
            self.session.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False

    # ------------------------------------------------------------------
    def process_document(
        self,
        doc_name: str,
        doc_type: str,
        uploaded_by,
        doc_path: str,
        raw_text: str = None,
        pages: list[str] = None,
        category_id=None,
        category_name: str = None,
        chunk_size: int = 200,
        overlap: int = 40,
    ) -> dict:
        # Exactly one of category_id / category_name must be given.
        # category_name is the convenient path for scripts/callers who
        # think in names, not UUIDs; it's resolved to a real category_id
        # via CategoryRepository before anything is written.
        if category_id is None and category_name is None:
            raise ValueError(
                "category_id or category_name is required — every document "
                "must belong to a category (see migration 002)."
            )
        if category_id is None:
            category_id = self.category_repo.require_by_name(category_name).category_id
        if not raw_text and not pages:
            raise ValueError("Provide either raw_text or pages")

        # ---------- build chunks (pure, no I/O) ----------
        if pages:
            from backend.utils.chunking import chunk_pages

            chunks: list[Chunk] = chunk_pages(pages, chunk_size, overlap)
            page_count = len(pages)
            source_len = sum(len(p) for p in pages)
        else:
            chunks = chunk_text(raw_text, chunk_size, overlap)
            page_count = None
            source_len = len(raw_text)

        if not chunks:
            raise ValueError(f"'{doc_name}' produced zero chunks — empty document?")

        # ---------- TXN 1: document + job, committed ----------
        document = self.doc_repo.create_document(
            doc_name=doc_name,
            doc_type=doc_type,
            uploaded_by=uploaded_by,
            doc_path=doc_path,
            category_id=category_id,
            page_count=page_count,
            doc_size=source_len,
        )
        doc_id = document.doc_id
        job = self.job_repo.start_job(doc_id)
        self.session.commit()

        # ---------- TXN 2: chunks + vectors ----------
        try:
            # One batched forward pass, not one per chunk.
            embeddings = self.embedder.embed_batch([c.text for c in chunks])

            pending: list[tuple[str, list[float], str]] = []
            chunk_ids: list[str] = []

            for chunk, embedding in zip(chunks, embeddings):
                row = self.chunk_repo.create_chunk(
                    document_id=doc_id,
                    chunk_text=chunk.text,
                    chunk_index=chunk.index,
                    token_count=chunk.token_count,
                    embedding_model=self.embedder.model_name,
                    page_number=chunk.page_number,
                    section_heading=chunk.section_heading,
                )
                cid = str(row.chunk_id)
                # Now populated for real, instead of hardcoded None.
                row.embedding_vector_id = self.retriever.vector_id(cid)
                pending.append((cid, embedding, chunk.text))
                chunk_ids.append(cid)

            self.session.flush()
            self.retriever.add_chunks(pending, str(doc_id))
            self.session.commit()

            self.job_repo.mark_completed(job)
            self.session.commit()

        except Exception:
            self.session.rollback()
            # Compensating action: vectors may have landed before the failure.
            try:
                self.retriever.delete_by_doc(str(doc_id))
            except Exception:
                pass
            IngestionJobRepository.record_failure(job.id, doc_id, traceback.format_exc())
            raise

        return {
            "doc_id": str(doc_id),
            "chunk_ids": chunk_ids,
            "chunk_count": len(chunk_ids),
        }

    # ------------------------------------------------------------------
    def reingest_document(self, doc_name: str, **kwargs) -> dict:
        """Re-upload path. Without this, re-ingesting a document silently
        doubles the corpus and retrieval starts mixing old and new text."""
        previous = self.doc_repo.get_latest_version_by_name(doc_name)
        new_version = 1

        if previous:
            self.chunk_repo.deactivate_chunks_for_document(previous.doc_id)
            self.doc_repo.deactivate(previous)
            self.session.commit()
            self.retriever.deactivate_doc(str(previous.doc_id))
            new_version = previous.version + 1

        result = self.process_document(doc_name=doc_name, **kwargs)

        if new_version > 1:
            doc = self.doc_repo.get_document_by_id(result["doc_id"])
            doc.version = new_version
            self.session.commit()

        result["version"] = new_version
        return result

    # ------------------------------------------------------------------
    def search(self, question: str, top_k: int | None = None) -> list[dict]:
        """Retrieval only — no LLM yet. Chroma finds the IDs, Postgres supplies
        the authoritative metadata (page, heading, parent doc)."""
        q_vec = self.embedder.embed_text(question)
        res = self.retriever.query(q_vec, n_results=top_k)

        ids = (res.get("ids") or [[]])[0]
        distances = (res.get("distances") or [[]])[0]
        if not ids:
            return []

        rows = {str(r.chunk_id): r for r in self.chunk_repo.get_chunks_by_ids(ids)}

        out = []
        for rank, (cid, dist) in enumerate(zip(ids, distances), start=1):
            row = rows.get(cid)
            out.append(
                {
                    "rank": rank,
                    "chunk_id": cid,
                    "similarity_score": 1.0 - dist,  # cosine distance -> similarity
                    "text": row.chunk_text if row else None,
                    "page_number": row.page_number if row else None,
                    "section_heading": row.section_heading if row else None,
                    "doc_id": str(row.document_id) if row else None,
                }
            )
        return out
