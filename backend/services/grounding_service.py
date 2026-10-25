"""Quote provenance validation, not a claim of semantic entailment or immunity."""

from dataclasses import dataclass
import re

from backend.services.llm_service import LLMResponseError

REFUSAL = "I could not find sufficient verified evidence to answer that question. Please clarify the question or ask an administrator to add an appropriate source."


@dataclass(frozen=True)
class GroundingResult:
    answer: str
    status: str
    citations: list[dict]
    validated_quotes: int
    rejected_claims: int
    reason: str


def insufficient_result(reason="insufficient_evidence", rejected_claims=0):
    return GroundingResult(REFUSAL, "insufficient_evidence", [], 0, rejected_claims, reason)


def quote_boundary_offsets(source_text, start_char=0, end_char=None):
    """Sentence/paragraph/table-row boundaries in the FULL immutable source.

    A PDF line wrap is not a semantic boundary. A token-window edge is not a
    semantic boundary either. Return source-derived offsets inside one chunk.
    """
    end_char = len(source_text) if end_char is None else end_char
    starts, ends = {len(source_text) - len(source_text.lstrip())}, {len(source_text.rstrip())}
    for match in re.finditer(r'[.!?](?:["\u201d\u2019\)\]]?)(?=\s|$)', source_text):
        ends.add(match.end())
        following = match.end()
        while following < len(source_text) and source_text[following].isspace():
            following += 1
        starts.add(following)
    for match in re.finditer(r"\n[ \t]*\n", source_text):
        preceding = match.start()
        while preceding > 0 and source_text[preceding - 1].isspace():
            preceding -= 1
        ends.add(preceding)
        following = match.end()
        while following < len(source_text) and source_text[following].isspace():
            following += 1
        starts.add(following)
    position = 0
    for line in source_text.splitlines(keepends=True):
        if line.count("|") >= 2:  # Structured DOCX/TXT table rows are full units.
            starts.add(position + len(line) - len(line.lstrip()))
            ends.add(position + len(line.rstrip()))
        position += len(line)
    return {
        "quote_start_offsets": sorted(offset - start_char for offset in starts if start_char <= offset <= end_char),
        "quote_end_offsets": sorted(offset - start_char for offset in ends if start_char <= offset <= end_char),
    }


def _complete_quote_offset(text, quote, boundary_metadata=None):
    """Reject fragments that can omit negations/conditions within a source unit."""
    boundaries = boundary_metadata if boundary_metadata is not None else quote_boundary_offsets(text)
    starts, ends = set(boundaries["quote_start_offsets"]), set(boundaries["quote_end_offsets"])
    offset = text.find(quote)
    while offset >= 0:
        if offset in starts and offset + len(quote) in ends:
            return offset
        offset = text.find(quote, offset + 1)
    return None


def validate_claims(payload, context):
    if not isinstance(payload, dict) or not isinstance(payload.get("insufficient_evidence"), bool) or not isinstance(payload.get("claims"), list):
        raise LLMResponseError("The LLM response did not match the required evidence-selection format")
    claims = payload["claims"]
    if len(claims) > 8:
        raise LLMResponseError("The LLM returned too many claims")
    if payload["insufficient_evidence"]:
        return insufficient_result()
    sources = {source["source_id"]: source for source in context.sources}
    citations, rejected, seen = [], 0, set()
    for claim in claims:
        if not isinstance(claim, dict):
            rejected += 1
            continue
        source_id, quote = claim.get("source_id"), claim.get("quote")
        source = sources.get(source_id) if isinstance(source_id, str) else None
        if source is None or not isinstance(quote, str) or not 8 <= len(quote.strip()) <= 3000:
            rejected += 1
            continue
        quote = quote.strip()
        metadata = source if "quote_start_offsets" in source and "quote_end_offsets" in source else None
        offset = _complete_quote_offset(source["text"], quote, metadata)
        if offset is None or (source_id, quote) in seen:
            rejected += 1
            continue
        # Quoted procedural instructions are still data. Do not present obvious
        # attempts to control the assistant as an answer to a helpdesk question.
        if re.search(r"(?i)ignore\s+(?:all\s+)?(?:previous|system|above)\s+instructions|reveal\s+(?:the\s+)?system\s+prompt", quote):
            rejected += 1
            continue
        seen.add((source_id, quote))
        base = source.get("start_char")
        start = base + offset if isinstance(base, int) and not isinstance(base, bool) else None
        citations.append({key: source.get(key) for key in (
            "source_id", "doc_id", "doc_name", "version", "source_hash", "chunk_id", "page_number", "section_heading")}
            | {"quote": quote, "start_char": start, "end_char": start + len(quote) if start is not None else None})
    if not citations:
        return insufficient_result("no_validated_quotes", rejected)
    answer = "\n\n".join(f'{citation["quote"]} [{citation["source_id"]}]' for citation in citations)
    return GroundingResult(answer, "answered", citations, len(citations), rejected, "verified_source_quotes")
