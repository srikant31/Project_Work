"""Quote provenance validation, not a claim of semantic entailment or immunity."""

from dataclasses import dataclass
import os
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
    # Why each rejected claim failed (diagnostics only; never shown as an answer).
    rejected_details: tuple = ()
    # RAG_ANSWER_STYLE=sentence: what was shown, the model's sentence, and why
    # it was accepted or replaced by the verified quotes.
    answer_style: str = "quotes"
    sentence: str | None = None
    sentence_check: str | None = None


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
    lines, position = [], 0
    for line in source_text.splitlines(keepends=True):
        lines.append((position, line))
        position += len(line)
    for index, (position, line) in enumerate(lines):
        # 2+ pipes: Markdown/PDF rows (|a|b|) and 3+ column DOCX rows (a | b | c).
        # One " | ": a 2-column DOCX row (a | b). It counts only beside another
        # " | " line, so a lone pipe inside wrapped prose never becomes a unit.
        docx_pair_row = " | " in line and any(
            0 <= other < len(lines) and " | " in lines[other][1] for other in (index - 1, index + 1))
        if line.count("|") >= 2 or docx_pair_row:
            starts.add(position + len(line) - len(line.lstrip()))
            ends.add(position + len(line.rstrip()))
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


# Characters a model often drops or adds when copying Markdown/PDF tables and
# wrapped text. They carry no wording, so ignoring them cannot change meaning.
_FORMATTING = re.compile(r"<br\s*/?>|[\s|*]+", re.IGNORECASE)


def _formatting_free(text):
    """Text without formatting characters, plus each kept char's original index."""
    kept, index, position = [], [], 0
    for match in _FORMATTING.finditer(text):
        for i in range(position, match.start()):
            kept.append(text[i]); index.append(i)
        position = match.end()
    for i in range(position, len(text)):
        kept.append(text[i]); index.append(i)
    return "".join(kept), index


def _tolerant_quote_span(text, quote, boundary_metadata=None):
    """Find a quote that differs from the source only in spaces, '|', '*' or <br>.

    Returns (start, end) of the matching EXACT source slice, snapped outward
    across formatting characters to the enclosing sentence/row boundaries, or
    None. The caller cites text[start:end], never the model's own copy, so the
    citation stays an exact source slice and fragments are still rejected.
    """
    boundaries = boundary_metadata if boundary_metadata is not None else quote_boundary_offsets(text)
    starts, ends = set(boundaries["quote_start_offsets"]), set(boundaries["quote_end_offsets"])
    plain_text, index = _formatting_free(text)
    plain_quote, _ = _formatting_free(quote)
    if len(plain_quote) < 8:
        return None
    found = plain_text.find(plain_quote)
    while found >= 0:
        start, end = index[found], index[found + len(plain_quote) - 1] + 1
        # Snap across formatting-only characters (e.g. a row's outer pipes).
        while start not in starts and start > 0 and _FORMATTING.fullmatch(text[start - 1]):
            start -= 1
        while end not in ends and end < len(text) and _FORMATTING.fullmatch(text[end]):
            end += 1
        if start in starts and end in ends:
            return start, end
        found = plain_text.find(plain_quote, found + 1)
    return None


def quote_match_mode():
    """RAG_QUOTE_MATCH=exact (default, current behaviour) or tolerant."""
    mode = os.getenv("RAG_QUOTE_MATCH", "exact").strip().lower() or "exact"
    if mode not in {"exact", "tolerant"}:
        raise ValueError("RAG_QUOTE_MATCH must be exact or tolerant")
    return mode


_INSTRUCTION_LIKE = re.compile(
    r"(?i)ignore\s+(?:all\s+)?(?:previous|system|above)\s+instructions|reveal\s+(?:the\s+)?system\s+prompt")

