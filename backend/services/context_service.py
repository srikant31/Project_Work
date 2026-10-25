"""Bounded evidence assembly. Byte counts are not claimed to be LLM tokens."""

from dataclasses import dataclass
import json
import math

from backend.prompts.rag_prompts import PROMPT_VERSION, SYSTEM_PROMPT


def serialize_messages(messages):
    return json.dumps(messages, ensure_ascii=False, separators=(",", ":"))


@dataclass(frozen=True)
class BuiltContext:
    sources: list[dict]
    messages: list[dict]
    context_bytes: int
    dropped_sources: int
    context_tokens: None = None
    prompt_version: str = PROMPT_VERSION


class ContextBuilder:
    def __init__(self, max_context_bytes=12000, max_sources=10, min_similarity=None):
        if not isinstance(max_context_bytes, int) or isinstance(max_context_bytes, bool) or not 512 <= max_context_bytes <= 262144:
            raise ValueError("Context byte budget must be between 512 and 262144")
        if not isinstance(max_sources, int) or isinstance(max_sources, bool) or not 1 <= max_sources <= 20:
            raise ValueError("max_sources must be between 1 and 20")
        if min_similarity is not None and (not math.isfinite(min_similarity) or not -1 <= min_similarity <= 1):
            raise ValueError("Invalid similarity threshold")
        self.max_context_bytes, self.max_sources, self.min_similarity = max_context_bytes, max_sources, min_similarity

    @staticmethod
    def _messages(question, sources):
        data = {"question": question, "evidence": [
            {"source_id": source["source_id"], "document": source.get("doc_name"),
             "version": source.get("version"), "page_number": source.get("page_number"),
             "section_heading": source.get("section_heading"), "text": source["text"]}
            for source in sources
        ]}
        return [{"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(data, ensure_ascii=False, separators=(",", ":"))}]

    def build(self, question, hits):
        if not isinstance(question, str) or not question.strip() or len(question) > 4000:
            raise ValueError("Enter a question of 1 to 4000 characters")
        question = question.strip()
        if len(serialize_messages(self._messages(question, [])).encode("utf-8")) > self.max_context_bytes:
            raise ValueError("The question and prompt exceed the configured context byte budget")
        sources, seen = [], set()
        for hit in hits:
            if len(sources) >= self.max_sources:
                break
            text, chunk_id = hit.get("text"), hit.get("chunk_id")
            if not isinstance(text, str) or not text.strip() or not chunk_id or chunk_id in seen:
                continue
            score = hit.get("similarity_score")
            if score is not None and (not isinstance(score, (int, float)) or not math.isfinite(score)):
                continue
            if self.min_similarity is not None and (score is None or score < self.min_similarity):
                continue
            candidate = {**hit, "source_id": f"S{len(sources) + 1}"}
            messages = self._messages(question, [*sources, candidate])
            if len(serialize_messages(messages).encode("utf-8")) > self.max_context_bytes:
                continue  # Keep complete immutable chunks; never truncate a condition.
            sources.append(candidate)
            seen.add(chunk_id)
        messages = self._messages(question, sources)
        return BuiltContext(sources, messages, len(serialize_messages(messages).encode("utf-8")), len(hits) - len(sources))
