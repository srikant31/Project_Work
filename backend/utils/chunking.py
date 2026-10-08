"""Chunking. Still a word-count splitter, but it now carries the metadata
fields the schema and the evaluation harness need (page_number, section_heading).

The splitting ALGORITHM is still a placeholder — replace `chunk_text` later.
The Chunk CONTRACT is what matters now: fix the shape once, so swapping the
algorithm later doesn't force a full re-ingest of the corpus.
"""

from dataclasses import dataclass


@dataclass
class Chunk:
    text: str
    index: int
    page_number: int | None = None
    section_heading: str | None = None

    @property
    def token_count(self) -> int:
        # Placeholder. Swap for the real tokenizer when you pin the embedding model:
        #   len(tokenizer.encode(self.text))
        return len(self.text.split())


def chunk_text(
    text: str,
    chunk_size: int = 200,
    overlap: int = 40,
    page_number: int | None = None,
    section_heading: str | None = None,
) -> list[Chunk]:
    """Split text into overlapping word windows.

    chunk_size=200 with overlap=40 is a saner default than the old 50/0 —
    50 words with no overlap routinely cuts sentences in half and drops the
    context that makes a chunk answerable on its own.
    """
    if overlap >= chunk_size:
        raise ValueError("overlap must be smaller than chunk_size")

    words = text.split()
    if not words:
        return []

    stride = chunk_size - overlap
    chunks: list[Chunk] = []

    for i in range(0, len(words), stride):
        window = words[i : i + chunk_size]
        if not window:
            break
        chunks.append(
            Chunk(
                text=" ".join(window),
                index=len(chunks),
                page_number=page_number,
                section_heading=section_heading,
            )
        )
        if i + chunk_size >= len(words):
            break

    return chunks


def chunk_pages(
    pages: list[str],
    chunk_size: int = 200,
    overlap: int = 40,
) -> list[Chunk]:
    """Chunk a list of page texts, preserving real page numbers (1-indexed).

    Use this once your PDF loader gives you per-page text — it's what makes
    EvaluationQuestions.expected_page_number gradeable.
    """
    all_chunks: list[Chunk] = []
    for page_no, page_text in enumerate(pages, start=1):
        for chunk in chunk_text(page_text, chunk_size, overlap, page_number=page_no):
            chunk.index = len(all_chunks)
            all_chunks.append(chunk)
    return all_chunks
