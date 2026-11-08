"""Table rows stay citable, and continued tables can recover their header."""

import pytest

from backend.services.grounding_service import _complete_quote_offset, quote_boundary_offsets
from backend.services.table_context import missing_table_header


DOCX_TWO_COLUMN = (
    "Document control\n\n"
    "Field | Value\n"
    "Document ID | FA-001\n"
    "Owner | Facilities Manager\n\n"
    "Badges are issued on the first working day."
)

PDF_TABLE = (
    "## **3. Retention schedule**\n\n"
    "|Record type|Retention period|Trigger|\n"
    "|---|---|---|\n"
    "|Employee expense records|5 years|Expense date|\n"
    "|Contracts (signed)|7 years after expiry|Expiry date|\n"
    "|Vendor invoices|7 years|Invoice date|\n"
    "|Customer contracts|7 years after termination|Termination date|"
)


def _quotable(source, quote, start=0, end=None):
    end = len(source) if end is None else end
    return _complete_quote_offset(source[start:end], quote, quote_boundary_offsets(source, start, end)) is not None


@pytest.mark.parametrize("row", ["Field | Value", "Document ID | FA-001", "Owner | Facilities Manager"])
def test_two_column_docx_rows_are_citable_one_row_at_a_time(row):
    assert _quotable(DOCX_TWO_COLUMN, row)


def test_two_column_docx_row_still_rejects_a_fragment():
    assert not _quotable(DOCX_TWO_COLUMN, "Facilities Manager")


@pytest.mark.parametrize("source,quote", [
    ("Use the A | B switch\nonly after manager approval.", "Use the A | B switch"),
    ("Choose option A | B\nonly after manager approval.", "Choose option A | B"),
])
def test_lone_pipe_in_wrapped_prose_does_not_create_a_quotable_fragment(source, quote):
    assert not _quotable(source, quote)


def test_pdf_and_markdown_rows_remain_citable():
    assert _quotable(PDF_TABLE, "|Employee expense records|5 years|Expense date|")


def test_continued_table_returns_header_span_before_the_hit():
    start = PDF_TABLE.index("7 years after expiry")  # chunk begins mid-row, as in RC-001
    span = missing_table_header(PDF_TABLE, start, len(PDF_TABLE))
    assert span is not None
    assert PDF_TABLE[span[0]:span[1]] == "|Record type|Retention period|Trigger|"


def test_hit_that_contains_its_header_needs_nothing():
    assert missing_table_header(PDF_TABLE, 0, len(PDF_TABLE)) is None


def test_rows_without_a_proven_header_return_none():
    text = "|Badge replacement|None|1 business day|\n|Visitor pre-registration|None|Instant|"
    assert missing_table_header(text, 0, len(text)) is None


def test_prose_hit_returns_none():
    text = "Expense records are retained for five years."
    assert missing_table_header(text, 0, len(text)) is None


def test_page_two_repeated_header_is_used_not_page_one():
    text = PDF_TABLE + "\n\nPage 1\n\n|Record type|Retention period|Trigger|\n|---|---|---|\n|Access logs|1 year|Log date|"
    start = text.index("|Access logs")
    span = missing_table_header(text, start, len(text))
    assert span is not None and span[0] > text.index("Page 1")


@pytest.fixture
def stored_table(db_factory):
    from backend.database.models import Document, DocumentCategory, DocumentChunk

    header_end = PDF_TABLE.index("|Employee expense")
    row_start = PDF_TABLE.index("|Contracts (signed)")
    with db_factory() as session:
        category = session.query(DocumentCategory).first()
        document = Document(doc_name="Retention", doc_type="md", doc_path="unused.md",
                            category_id=category.category_id, status="active", is_online=True,
                            version=1, source_hash="a" * 64, extracted_text=PDF_TABLE)
        session.add(document)
        session.flush()
        header = DocumentChunk(document_id=document.doc_id, chunk_index=0, is_active=True,
                               chunk_text=PDF_TABLE[:header_end], start_char=0, end_char=header_end)
        body = DocumentChunk(document_id=document.doc_id, chunk_index=1, is_active=True,
                             chunk_text=PDF_TABLE[row_start:], start_char=row_start, end_char=len(PDF_TABLE))
        session.add_all([header, body])
        session.commit()
        hit = {"doc_id": str(document.doc_id), "chunk_id": str(body.chunk_id),
               "doc_name": document.doc_name, "version": 1, "source_hash": document.source_hash,
               "text": body.chunk_text, "start_char": body.start_char, "end_char": body.end_char,
               "similarity_score": .8, "rank": 1}
        return document.doc_id, header.chunk_id, hit


@pytest.mark.integration
def test_context_adds_real_header_chunk_and_preserves_citable_offsets(db_factory, stored_table, monkeypatch):
    import json
    from backend.services.rag_service import RAGService
    from backend.services.search_service import SearchService
    from backend.services.grounding_service import validate_claims

    _, header_id, hit = stored_table
    monkeypatch.setattr(SearchService, "search", lambda *a, **k: {"question": "Retention?", "hits": [hit, hit]})
    context = RAGService(session_factory=db_factory, embedder=None, retriever=None)._context("Retention?", 3)
    assert len(context.sources) == 2  # duplicate retrieved hits/header additions are deduplicated
    header = next(source for source in context.sources if source["chunk_id"] == str(header_id))
    assert header["similarity_score"] is None
    assert header["context_role"] == "table_header_chunk"
    for source in context.sources:
        assert PDF_TABLE[source["start_char"]:source["end_char"]] == source["text"]
    evidence = json.loads(context.messages[-1]["content"])["evidence"]
    assert any("|Record type|Retention period|Trigger|" in item["text"] for item in evidence)
    quote = "|Contracts (signed)|7 years after expiry|Expiry date|"
    result = validate_claims({"insufficient_evidence": False,
                              "claims": [{"source_id": context.sources[0]["source_id"], "quote": quote}]}, context)
    assert result.status == "answered"
    citation = result.citations[0]
    assert PDF_TABLE[citation["start_char"]:citation["end_char"]] == quote


@pytest.mark.integration
@pytest.mark.parametrize("change", ["inactive_chunk", "offline", "inactive_document", "version", "hash", "corrupt_text"])
def test_header_lookup_rejects_ineligible_or_corrupt_evidence(db_factory, stored_table, change):
    from backend.database.models import Document, DocumentChunk
    from backend.services.rag_service import RAGService

    document_id, header_id, hit = stored_table
    with db_factory() as session:
        document, header = session.get(Document, document_id), session.get(DocumentChunk, header_id)
        if change == "inactive_chunk":
            header.is_active = False
        elif change == "offline":
            document.is_online = False
        elif change == "inactive_document":
            document.status = "deactivated"
        elif change == "version":
            document.version = 2
        elif change == "hash":
            document.source_hash = "b" * 64
        else:
            header.chunk_text = "Invented header"
        session.commit()
        assert RAGService._table_header_chunk(session, hit, PDF_TABLE) is None


def test_partial_row_is_not_made_citable_by_header_recovery():
    start = PDF_TABLE.index("7 years after expiry")
    assert not _quotable(PDF_TABLE, "7 years after expiry|Expiry date|", start=start)
