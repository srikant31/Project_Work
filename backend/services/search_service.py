"""Application search response, backed by the existing retrieval pipeline."""

from time import perf_counter

from backend.services.document_processing_service import DocumentProcessingService


class SearchService:
    def __init__(self, *, session_factory, embedder, retriever):
        self.session_factory, self.embedder, self.retriever = session_factory, embedder, retriever

    def search(self, question, top_k=3):
        question = question.strip()
        if not question:
            raise ValueError("Enter a question")
        started = perf_counter()
        with DocumentProcessingService(session_factory=self.session_factory,
                                       embedder=self.embedder, retriever=self.retriever) as processing:
            hits = processing.search(question, top_k)
        return {"question": question, "hits": hits, "elapsed_ms": round((perf_counter() - started) * 1000, 2),
                "retrieval_only": True}
