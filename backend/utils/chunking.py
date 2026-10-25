"""Token-bounded, source-preserving chunks for the configured embedding model."""
from dataclasses import dataclass

from backend.utils.loaders import LoadedDocument, SourceBlock

CHUNKER_VERSION = "source-token-window-v1"


@dataclass(frozen=True)
class Chunk:
    text: str
    index: int
    page_number: int | None = None
    section_heading: str | None = None
    start_char: int = 0
    end_char: int = 0
    token_count: int = 0


def _validate_settings(tokenizer, max_sequence_length, chunk_size, overlap):
    for value, name in ((max_sequence_length, "max_sequence_length"), (chunk_size, "chunk_size"), (overlap, "overlap")):
        if not isinstance(value, int) or isinstance(value, bool):
            raise ValueError(f"{name} must be an integer")
    special_tokens = tokenizer.num_special_tokens_to_add(pair=False)
    if max_sequence_length <= special_tokens:
        raise ValueError("max_sequence_length must leave room for payload and special tokens")
    if chunk_size <= 0 or not 0 <= overlap < chunk_size:
        raise ValueError("Require chunk_size > 0 and 0 <= overlap < chunk_size")
    if chunk_size + special_tokens > max_sequence_length:
        raise ValueError("chunk_size plus special tokens exceeds the embedding model's max_sequence_length")


def _token_data(tokenizer, text):
    try:
        encoded = tokenizer(
            text, add_special_tokens=False, return_offsets_mapping=True, truncation=False,
            verbose=False,
        )
        offsets = encoded["offset_mapping"]
        ids = encoded["input_ids"]
    except (NotImplementedError, KeyError, TypeError) as exc:
        raise ValueError("Chunking requires a tokenizer supporting character offset mappings (a fast tokenizer)") from exc
    if len(ids) != len(offsets):
        raise ValueError("Tokenizer returned inconsistent token IDs and character offsets")
    return ids, offsets


def _boundary(text, offsets, start, end, previous_end=0):
    """Prefer the last paragraph/sentence boundary in the latter half-window."""
    lower = max(start + max(1, (end - start) // 2), previous_end + 1)
    paragraphs, sentences = [], []
    for candidate in range(lower, end + 1):
        char_end = offsets[candidate - 1][1]
        next_start = offsets[candidate][0] if candidate < len(offsets) else len(text)
        gap = text[char_end:next_start]
        if "\n\n" in gap:
            paragraphs.append(candidate)
        elif char_end and text[char_end - 1] in ".!?" and (gap or char_end == len(text)):
            sentences.append(candidate)
    return (paragraphs or sentences or [end])[-1]


def _groups(loaded):
    """Join neighboring blocks only when their page and section agree."""
    group = []
    for block in loaded.blocks:
        if loaded.source_text[block.start_char:block.end_char] != block.text:
            raise ValueError("Source block offsets do not match normalized source_text")
        if group and (block.page_number, block.section_heading) != (group[-1].page_number, group[-1].section_heading):
            yield group
            group = []
        group.append(block)
    if group:
        yield group


def chunk_document(
    loaded: LoadedDocument, tokenizer, max_sequence_length: int,
    chunk_size: int = 200, overlap: int = 40,
) -> list[Chunk]:
    """Return exact source substrings, bounded in model tokens.

    token_count excludes special tokens; validation reserves those tokens in
    the embedding model's actual limit. Overlap is up to the requested payload
    tokens and never crosses page/section boundaries.
    """
    _validate_settings(tokenizer, max_sequence_length, chunk_size, overlap)
    chunks = []
    for group in _groups(loaded):
        base = group[0].start_char
        text = loaded.source_text[base:group[-1].end_char]
        ids, offsets = _token_data(tokenizer, text)
        if not ids:
            continue
        start = previous_end = 0
        while start < len(ids):
            end = min(start + chunk_size, len(ids))
            if end < len(ids):
                end = _boundary(text, offsets, start, end, previous_end)
            start_char = 0 if start == 0 else offsets[start][0]
            while end > start:
                # Include characters removed by tokenizer normalization (for
                # example combining marks/zero-width characters) between this
                # token and the next. Only insignificant trailing whitespace
                # may remain outside a chunk when overlap is zero.
                end_char = len(text) if end == len(ids) else offsets[end][0]
                end_char -= len(text[start_char:end_char]) - len(text[start_char:end_char].rstrip())
                candidate = text[start_char:end_char]
                # A WordPiece beginning midword can tokenize differently when
                # it begins a fresh substring, so check every final chunk.
                candidate_ids, _ = _token_data(tokenizer, candidate)
                full_ids = tokenizer(
                    candidate, add_special_tokens=True, truncation=False,
                    verbose=False,
                )["input_ids"]
                if len(candidate_ids) <= chunk_size and len(full_ids) <= max_sequence_length:
                    break
                end -= 1
            if end <= start:
                raise ValueError("A source span cannot fit within the requested token budget")
            # Retokenization can shrink a highly overlapping window back into
            # already-covered text. Advance through it without emitting a
            # redundant contained chunk; every emitted chunk must add source.
            if candidate.strip() and (not chunks or base + end_char > chunks[-1].end_char):
                chunks.append(Chunk(
                    text=candidate, index=len(chunks), page_number=group[0].page_number,
                    section_heading=group[0].section_heading,
                    start_char=base + start_char, end_char=base + end_char,
                    token_count=len(candidate_ids),
                ))
            if end == len(ids):
                break
            previous_end = max(previous_end, end)
            start = max(start + 1, end - overlap)
    return chunks


def chunk_text(
    text: str, chunk_size: int = 200, overlap: int = 40,
    page_number: int | None = None, section_heading: str | None = None,
    *, tokenizer, max_sequence_length: int,
) -> list[Chunk]:
    """Compatibility wrapper; actual tokenizer/limit are explicitly required."""
    if not text.strip():
        _validate_settings(tokenizer, max_sequence_length, chunk_size, overlap)
        return []
    loaded = LoadedDocument.from_text(text)
    if page_number is not None or section_heading is not None:
        loaded.blocks = [SourceBlock(
            b.text, b.index, page_number, section_heading, b.start_char, b.end_char,
        ) for b in loaded.blocks]
    return chunk_document(loaded, tokenizer, max_sequence_length, chunk_size, overlap)


def chunk_pages(
    pages: list[str], chunk_size: int = 200, overlap: int = 40,
    *, tokenizer, max_sequence_length: int,
) -> list[Chunk]:
    """Compatibility wrapper for callers supplying genuine PDF page text."""
    if not any(p.strip() for p in pages):
        _validate_settings(tokenizer, max_sequence_length, chunk_size, overlap)
        return []
    return chunk_document(LoadedDocument.from_pages(pages), tokenizer, max_sequence_length, chunk_size, overlap)
