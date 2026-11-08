"""Locate the header row of a Markdown table whose rows a retrieved chunk holds.

PDF (PyMuPDF4LLM) and Markdown sources store tables as |a|b| rows with a
|---| separator. A long table can continue into a chunk that no longer holds
its header row. This module only finds where that header is in the immutable
source text; the caller attaches the real stored chunk that contains it, so
every evidence source stays an exact source slice.

DOCX tables lack Markdown separators in the extracted text and are not
handled here. Their source blocks can still be split by the token chunker.
"""
import re

_ROW = re.compile(r"^\s*\|.*\|\s*$")
_SEPARATOR = re.compile(r"^\s*\|(\s*:?-{3,}:?\s*\|)+\s*$")


def _line_spans(text):
    """(start, end) of each line in text; end excludes the line break."""
    spans, position = [], 0
    for line in text.splitlines(keepends=True):
        spans.append((position, position + len(line.rstrip("\r\n"))))
        position += len(line)
    return spans


def missing_table_header(source_text, start, end):
    """Return (start, end) of the header row of the first table row in
    source_text[start:end], when that header lies before start; else None.

    A header is recognised only when a separator row follows it, so a run of
    rows without a proven header (or a hit that already contains its header)
    returns None.
    """
    if (not isinstance(start, int) or isinstance(start, bool)
            or not isinstance(end, int) or isinstance(end, bool)
            or not 0 <= start < end <= len(source_text)):
        return None
    spans = _line_spans(source_text)

    def is_row(index):
        line_start, line_end = spans[index]
        return bool(_ROW.match(source_text[line_start:line_end]))

    first = next((index for index, (line_start, line_end) in enumerate(spans)
                  if line_end > start and line_start < end and is_row(index)), None)
    if first is None:
        return None
    top = first
    while top > 0 and is_row(top - 1):
        top -= 1
    if top + 1 >= len(spans):
        return None
    separator_start, separator_end = spans[top + 1]
    if not _SEPARATOR.match(source_text[separator_start:separator_end]):
        return None
    header_start, header_end = spans[top]
    if header_start >= start:
        return None  # the hit already begins at or before its header
    return header_start, header_end
