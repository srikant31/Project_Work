from sentence_transformers import SentenceTransformer


class EmbeddingService:
    def __init__(self, model_name: str = "all-MiniLM-L6-v2", batch_size: int = 64):
        self.model_name = model_name
        self.batch_size = batch_size
        self.model = SentenceTransformer(model_name)

    @property
    def dimension(self) -> int:
        return self.model.get_sentence_embedding_dimension()

    def embed_text(self, text: str) -> list[float]:
        return self.model.encode(text, show_progress_bar=False).tolist()

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """One forward pass per batch instead of one per chunk. On a 300-chunk
        document this is the difference between minutes and seconds."""
        if not texts:
            return []
        return self.model.encode(
            texts,
            batch_size=self.batch_size,
            show_progress_bar=False,
        ).tolist()
