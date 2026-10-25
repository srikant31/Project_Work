"""Tokenizer and provenance tests, with no database or vector-store access."""
from pathlib import Path

import pytest
from tokenizers import Tokenizer, models, normalizers, pre_tokenizers, processors
from transformers import PreTrainedTokenizerFast

from backend.utils.chunking import chunk_document, chunk_pages, chunk_text
from backend.utils.loaders import LoadedDocument, SourceBlock, load_document


@pytest.fixture(scope="module")
def tokenizer():
    # A real WordPiece tokenizer makes punctuation and subword limits testable
    # without a model download. The cached production tokenizer is checked below.
    words = ["[PAD]", "[UNK]", "[CLS]", "[SEP]", "[MASK]", ".", ",", "!", "?", "a", "b", "c", "the", "network", "policy", "alpha", "beta", "gamma", "delta", "un", "##break", "##able"]
    words += [f"w{i}" for i in range(1000)]
    backend = Tokenizer(models.WordPiece(vocab={word: i for i, word in enumerate(words)}, unk_token="[UNK]"))
    backend.normalizer = normalizers.BertNormalizer(lowercase=True)
    backend.pre_tokenizer = pre_tokenizers.BertPreTokenizer()
    backend.post_processor = processors.TemplateProcessing(single="[CLS] $A [SEP]", special_tokens=[("[CLS]", 2), ("[SEP]", 3)])
    return PreTrainedTokenizerFast(tokenizer_object=backend, unk_token="[UNK]", cls_token="[CLS]", sep_token="[SEP]", pad_token="[PAD]")


def assert_contract(loaded, chunks, tokenizer, budget):
    assert [c.index for c in chunks] == list(range(len(chunks)))
    for chunk in chunks:
        assert loaded.source_text[chunk.start_char:chunk.end_char] == chunk.text
        payload = tokenizer(chunk.text, add_special_tokens=False, truncation=False)["input_ids"]
        assert chunk.token_count == len(payload)
        assert len(payload) <= budget
        assert len(tokenizer(chunk.text, add_special_tokens=True, truncation=False)["input_ids"]) <= budget + 2
    # Every non-whitespace source character is present, even repeated words.
    for position, character in enumerate(loaded.source_text):
        if not character.isspace():
            assert any(c.start_char <= position < c.end_char for c in chunks), (position, character)


def test_actual_tokens_and_exact_source_formatting(tokenizer):
    loaded = LoadedDocument.from_text("alpha,beta! gamma?\n\n- network\n- policy")
    chunks = chunk_document(loaded, tokenizer, 10, chunk_size=8, overlap=2)
    assert_contract(loaded, chunks, tokenizer, 8)
    assert any("\n" in c.text for c in chunks)
    assert all(c.page_number is None for c in chunks)
    assert sum(c.token_count for c in chunks) > len(loaded.source_text.split())


def test_overlap_uses_model_tokens_and_always_progresses(tokenizer):
    loaded = LoadedDocument.from_text(" ".join(f"w{i}" for i in range(30)))
    chunks = chunk_document(loaded, tokenizer, 12, chunk_size=10, overlap=3)
    assert [c.text.split()[0] for c in chunks] == ["w0", "w7", "w14", "w21"]
    assert_contract(loaded, chunks, tokenizer, 10)
    tiny = chunk_document(loaded, tokenizer, 12, chunk_size=10, overlap=9)
    assert len(tiny) == 21
    assert len({c.start_char for c in tiny}) == len(tiny)


def test_paragraph_and_sentence_boundaries_preferred(tokenizer):
    loaded = LoadedDocument.from_text("w0 w1 w2 w3. w4 w5 w6 w7 w8 w9 w10 w11")
    chunks = chunk_document(loaded, tokenizer, 12, chunk_size=10, overlap=1)
    assert chunks[0].text.endswith(".")
    assert_contract(loaded, chunks, tokenizer, 10)


def test_large_overlap_does_not_repeat_already_covered_sentence_tails(tokenizer):
    loaded = LoadedDocument.from_text("w0 w1 w2 w3 w4. " + " ".join(f"w{i}" for i in range(5, 40)))
    chunks = chunk_document(loaded, tokenizer, 12, chunk_size=10, overlap=9)
    assert all(a.end_char < b.end_char for a, b in zip(chunks, chunks[1:]))
    assert_contract(loaded, chunks, tokenizer, 10)


def test_wordpiece_retokenization_does_not_emit_contained_overlaps():
    words = ["[UNK]", "[CLS]", "[SEP]", "abcd", "##ef", "e", "##f"]
    backend = Tokenizer(models.WordPiece(vocab={word: i for i, word in enumerate(words)}, unk_token="[UNK]"))
    backend.normalizer = normalizers.BertNormalizer(lowercase=True)
    backend.pre_tokenizer = pre_tokenizers.BertPreTokenizer()
    backend.post_processor = processors.TemplateProcessing(single="[CLS] $A [SEP]", special_tokens=[("[CLS]", 1), ("[SEP]", 2)])
    tokenizer = PreTrainedTokenizerFast(tokenizer_object=backend, unk_token="[UNK]", cls_token="[CLS]", sep_token="[SEP]")
    # The continuation '##ef' becomes two tokens ('e', '##f') when a new
    # overlapping substring begins there. Combining/zero-width characters
    # must remain covered even when that window shrinks and is omitted.
    loaded = LoadedDocument.from_text("abcdef\u0301 abcdef\u200b abcdef abcdef")
    chunks = chunk_document(loaded, tokenizer, 5, chunk_size=3, overlap=2)
    assert all(a.end_char < b.end_char for a, b in zip(chunks, chunks[1:]))
    assert_contract(loaded, chunks, tokenizer, 3)