# Spelled-out numbers become digits so "five" and "5" compare equal. "one" is
# left alone: "one-time", "no one" and "at least one" are rarely the fact.
_NUMBER_WORDS = {"two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
                 "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
                 "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19,
                 "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "ninety": 90}
_NUMBER_WORD = re.compile(r"\b(" + "|".join(_NUMBER_WORDS) + r")\b")
_CODE = re.compile(r"\b[a-z]{1,6}-?\d+\b")          # auth-403, sev-1, p2, it-005
_NUMBER = re.compile(r"\d+(?:\.\d+)?")
_NEGATION = re.compile(r"\b(?:not|no|none|never|cannot|prohibited|forbidden)\b|n't\b")
_SOURCE_TAG = re.compile(r"\s*\[S\d+\]")
# Models often write U+2011 (non-breaking hyphen) or another dash look-alike,
# e.g. "IT\u2011005" or "ABC CORP\u2011managed". Read them as "-" so codes stay
# codes, and show the user a plain hyphen.
_HYPHENS = str.maketrans({"\u2010": "-", "\u2011": "-", "\u2012": "-", "\u2013": "-",
                          "\u2014": "-", "\u2212": "-", "\u00ad": None})


def _facts(text):
    """(codes, numbers) in text, normalised so formatting cannot hide a change."""
    text = (text or "").translate(_HYPHENS).lower().replace("’", "'")
    text = _NUMBER_WORD.sub(lambda m: str(_NUMBER_WORDS[m.group(1)]), text)
    text = re.sub(r"(?<=\d),(?=\d{3}\b)", "", text)          # 1,000 -> 1000
    codes = set(_CODE.findall(text))
    plain = _CODE.sub(" ", text)                               # a code's digits are not a number
    numbers = {str(float(n)).rstrip("0").rstrip(".") for n in _NUMBER.findall(plain)}
    return codes, numbers


_WORD = re.compile(r"[a-z]{4,}")


def _drops_a_negation(sentence, evidence):
    """True if the sentence restates negated wording without the negation.

    "a receipt is not required" -> "a receipt is required" is caught: two
    content words next to the "not" reappear in a sentence with no negation.
    A negation about something the sentence does not mention ("If a user
    cannot wait:") is ignored, so it does not block a faithful answer.
    """
    sentence_words = set(_WORD.findall(sentence.lower()))
    words = re.findall(r"[a-z']+", evidence.lower().replace("’", "'"))
    for index, word in enumerate(words):
        if _NEGATION.fullmatch(word) or word.endswith("n't"):
            nearby = {w for w in words[max(0, index - 3):index + 4] if _WORD.fullmatch(w)}
            if len(nearby & sentence_words) >= 2:
                return True
    return False


def check_sentence(sentence, citations):
    """Accept the model's plain-English answer only if it adds no facts.

    Returns (cleaned_sentence, "accepted") or (None, reason). Checked against
    the VERIFIED quotes only, never the question, so a false premise in the
    question ("the $1000 allowance") cannot slip into the answer. This checks
    numbers, codes and negations; it does not prove the wording is entailed.
    """
    if not isinstance(sentence, str) or not sentence.strip():
        return None, "no_sentence"
    sentence = _SOURCE_TAG.sub("", sentence).replace("\u2011", "-").replace("\u00ad", "").strip()
    if not 3 <= len(sentence) <= 600:
        return None, "sentence_too_short_or_long"
    if _INSTRUCTION_LIKE.search(sentence):
        return None, "instruction_like"
    evidence = " ".join(c["quote"] for c in citations)
    names = " ".join(str(c.get("doc_name") or "").replace("_", " ") for c in citations)
    codes, numbers = _facts(sentence)
    evidence_codes, evidence_numbers = _facts(evidence)
    evidence_codes |= _facts(names)[0]                         # "IT-005 says..." is allowed
    if codes - evidence_codes:
        return None, "code_not_in_quotes: " + ", ".join(sorted(codes - evidence_codes))
    if numbers - evidence_numbers:
        return None, "number_not_in_quotes: " + ", ".join(sorted(numbers - evidence_numbers))
    said, stated = bool(_NEGATION.search(sentence.lower())), bool(_NEGATION.search(evidence.lower()))
    if said and not stated:
        return None, "negation_added"
    if not said and _drops_a_negation(sentence, evidence):
        return None, "negation_dropped"
    # With several cited documents, each one that states a number must have
    # one of its numbers in the sentence, so a conflict is never answered from
    # one side only. With one document the sentence may answer just the part
    # asked about ("no approval needed" without the delivery time).
    by_document = {}
    for citation in citations:
        by_document.setdefault(citation.get("doc_id"), []).append(citation["quote"])
    for doc_id, quotes in (by_document.items() if len(by_document) > 1 else ()):
        stated_numbers = _facts(" ".join(quotes))[1]
        if stated_numbers and not stated_numbers & numbers:
            return None, "a_cited_source_value_is_missing"
    return sentence, "accepted"


def validate_claims(payload, context, answer_style=None):
    if not isinstance(payload, dict) or not isinstance(payload.get("insufficient_evidence"), bool) or not isinstance(payload.get("claims"), list):
        raise LLMResponseError("The LLM response did not match the required evidence-selection format")
    claims = payload["claims"]
    if len(claims) > 8:
        raise LLMResponseError("The LLM returned too many claims")
    if payload["insufficient_evidence"]:
        return insufficient_result()
    sources = {source["source_id"]: source for source in context.sources}
    tolerant = quote_match_mode() == "tolerant"
    citations, rejected, seen, details = [], 0, set(), []

    def reject(why, source_id=None, quote=None):
        details.append({"source_id": source_id if isinstance(source_id, str) else None,
                        "quote": quote[:300] if isinstance(quote, str) else None, "why": why})
        return 1

    for claim in claims:
        if not isinstance(claim, dict):
            rejected += reject("not_an_object")
            continue
        source_id, quote = claim.get("source_id"), claim.get("quote")
        source = sources.get(source_id) if isinstance(source_id, str) else None
        if source is None:
            rejected += reject("unknown_source", source_id, quote)
            continue
        if not isinstance(quote, str) or not 8 <= len(quote.strip()) <= 3000:
            rejected += reject("too_short_or_long", source_id, quote)
            continue
        quote = quote.strip()
        metadata = source if "quote_start_offsets" in source and "quote_end_offsets" in source else None
        offset = _complete_quote_offset(source["text"], quote, metadata)
        if offset is None and tolerant:
            span = _tolerant_quote_span(source["text"], quote, metadata)
            if span is not None:
                offset, quote = span[0], source["text"][span[0]:span[1]]  # cite the exact source slice
        if offset is None:
            # formatting_differs_only: tolerant mode would accept it as a whole unit.
            # not_a_complete_sentence_or_row: the words are in the source, but only
            # as part of a sentence or row (a fragment). not_found_in_source: the
            # words themselves differ from the source.
            if _tolerant_quote_span(source["text"], quote, metadata) is not None:
                why = "formatting_differs_only"
            elif _formatting_free(quote)[0] in _formatting_free(source["text"])[0]:
                why = "not_a_complete_sentence_or_row"
            else:
                why = "not_found_in_source"
            rejected += reject(why, source_id, quote)
            continue
        if (source_id, quote) in seen:
            rejected += reject("duplicate", source_id, quote)
            continue
        # Quoted procedural instructions are still data. Do not present obvious
        # attempts to control the assistant as an answer to a helpdesk question.
        if _INSTRUCTION_LIKE.search(quote):
            rejected += reject("instruction_like", source_id, quote)
            continue
        seen.add((source_id, quote))
        base = source.get("start_char")
        start = base + offset if isinstance(base, int) and not isinstance(base, bool) else None
        citations.append({key: source.get(key) for key in (
            "source_id", "doc_id", "doc_name", "version", "source_hash", "chunk_id", "page_number", "section_heading")}
            | {"quote": quote, "start_char": start, "end_char": start + len(quote) if start is not None else None})
    if answer_style is None:
        from backend.prompts import rag_prompts
        answer_style = rag_prompts.ANSWER_STYLE
    sentence = payload.get("answer") if answer_style == "sentence" else None
    if not citations:
        refused = insufficient_result("no_validated_quotes", rejected)
        return GroundingResult(refused.answer, refused.status, [], 0, rejected, refused.reason, tuple(details),
                               answer_style="quotes", sentence=sentence if isinstance(sentence, str) else None,
                               sentence_check="no_verified_quotes" if answer_style == "sentence" else None)
    answer = "\n\n".join(f'{citation["quote"]} [{citation["source_id"]}]' for citation in citations)
    shown, check = "quotes", None
    if answer_style == "sentence":
        accepted, check = check_sentence(sentence, citations)
        if accepted is not None:
            answer, shown = accepted, "sentence"  # the verified quotes stay in citations
    return GroundingResult(answer, "answered", citations, len(citations), rejected, "verified_source_quotes",
                           tuple(details), answer_style=shown,
                           sentence=sentence if isinstance(sentence, str) else None, sentence_check=check)
