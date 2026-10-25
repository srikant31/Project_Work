"""Compare retrieval settings on a frozen corpus without publishing any index.

The real parser, tokenizer, chunker and embedding model are reused. Cosine
retrieval lives only in memory; neither live PostgreSQL rows nor Chroma change.
No LLM or feedback rating contributes to these metrics.
"""

from dataclasses import asdict
from datetime import UTC, datetime
import hashlib
import json
from time import perf_counter
import uuid

import numpy as np

from backend.models.experiment_models import ExperimentQuestion, ExperimentRunRequest
from backend.repositories.experiment_repository import ExperimentRepository
from backend.utils.chunking import CHUNKER_VERSION, chunk_document
from backend.utils.loaders import load_document


PRESETS = [
    {"chunk_size": 100, "overlap": 20, "top_k": 3},
    {"chunk_size": 150, "overlap": 30, "top_k": 3},
    {"chunk_size": 200, "overlap": 40, "top_k": 3},
    {"chunk_size": 200, "overlap": 40, "top_k": 5},
]
MAX_SOURCE_CHARACTERS = 1_000_000
MAX_CHUNKS = 12_000


def _normalized_with_offsets(text):
    characters, offsets = [], []
    for index, character in enumerate(text):
        if character.isspace():
            if characters and characters[-1] != " ":
                characters.append(" ")
                offsets.append(index)
        else:
            characters.append(character)
            offsets.append(index)
    if characters and characters[-1] == " ":
        characters.pop()
        offsets.pop()
    return "".join(characters), offsets


def evidence_occurrences(source_text, quote):
    """Find exact, whitespace-normalized gold evidence in immutable source text."""
    normalized, offsets = _normalized_with_offsets(source_text)
    expected = " ".join(quote.split())
    spans, cursor = [], 0
    while expected and (position := normalized.find(expected, cursor)) >= 0:
        spans.append((offsets[position], offsets[position + len(expected) - 1] + 1))
        cursor = position + 1
    return spans


def evidence_is_covered(source_text, occurrences, hits):
    """Allow adjacent/overlapping top-K source spans to cover one gold occurrence."""
    intervals = sorted((hit["start_char"], hit["end_char"]) for hit in hits)
    for start, end in occurrences:
        if all(
            source_text[index].isspace() or any(left <= index < right for left, right in intervals)
            for index in range(start, end)
        ):
            return True
    return False


def score_question(question, hits, expected_doc_id, *, source_text=None, occurrences=()):
    """Gold labels score evidence/source coverage, never a generated answer."""
    if not question.answerable:
        return {"source_hit": None, "evidence_hit": None, "reciprocal_rank": None,
                "diagnostic": "Unsupported diagnostic: nearest passages are not an answer or a refusal test."}
    relevant = [hit for hit in hits if hit["doc_id"] == expected_doc_id]
    first_rank = min((hit["rank"] for hit in relevant), default=None)
    return {
        "source_hit": bool(relevant),
        "evidence_hit": evidence_is_covered(source_text, occurrences, relevant)
                        if question.evidence_quote is not None else None,
        "reciprocal_rank": 1.0 / first_rank if first_rank else 0.0,
    }


def aggregate_metrics(questions):
    answerable = [row for row in questions if row["answerable"]]
    evidence_rows = [row for row in answerable if row["evidence_hit"] is not None]
    return {
        "answerable_count": len(answerable), "evidence_question_count": len(evidence_rows),
        "unsupported_count": len(questions) - len(answerable),
        "source_hit_rate": sum(row["source_hit"] for row in answerable) / len(answerable),
        "evidence_hit_rate": sum(row["evidence_hit"] for row in evidence_rows) / len(evidence_rows)
                             if evidence_rows else None,
        "mean_reciprocal_rank": sum(row["reciprocal_rank"] for row in answerable) / len(answerable),
        "mean_latency_ms": sum(row["latency_ms"] for row in answerable) / len(answerable),
    }


def normalized_embedding_matrix(vectors, expected_count, dimension):
    matrix = np.asarray(vectors, dtype=np.float64)
    if matrix.shape != (expected_count, dimension) or not np.isfinite(matrix).all():
        raise ValueError("Experiment embeddings have invalid dimensions or nonfinite values")
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    if np.any(norms == 0) or not np.isfinite(norms).all():
        raise ValueError("Experiment embeddings must have a finite nonzero norm")
    return matrix / norms


def cosine_hits(passages, matrix, query_vector, top_k):
    scores = matrix @ query_vector
    indices = np.argsort(-scores, kind="stable")[:top_k]
    return [{**passages[int(index)], "rank": rank,
             "similarity_score": float(np.clip(scores[index], -1.0, 1.0))}
            for rank, index in enumerate(indices, start=1)]


