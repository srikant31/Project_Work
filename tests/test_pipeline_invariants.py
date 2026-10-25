"""Pure regression checks; no Postgres, Chroma or model weights are loaded."""
import re

import pytest

from backend.utils.chunking import chunk_pages, chunk_text


class WordTokenizer:
    """Explicit deterministic test double; production always uses model tokens."""
    def num_special_tokens_to_add(self, pair=False):
        return 2

    def __call__(self, text, **kwargs):
        spans = [(m.start(), m.end()) for m in re.finditer(r"\S+", text)]
        return {"input_ids": list(range(len(spans))), "offset_mapping": spans}


def split(text, size=100, overlap=20):
    return chunk_text(text, size, overlap, tokenizer=WordTokenizer(), max_sequence_length=size + 2)


def test_overlap_and_indices_are_real():
    chunks = split(" ".join(f"w{i}" for i in range(1, 251)))
    assert [c.text.split()[0] for c in chunks] == ["w1", "w81", "w161"]
    assert [c.index for c in chunks] == list(range(len(chunks)))


def test_repeated_content_coverage_uses_positions_not_sets():
    text = " ".join("same" for _ in range(250))
    chunks = split(text, 50, 10)
    for position, char in enumerate(text):
        if not char.isspace():
            assert any(c.start_char <= position < c.end_char for c in chunks)
    assert all(text[c.start_char:c.end_char] == c.text for c in chunks)


def test_empty_and_short_input():
    assert split("") == []
    assert split("   ") == []
    assert len(split("one two three")) == 1
    assert split("one two three")[0].token_count == 3


def test_invalid_negative_overlap_rejected():
    with pytest.raises(ValueError):
        split("a b c", 10, -1)


def test_page_gap_and_global_indices():
    chunks = chunk_pages(["a b c d", "", "e f g h"], 2, 0, tokenizer=WordTokenizer(), max_sequence_length=4)
    assert [c.page_number for c in chunks] == [1, 1, 3, 3]
    assert [c.index for c in chunks] == list(range(len(chunks)))
