import chromadb


class RetrievalService:
    def __init__(self, persist_path: str = "./chroma_db", collection_name: str = "chunks_minilm_v1"):
        self.client = chromadb.PersistentClient(path=persist_path)
        self.collection = self.client.get_or_create_collection(name=collection_name)

    def add_chunk(self, chunk_id: str, embedding: list[float], text: str, doc_id: str, is_active: bool = True):
        self.collection.add(
            ids=[chunk_id],
            embeddings=[embedding],
            documents=[text],
            metadatas=[{"doc_id": doc_id, "is_active": is_active}],
        )

    def query(self, query_embedding: list[float], n_results: int = 2):
        return self.collection.query(query_embeddings=[query_embedding], n_results=n_results)