class ExperimentService:
    def __init__(self, *, session_factory, storage, embedder=None, output_root=None):
        self.repository = ExperimentRepository(session_factory, output_root)
        self.storage, self.embedder = storage, embedder

    def config(self):
        documents = self.repository.eligible_documents()
        public = [{key: row[key] for key in ("doc_id", "doc_name", "source_key", "version", "source_hash")}
                  for row in documents]
        first = documents[0] if documents else None
        return {
            "documents": public, "presets": PRESETS,
            "dataset_schema": {"type": "array", "items": ExperimentQuestion.model_json_schema()},
            "example": [
                {"question_id": "gold-001", "question": "Replace with a question answered by this source.",
                 "expected_doc_id": first["doc_id"] if first else None,
                 "expected_source_key": first["source_key"] if first else "policy.txt",
                 "evidence_quote": None, "answerable": True},
                {"question_id": "unsupported-001", "question": "Replace with a question absent from the selected sources.",
                 "expected_doc_id": None, "expected_source_key": None,
                 "evidence_quote": None, "answerable": False},
            ],
            "limits": {"documents": 10, "questions": 100, "configurations": 12,
                       "source_characters": MAX_SOURCE_CHARACTERS, "chunks": MAX_CHUNKS},
        }

    def get(self, run_id):
        return self.repository.get(run_id)

    def _corpus(self, document_ids):
        rows = self.repository.eligible_documents(document_ids, include_source=True)
        if {row["doc_id"] for row in rows} != {str(identifier) for identifier in document_ids}:
            raise ValueError("Select only current online documents with complete managed-source provenance")
        if sum(len(row["extracted_text"]) for row in rows) > MAX_SOURCE_CHARACTERS:
            raise ValueError("The experiment corpus is too large; select fewer or smaller documents")
        prepared = []
        for row in sorted(rows, key=lambda item: item["doc_id"]):
            # Reparse the approved source instead of rebuilding a PDF from flat
            # database text: this preserves real page and section boundaries.
            path = self.storage.resolve_source(row["doc_path"])
            loaded = load_document(path)
            text_hash = hashlib.sha256(loaded.source_text.encode("utf-8")).hexdigest()
            if (loaded.doc_type != row["doc_type"] or loaded.source_hash != row["source_hash"]
                    or text_hash != row["text_hash"] or loaded.source_text != row["extracted_text"]):
                raise ValueError("A selected source has changed since indexing; replace or reindex it before experimenting")
            snapshot = {key: row[key] for key in (
                "doc_id", "doc_name", "source_key", "version", "source_hash", "text_hash", "doc_type"
            )}
            snapshot.update({"source_characters": len(loaded.source_text), "page_count": loaded.page_count,
                             "extractor_version": loaded.extractor_version})
            prepared.append({"snapshot": snapshot, "loaded": loaded})
        return prepared

    @staticmethod
    def _labels(questions, corpus):
        documents = {item["snapshot"]["doc_id"]: item for item in corpus}
        labels = []
        for question in questions:
            expected_id, occurrences, source_text = None, [], None
            if question.answerable:
                if question.expected_doc_id is not None:
                    expected_id = str(question.expected_doc_id)
                    selected = documents[expected_id]
                    if (question.expected_source_key is not None
                            and selected["snapshot"]["source_key"] != question.expected_source_key):
                        raise ValueError(f"Question {question.question_id}: expected ID and source key disagree")
                else:
                    matches = [item for item in corpus if item["snapshot"]["source_key"] == question.expected_source_key]
                    if len(matches) != 1:
                        raise ValueError(f"Question {question.question_id}: source key is absent or ambiguous; use the document ID")
                    selected = matches[0]
                    expected_id = selected["snapshot"]["doc_id"]
                source_text = selected["loaded"].source_text
                if question.evidence_quote is not None:
                    occurrences = evidence_occurrences(source_text, question.evidence_quote)
                    if not occurrences:
                        raise ValueError(f"Question {question.question_id}: the gold evidence quote is absent from its expected source")
            labels.append({"question": question, "expected_doc_id": expected_id,
                           "source_text": source_text, "occurrences": occurrences})
        return labels

    def _index(self, corpus, configuration):
        started = perf_counter()
        passages = []
        for item in corpus:
            snapshot = item["snapshot"]
            chunks = chunk_document(item["loaded"], self.embedder.tokenizer, self.embedder.max_sequence_length,
                                    chunk_size=configuration.chunk_size, overlap=configuration.overlap)
            if not chunks:
                raise ValueError("A selected source produced no chunks under this configuration")
            for chunk in chunks:
                passages.append({**{key: snapshot[key] for key in ("doc_id", "doc_name", "source_key", "version", "source_hash")},
                                 "chunk_index": chunk.index, **asdict(chunk)})
            if len(passages) > MAX_CHUNKS:
                raise ValueError("This configuration creates too many chunks; increase chunk size or reduce overlap")
        vectors = self.embedder.embed_batch([row["text"] for row in passages])
        matrix = normalized_embedding_matrix(vectors, len(passages), self.embedder.dimension)
        return passages, matrix, (perf_counter() - started) * 1000

    def run(self, request):
        request = ExperimentRunRequest.model_validate(request)
        if self.embedder is None:
            raise RuntimeError("The experiment embedding model is unavailable")
        special = self.embedder.tokenizer.num_special_tokens_to_add(pair=False)
        maximum = self.embedder.max_sequence_length - special
        if any(configuration.chunk_size > maximum for configuration in request.configurations):
            raise ValueError(f"The current embedding model permits at most {maximum} payload tokens per chunk")
        corpus = self._corpus(request.document_ids)
        labels = self._labels(request.questions, corpus)
        question_vectors, embedding_times = [], []
        for label in labels:
            started = perf_counter()
            vector = self.embedder.embed_text(label["question"].question)
            question_vectors.append(normalized_embedding_matrix([vector], 1, self.embedder.dimension)[0])
            embedding_times.append((perf_counter() - started) * 1000)
        warnings = [warning for item in corpus for warning in item["loaded"].warnings]
        warnings.append("Rankings compare only the selected document snapshots; live search can differ when other online sources are present.")
        if any(question.answerable and question.evidence_quote is None for question in request.questions):
            warnings.append("Questions without an evidence quote are scored for source retrieval only and excluded from the evidence-hit denominator.")
        if any(not question.answerable for question in request.questions):
            warnings.append("Unsupported questions are diagnostics only; nearest passages do not prove safe answer refusal.")
        results, cached = [], {}
        for configuration in request.configurations:
            key = (configuration.chunk_size, configuration.overlap)
            reused = key in cached
            if not reused:
                cached[key] = self._index(corpus, configuration)
            passages, matrix, index_ms = cached[key]
            evaluated = []
            for label, vector, embedding_ms in zip(labels, question_vectors, embedding_times, strict=True):
                started = perf_counter()
                hits = cosine_hits(passages, matrix, vector, configuration.top_k)
                query_latency_ms = (perf_counter() - started) * 1000 + embedding_ms
                score = score_question(label["question"], hits, label["expected_doc_id"],
                                       source_text=label["source_text"], occurrences=label["occurrences"])
                question = label["question"]
                expected = next((item["snapshot"] for item in corpus if item["snapshot"]["doc_id"] == label["expected_doc_id"]), None)
                evaluated.append({
                    "question_id": question.question_id, "question": question.question,
                    "answerable": question.answerable, "expected_doc_id": label["expected_doc_id"],
                    "resolved_source_key": expected["source_key"] if expected else None,
                    "evidence_quote": question.evidence_quote, "hits": hits,
                    "latency_ms": query_latency_ms, **score,
                })
            results.append({**configuration.model_dump(), "chunk_count": len(passages),
                            "index_build_ms": index_ms, "reused_index": reused,
                            **aggregate_metrics(evaluated), "questions": evaluated})
        snapshots = [item["snapshot"] for item in corpus]
        report = {
            "run_id": str(uuid.uuid4()), "status": "completed", "created_at": datetime.now(UTC).isoformat(),
            "corpus": snapshots,
            "corpus_fingerprint": hashlib.sha256(json.dumps(snapshots, sort_keys=True).encode()).hexdigest(),
            "embedding_configuration": self.embedder.config, "chunker_version": CHUNKER_VERSION,
            "dataset": [question.model_dump(mode="json") for question in request.questions],
            "results": results, "warnings": list(dict.fromkeys(warnings)),
            "retrieval_only": True,
            "metric_notes": {
                "retrieval_backend": "Exact normalized cosine in memory; production Chroma indexing and tie order may differ.",
                "source_hit_rate": "Expected source found within the selected top K; denominator is answerable questions.",
                "evidence_hit_rate": "A full authored quote is covered by expected-source top-K offsets; denominator is answerable questions with a quote.",
                "mean_reciprocal_rank": "Inverse rank of the first expected-source hit, averaged over answerable questions.",
                "mean_latency_ms": "Question embedding time measured once plus exact cosine ranking; averaged over answerable questions. Gold grading and index building are excluded; index building is reported separately.",
            },
            "selection_note": "No configuration was applied automatically. Compare evidence coverage, source hits, rank, latency and chunk count; explicitly reindex a document to apply a chosen setting. These results measure retrieval, not generated-answer quality.",
        }
        self.repository.save(report)
        return report
