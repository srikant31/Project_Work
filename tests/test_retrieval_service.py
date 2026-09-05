from backend.services.embedding_service import EmbeddingService
from backend.services.retrieval_service import RetrievalService


def test_semantic_retrieval():
    embedder = EmbeddingService()
    retriever = RetrievalService()

    sample_chunks = [
        {"chunk_id": "chunk-001", "doc_id": "doc-abc", "text": "Employees can request annual leave through the HR portal."},
        {"chunk_id": "chunk-002", "doc_id": "doc-abc", "text": "VPN access requires a company-issued laptop and MFA setup."},
        {"chunk_id": "chunk-003", "doc_id": "doc-xyz", "text": "Expense reports must be submitted within 30 days of purchase."},
    ]
    for chunk in sample_chunks:
        embedding = embedder.embed_text(chunk["text"])
        retriever.add_chunk(chunk["chunk_id"], embedding, chunk["text"], chunk["doc_id"])

    query_embedding = embedder.embed_text("How do I take time off work?")
    results = retriever.query(query_embedding, n_results=1)

    assert "annual leave" in results["documents"][0][0]