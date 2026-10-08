import chromadb


class RetrievalService:
    def __init__(
        self,
        persist_path: str = "./chroma_db",
        collection_name: str = "chunks_minilm_v1",
        default_top_k: int = 5,
    ):
        self.collection_name = collection_name
        self.default_top_k = default_top_k
        self.client = chromadb.PersistentClient(path=persist_path)
        self.collection = self._get_or_create_collection()

    def _get_or_create_collection(self):
        """Single source of truth for how the collection is created.
        Anything that (re)builds this collection — __init__, or a full
        reset — MUST go through this method. Duplicating the
        get_or_create_collection() call elsewhere is how the cosine-space
        setting silently drops out, which is exactly what happened in
        reset_stores.py before this fix."""
        return self.client.get_or_create_collection(
            name=self.collection_name,
            metadata={"hnsw:space": "cosine"},
        )

    def recreate_collection(self):
        """Drop and rebuild the collection from scratch, correctly configured.
        Used by scripts/reset_stores.py --all instead of it building the
        collection inline."""
        self.client.delete_collection(self.collection_name)
        self.collection = self._get_or_create_collection()

    def vector_id(self, chunk_id: str) -> str:
        """The value stored in DocumentChunks.embedding_vector_id.
        Namespacing by collection lets several embedding models coexist over
        the same corpus — which EvaluationTestRuns.embedding_model_config
        assumes you'll be doing."""
        return f"{self.collection_name}:{chunk_id}"

    def add_chunks(
        self,
        records: list[tuple[str, list[float], str]],
        doc_id: str,
        is_active: bool = True,
    ) -> None:
        """records = [(chunk_id, embedding, text), ...] — one batched write."""
        if not records:
            return
        ids, embeddings, texts = zip(*records)
        self.collection.add(
            ids=list(ids),
            embeddings=[list(e) for e in embeddings],
            documents=list(texts),
            metadatas=[{"doc_id": doc_id, "is_active": is_active} for _ in ids],
        )

    def delete_by_doc(self, doc_id: str) -> None:
        """Compensating action when a Postgres transaction rolls back after
        vectors were already written."""
        self.collection.delete(where={"doc_id": doc_id})

    def deactivate_doc(self, doc_id: str) -> None:
        """Mirror of ChunkRepository.deactivate_chunks_for_document().
        Both stores must be flipped or retrieval keeps serving stale text."""
        existing = self.collection.get(where={"doc_id": doc_id})
        ids = existing.get("ids") or []
        if not ids:
            return
        self.collection.update(
            ids=ids,
            metadatas=[{"doc_id": doc_id, "is_active": False} for _ in ids],
        )

    def query(self, query_embedding: list[float], n_results: int | None = None):
        """The is_active filter was the missing half of soft-deletion — without
        it, flipping the flag had no effect on what got retrieved."""
        return self.collection.query(
            query_embeddings=[query_embedding],
            n_results=n_results or self.default_top_k,
            where={"is_active": True},
        )

    def count(self) -> int:
        return self.collection.count()