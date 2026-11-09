"""Prompt experiments must change only the instruction text, and say so."""
import importlib

import pytest


def _load(monkeypatch, variant):
    import backend.prompts.rag_prompts as prompts
    if variant is None:
        monkeypatch.delenv("RAG_PROMPT_VARIANT", raising=False)
    else:
        monkeypatch.setenv("RAG_PROMPT_VARIANT", variant)
    return importlib.reload(prompts)


def test_default_prompt_and_version_are_unchanged(monkeypatch):
    prompts = _load(monkeypatch, None)
    assert prompts.PROMPT_VERSION == "grounded-quotes-current-chat-v2"
    assert prompts.SYSTEM_PROMPT == prompts.DEFAULT_PROMPT


def test_variant_changes_instructions_and_is_recorded_in_version(monkeypatch):
    prompts = _load(monkeypatch, "spec_minimal")
    assert prompts.SYSTEM_PROMPT == prompts.SPEC_MINIMAL_PROMPT != prompts.DEFAULT_PROMPT
    assert prompts.PROMPT_VERSION == "grounded-quotes-current-chat-v2+spec_minimal"
    for text in prompts.PROMPT_VARIANTS.values():  # same output contract for every variant
        assert '"insufficient_evidence"' in text and '"claims"' in text and "source_id" in text
    _load(monkeypatch, None)


def test_unknown_variant_fails_loudly(monkeypatch):
    with pytest.raises(ValueError, match="RAG_PROMPT_VARIANT"):
        _load(monkeypatch, "typo")
    _load(monkeypatch, None)
