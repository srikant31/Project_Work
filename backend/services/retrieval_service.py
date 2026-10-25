"""Chroma adapter. PostgreSQL decides which document versions are visible."""

import chromadb
from pathlib import Path


class RetrievalService:
    def __init__(self, persist_path="./chroma_db", collection_name="chunks_minilm_v1", default_top_k=5, *, create=True):
        self.collection_name = collection_name
        self.default_top_k = default_top_k
        self._allow_create = create
        if not create and not (Path(persist_path) / "chroma.sqlite3").is_file():
            raise FileNotFoundError("Existing Chroma database required; nothing was created")
        self.client = chromadb.PersistentClient(path=str(persist_path))
        self.collection = self._get_or_create_collection()

    def _get_or_create_collection(self):
        collection = (
            self.client.get_or_create_collection(name=self.collection_name, metadata={"hnsw:space": "cosine"})
            if self._allow_create else self.client.get_collection(self.collection_name)
        )
        if (collection.metadata or {}).get("hnsw:space") != "cosine":
            raise ValueError("Existing collection is not configured for cosine distance")
        return collection

    def recreate_collection(self):
        self.client.delete_collection(self.collection_name)
        self.collection = self._get_or_create_collection()

    def vector_id(self, chunk_id):
        return f"{self.collection_name}:{chunk_id}"

    def add_chunks(self, records, doc_id, is_active=True):
        # Immutable UUIDs make upsert safe to retry after partial writes.
        batch_size = self.client.get_max_batch_size()
        for start in range(0, len(records), batch_size):
            ids, embeddings, texts = zip(*records[start:start + batch_size])
            self.collection.upsert(
                ids=list(ids), embeddings=[list(e) for e in embeddings],
                documents=list(texts),
                metadatas=[{"doc_id": str(doc_id), "is_active": is_active} for _ in ids],
            )

    def get_by_doc(self, doc_id):
        return self.collection.get(where={"doc_id": str(doc_id)}, include=["metadatas"])

    def verify_document(self, doc_id, expected_ids):
        actual = self.get_by_doc(doc_id)
        ids, metadata = actual.get("ids") or [], actual.get("metadatas") or []
        if set(ids) != set(expected_ids) or len(metadata) != len(ids) or not all(
            m.get("doc_id") == str(doc_id) and m.get("is_active") is True for m in metadata
        ):
            raise RuntimeError("Vector verification failed: incomplete IDs or metadata")

    def delete_by_doc(self, doc_id):
        self.collection.delete(where={"doc_id": str(doc_id)})

    def deactivate_doc(self, doc_id):
        ids = self.get_by_doc(doc_id).get("ids") or []
        if ids:
            self.collection.update(ids=ids, metadatas=[{"doc_id": str(doc_id), "is_active": False} for _ in ids])

    def query(self, query_embedding, n_results=None, *, doc_ids=None):
        k = self.default_top_k if n_results is None else n_results
        if not isinstance(k, int) or isinstance(k, bool) or k <= 0:
            raise ValueError("n_results must be a positive integer")
        if doc_ids is not None and not doc_ids:
            return {"ids": [[]], "distances": [[]], "documents": [[]]}
        where = {"is_active": True}
        if doc_ids is not None:
            where = {"$and": [where, {"doc_id": {"$in": [str(d) for d in doc_ids]}}]}
        return self.collection.query(query_embeddings=[query_embedding], n_results=k, where=where)

    def count(self):
        return self.collection.count()
