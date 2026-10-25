"""Embedding model and tokenizer form one versioned dependency."""

from importlib.metadata import version

from sentence_transformers import SentenceTransformer


class EmbeddingService:
    def __init__(self, model_name: str = "all-MiniLM-L6-v2", batch_size: int = 64, *, local_files_only: bool = True):
        if batch_size <= 0:
            raise ValueError("batch_size must be positive")
        self.model_name = model_name
        self.batch_size = batch_size
        self.model = SentenceTransformer(model_name, local_files_only=local_files_only)

    @property
    def dimension(self) -> int:
        return self.model.get_embedding_dimension()

    @property
    def tokenizer(self):
        return self.model.tokenizer

    @property
    def max_sequence_length(self) -> int:
        # The sentence model can impose a LOWER limit than its tokenizer.
        return self.model.max_seq_length

    @property
    def config(self) -> dict:
        return {
            "model_name": self.model_name,
            "revision": getattr(self.model[0].auto_model.config, "_commit_hash", None),
            "dimension": self.dimension,
            "max_sequence_length": self.max_sequence_length,
            "tokenizer": type(self.tokenizer).__name__,
            "sentence_transformers_version": version("sentence-transformers"),
            "transformers_version": version("transformers"),
        }

    def _validate_lengths(self, texts: list[str]) -> None:
        for index, text in enumerate(texts):
            if not isinstance(text, str) or not text.strip():
                raise ValueError("Embedding text must be nonempty")
            encoded = self.tokenizer(text, add_special_tokens=True, truncation=False, verbose=False)
            if len(encoded["input_ids"]) > self.max_sequence_length:
                raise ValueError(f"Embedding input {index} exceeds model token limit")

    def embed_text(self, text: str) -> list[float]:
        self._validate_lengths([text])
        return self.model.encode(text, show_progress_bar=False).tolist()

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """One forward pass per batch instead of one per chunk. On a 300-chunk
        document this is the difference between minutes and seconds."""
        if not texts:
            return []
        self._validate_lengths(texts)
        return self.model.encode(
            texts,
            batch_size=self.batch_size,
            show_progress_bar=False,
        ).tolist()
