"""RAG_QUOTE_MATCH=tolerant accepts copies that differ only in formatting.

Every accepted citation must still be an exact slice of the source, and
fragments or changed words must still be rejected.
"""
from types import SimpleNamespace

import pytest

from backend.services.grounding_service import quote_boundary_offsets, validate_claims

SOURCE = (
    "## Retention\n\n"
    "|Record type|Retention period|Trigger|\n"
    "|---|---|---|\n"
    "|Payroll records|7 years|End of tax year|\n"
    "|Access logs|1 year|Log date|\n\n"
    "Accounts lock after five\nconsecutive failed attempts. They unlock after 30 minutes."
)
BASE = 100  # the chunk's start_char in its document


def _context():
    source = {"source_id": "S1", "doc_id": "d", "doc_name": "RC-001_Retention", "version": 1,
              "source_hash": "h", "chunk_id": "c", "page_number": 1, "section_heading": None,
              "text": SOURCE, "start_char": BASE, "end_char": BASE + len(SOURCE),
              **quote_boundary_offsets(SOURCE)}
    return SimpleNamespace(sources=[source])


def _validate(quote, monkeypatch, mode):
    monkeypatch.setenv("RAG_QUOTE_MATCH", mode)
    return validate_claims({"insufficient_evidence": False,
                            "claims": [{"source_id": "S1", "quote": quote}]}, _context())


def _assert_exact_slice(result):
    for citation in result.citations:
        start, end = citation["start_char"] - BASE, citation["end_char"] - BASE
        assert SOURCE[start:end] == citation["quote"]


@pytest.mark.parametrize("quote,expected", [
    ("Payroll records|7 years|End of tax year", "|Payroll records|7 years|End of tax year|"),
    ("| Payroll records | 7 years | End of tax year |", "|Payroll records|7 years|End of tax year|"),
    ("Accounts lock after five consecutive failed attempts.",
     "Accounts lock after five\nconsecutive failed attempts."),
])
def test_formatting_only_differences_are_accepted_as_the_exact_source_text(quote, expected, monkeypatch):
    result = _validate(quote, monkeypatch, "tolerant")
    assert result.status == "answered"
    assert result.citations[0]["quote"] == expected  # the source's text, not the model's copy
    _assert_exact_slice(result)


@pytest.mark.parametrize("quote,why", [
    ("7 years|End of tax year", "not_a_complete_sentence_or_row"),   # fragment of a row
    ("Accounts lock after five consecutive", "not_a_complete_sentence_or_row"),  # fragment of a sentence
    ("|Payroll records|10 years|End of tax year|", "not_found_in_source"),  # changed value
    ("accounts lock after five consecutive failed attempts.", "not_found_in_source"),  # changed case
])
def test_fragments_and_changed_words_are_still_rejected(quote, why, monkeypatch):
    result = _validate(quote, monkeypatch, "tolerant")
    assert result.status == "insufficient_evidence"
    assert result.rejected_details[0]["why"] == why


def test_default_exact_mode_is_unchanged_and_explains_the_rejection(monkeypatch):
    monkeypatch.delenv("RAG_QUOTE_MATCH", raising=False)
    result = validate_claims({"insufficient_evidence": False, "claims": [
        {"source_id": "S1", "quote": "Payroll records|7 years|End of tax year"}]}, _context())
    assert result.status == "insufficient_evidence"
    assert result.rejected_details[0]["why"] == "formatting_differs_only"
    exact = validate_claims({"insufficient_evidence": False, "claims": [
        {"source_id": "S1", "quote": "|Payroll records|7 years|End of tax year|"}]}, _context())
    assert exact.status == "answered"
    _assert_exact_slice(exact)


def test_unknown_mode_fails_loudly(monkeypatch):
    with pytest.raises(ValueError, match="RAG_QUOTE_MATCH"):
        _validate("|Payroll records|7 years|End of tax year|", monkeypatch, "fuzzy")


def test_prompt_variant_strict_copy_is_available(monkeypatch):
    import importlib
    import backend.prompts.rag_prompts as prompts
    monkeypatch.setenv("RAG_PROMPT_VARIANT", "strict_copy")
    loaded = importlib.reload(prompts)
    assert loaded.PROMPT_VERSION.endswith("+strict_copy")
    assert '"insufficient_evidence"' in loaded.SYSTEM_PROMPT and "Copying rules" in loaded.SYSTEM_PROMPT
    monkeypatch.delenv("RAG_PROMPT_VARIANT")
    importlib.reload(prompts)