def test_final_full_encoding_is_checked_even_if_advertised_special_count_is_wrong(tokenizer):
    class UnderreportedSpecials:
        def num_special_tokens_to_add(self, pair=False):
            return 0

        def __call__(self, text, **kwargs):
            return tokenizer(text, **kwargs)

    loaded = LoadedDocument.from_text("w0 w1 w2 w3")
    chunks = chunk_document(loaded, UnderreportedSpecials(), 3, chunk_size=3, overlap=0)
    assert len(chunks) == 4
    assert all(len(tokenizer(c.text, add_special_tokens=True)["input_ids"]) <= 3 for c in chunks)
    assert_contract(loaded, chunks, tokenizer, 3)


def test_real_pdf_pages_and_sections_never_cross(tokenizer):
    loaded = LoadedDocument.from_pages(["w0 w1 w2", "", "w3 w4 w5"])
    chunks = chunk_document(loaded, tokenizer, 12, chunk_size=10, overlap=2)
    assert [c.page_number for c in chunks] == [1, 3]
    assert_contract(loaded, chunks, tokenizer, 10)
    md = LoadedDocument.from_text("# Alpha\n\nw0 w1\n\n## Beta\n\nw2 w3", doc_type="md")
    chunks = chunk_document(md, tokenizer, 12, chunk_size=10, overlap=2)
    assert [c.section_heading for c in chunks] == ["Alpha", "Beta"]
    assert all(not ("w0" in c.text and "w2" in c.text) for c in chunks)
    assert_contract(md, chunks, tokenizer, 10)


def test_multilingual_and_long_unbroken_text_preserved(tokenizer):
    loaded = LoadedDocument.from_text("\u0928\u092e\u0938\u094d\u0924\u0947 \u4e16\u754c caf\u00e9 " + "unbreakable" * 100 + "\n\n" + " ".join("unbreakable" for _ in range(25)))
    chunks = chunk_document(loaded, tokenizer, 12, chunk_size=10, overlap=2)
    assert_contract(loaded, chunks, tokenizer, 10)


def test_normalizer_removed_characters_are_preserved_in_source_spans(tokenizer):
    loaded = LoadedDocument.from_text("alpha\u0301 beta\u200b gamma")
    chunks = chunk_document(loaded, tokenizer, 3, chunk_size=1, overlap=0)
    assert_contract(loaded, chunks, tokenizer, 1)


@pytest.mark.parametrize("size,overlap,maximum", [(0, 0, 12), (-1, 0, 12), (10, -1, 12), (10, 10, 12), (11, 1, 12), (1, 0, 2), (True, 0, 12), (10, 1.5, 12)])
def test_invalid_configuration_rejected(tokenizer, size, overlap, maximum):
    with pytest.raises(ValueError):
        chunk_document(LoadedDocument.from_text("alpha"), tokenizer, maximum, size, overlap)


def test_corrupt_source_offsets_rejected(tokenizer):
    loaded = LoadedDocument.from_text("alpha beta")
    loaded.blocks = [SourceBlock("wrong text", 0, start_char=0, end_char=5)]
    with pytest.raises(ValueError, match="offsets"):
        chunk_document(loaded, tokenizer, 12, chunk_size=10, overlap=2)


def test_compatibility_wrappers_require_explicit_tokenizer(tokenizer):
    with pytest.raises(TypeError):
        chunk_text("alpha beta")
    assert chunk_text("   ", 10, 2, tokenizer=tokenizer, max_sequence_length=12) == []
    assert chunk_pages(["", ""], 10, 2, tokenizer=tokenizer, max_sequence_length=12) == []
    assert chunk_text("alpha beta", 10, 2, page_number=4, section_heading="VPN", tokenizer=tokenizer, max_sequence_length=12)[0].page_number == 4


def test_offline_cached_production_tokenizer_and_samples():
    from transformers import AutoTokenizer

    cache = Path.home() / ".cache" / "huggingface" / "hub" / "models--sentence-transformers--all-MiniLM-L6-v2" / "snapshots"
    snapshots = sorted(cache.glob("*/tokenizer.json"))
    if not snapshots:
        pytest.skip("Production tokenizer is not cached locally; no downloads in tests")
    model_tokenizer = AutoTokenizer.from_pretrained(str(snapshots[-1].parent), use_fast=True, local_files_only=True)
    corpus = Path(__file__).resolve().parents[1] / "data" / "documents"
    for path in sorted(corpus.iterdir()):
        if path.suffix not in {".txt", ".md", ".docx"} and path.name != "sample_equipment_manual.pdf":
            continue
        loaded = load_document(path)
        chunks = chunk_document(loaded, model_tokenizer, 256, chunk_size=200, overlap=40)
        assert chunks
        assert_contract(loaded, chunks, model_tokenizer, 200)
        assert max(len(model_tokenizer(c.text, truncation=False)["input_ids"]) for c in chunks) <= 256
