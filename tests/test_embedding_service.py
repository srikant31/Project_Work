from backend.services.embedding_service import EmbeddingService


def test_embedding_shape():
    service = EmbeddingService()
    embedding = service.embed_text("This is a test sentence.")
    assert len(embedding) == 384