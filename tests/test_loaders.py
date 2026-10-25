"""Extraction tests generate tiny fixtures and never access the application stores."""
import hashlib
from pathlib import Path

import pytest
from docx import Document
from pypdf import PdfWriter
from pypdf.generic import DictionaryObject, NameObject, NumberObject, DecodedStreamObject

from backend.utils.loaders import (
    DocumentLoadError, EmptyDocumentError, EncryptedPdfError, LoadedDocument,
    ScannedPdfError, UnsupportedFormatError, clean_text, load_document,
)


def write_pdf(path, kinds, *, password=None):
    writer = PdfWriter()
    for kind in kinds:
        page = writer.add_blank_page(width=300, height=300)
        resources = DictionaryObject()
        if kind == "image":
            image = DecodedStreamObject()
            image.set_data(b"\x00\x00\x00")
            image.update({
                NameObject("/Type"): NameObject("/XObject"),
                NameObject("/Subtype"): NameObject("/Image"),
                NameObject("/Width"): NumberObject(1), NameObject("/Height"): NumberObject(1),
                NameObject("/ColorSpace"): NameObject("/DeviceRGB"),
                NameObject("/BitsPerComponent"): NumberObject(8),
            })
            resources[NameObject("/XObject")] = DictionaryObject({NameObject("/Im0"): writer._add_object(image)})
            commands = b"q 100 0 0 100 0 0 cm /Im0 Do Q"
        elif kind:
            font = DictionaryObject({NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"), NameObject("/BaseFont"): NameObject("/Helvetica")})
            resources[NameObject("/Font")] = DictionaryObject({NameObject("/F1"): writer._add_object(font)})
            commands = f"BT /F1 12 Tf 10 200 Td ({kind}) Tj ET".encode("ascii")
        else:
            commands = b""
        page[NameObject("/Resources")] = resources
        stream = DecodedStreamObject()
        stream.set_data(commands)
        page[NameObject("/Contents")] = writer._add_object(stream)
    if password:
        writer.encrypt(password)
    with path.open("wb") as handle:
        writer.write(handle)
    return path


def assert_offsets(loaded):
    assert [b.index for b in loaded.blocks] == list(range(len(loaded.blocks)))
    assert all(loaded.source_text[b.start_char:b.end_char] == b.text for b in loaded.blocks)


def test_plain_unicode_bom_cleanup_and_provenance(tmp_path):
    path = tmp_path / "Policy.TXT"
    raw = b"\xef\xbb\xbf" + "  Policy\r\n\r\n\r\nCaf\u00e9 \u0928\u092e\u0938\u094d\u0924\u0947\x00\r\n".encode("utf-8")
    path.write_bytes(raw)
    loaded = load_document(path)
    assert loaded.doc_type == "txt"
    assert loaded.source_text == "Policy\n\nCaf\u00e9 \u0928\u092e\u0938\u094d\u0924\u0947"
    assert loaded.source_hash == hashlib.sha256(raw).hexdigest()
    assert loaded.source_size == len(raw)
    assert loaded.page_count is None
    assert all(b.page_number is None for b in loaded.blocks)
    assert_offsets(loaded)


def test_legacy_encoding_is_explicit(tmp_path):
    path = tmp_path / "legacy.txt"
    path.write_bytes("Caf\u00e9 costs \u20ac5".encode("cp1252"))
    loaded = load_document(path)
    assert loaded.source_text == "Caf\u00e9 costs \u20ac5"
    assert "Windows-1252" in loaded.warnings[0]


@pytest.mark.parametrize("encoding", ["utf-16", "utf-32"])
def test_bom_marked_unicode_text_is_not_misdecoded_as_legacy(tmp_path, encoding):
    path = tmp_path / "unicode.txt"
    path.write_bytes("Caf\u00e9 \u0928\u092e\u0938\u094d\u0924\u0947".encode(encoding))
    loaded = load_document(path)
    assert loaded.source_text == "Caf\u00e9 \u0928\u092e\u0938\u094d\u0924\u0947"
    assert encoding.upper() in loaded.warnings[0]


def test_markdown_sections_ignore_heading_inside_fence(tmp_path):
    path = tmp_path / "guide.md"
    path.write_text("# VPN\n\nIntro.\n\n## Setup\n\n- Install\n- Connect\n\n```python\n# not a heading\n```\n\nTail\n", encoding="utf-8")
    loaded = load_document(path)
    headings = {b.section_heading for b in loaded.blocks}
    assert headings == {"VPN", "Setup"}
    assert "- Install\n- Connect" in loaded.source_text
    assert "# not a heading" in loaded.source_text
    assert_offsets(loaded)


def test_setext_markdown_heading(tmp_path):
    path = tmp_path / "guide.md"
    path.write_text("Network\n=======\n\nAnswer", encoding="utf-8")
    assert {b.section_heading for b in load_document(path).blocks} == {"Network"}


def test_docx_preserves_paragraph_table_order_and_headings(tmp_path):
    path = tmp_path / "faq.docx"
    doc = Document()
    doc.add_heading("Network", level=1)
    doc.add_paragraph("Before the table.")
    table = doc.add_table(rows=2, cols=2)
    table.cell(0, 0).text, table.cell(0, 1).text = "Issue", "Action"
    table.cell(1, 0).text, table.cell(1, 1).text = "VPN", "Enable MFA"
    doc.add_paragraph("After the table.")
    doc.add_heading("Escalation", level=2)
    doc.add_paragraph("Contact IT.")
    doc.save(path)
    loaded = load_document(path)
    assert loaded.source_text.index("Before") < loaded.source_text.index("Issue | Action") < loaded.source_text.index("After")
    assert "VPN | Enable MFA" in loaded.source_text
    assert next(b for b in loaded.blocks if "After" in b.text).section_heading == "Network"
    assert loaded.blocks[-1].section_heading == "Escalation"
    assert loaded.page_count is None
    assert all(b.page_number is None for b in loaded.blocks)
    assert_offsets(loaded)


def test_docx_nested_table_content_is_in_cell_order(tmp_path):
    path = tmp_path / "nested.docx"
    doc = Document()
    cell = doc.add_table(rows=1, cols=1).cell(0, 0)
    cell.text = "Before nested"
    cell.add_table(rows=1, cols=1).cell(0, 0).text = "Nested action"
    cell.add_paragraph("After nested")
    doc.save(path)
    text = load_document(path).source_text
    assert text.index("Before nested") < text.index("Nested action") < text.index("After nested")


def test_docx_merged_cells_are_not_duplicated(tmp_path):
    path = tmp_path / "merged.docx"
    doc = Document()
    table = doc.add_table(rows=2, cols=2)
    table.cell(0, 0).merge(table.cell(0, 1)).text = "Merged heading"
    table.cell(1, 0).text = "Issue"
    table.cell(1, 1).text = "Resolution"
    doc.save(path)
    text = load_document(path).source_text
    assert text.count("Merged heading") == 1
    assert "Issue | Resolution" in text


def test_short_valid_pdf_and_real_pages_with_blank_gap(tmp_path):
    loaded = load_document(write_pdf(tmp_path / "short.pdf", ["Hi", "", "Bye"]))
    assert loaded.page_count == 3
    assert [b.page_number for b in loaded.blocks] == [1, 3]
    assert loaded.pages == ["Hi", "", "Bye"]
    assert any("page 2" in warning for warning in loaded.warnings)
    assert_offsets(loaded)


@pytest.mark.parametrize("pages", [["image"], ["Valid extractable page", "image"]])
def test_image_only_or_mixed_pdf_rejected_without_partial_extraction(tmp_path, pages):
    with pytest.raises(ScannedPdfError, match="require OCR"):
        load_document(write_pdf(tmp_path / "scanned.pdf", pages))


def test_encrypted_pdf_has_specific_error(tmp_path):
    with pytest.raises(EncryptedPdfError, match="password"):
        load_document(write_pdf(tmp_path / "secret.pdf", ["Hello"], password="fixture-password"))


@pytest.mark.parametrize("extension", ["pdf", "docx"])
def test_corrupt_file_rejected(tmp_path, extension):
    path = tmp_path / f"bad.{extension}"
    path.write_bytes(b"not a document")
    with pytest.raises(DocumentLoadError, match="could not read"):
        load_document(path)


@pytest.mark.parametrize("extension", ["txt", "md"])
def test_empty_plain_file_rejected(tmp_path, extension):
    path = tmp_path / f"empty.{extension}"
    path.write_text(" \n\t\n", encoding="utf-8")
    with pytest.raises(EmptyDocumentError):
        load_document(path)


def test_empty_pdf_rejected(tmp_path):
    with pytest.raises(EmptyDocumentError):
        load_document(write_pdf(tmp_path / "blank.pdf", [""]))


def test_empty_docx_rejected(tmp_path):
    path = tmp_path / "empty.docx"
    Document().save(path)
    with pytest.raises(EmptyDocumentError):
        load_document(path)


def test_missing_directory_unsupported_and_unknown_encoding(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_document(tmp_path / "missing.txt")
    with pytest.raises(IsADirectoryError):
        load_document(tmp_path)
    unsupported = tmp_path / "file.xyz"
    unsupported.write_text("ignored")
    with pytest.raises(UnsupportedFormatError):
        load_document(unsupported)
    invalid = tmp_path / "invalid.txt"
    invalid.write_bytes(b"\x81\x8d\x8f")
    with pytest.raises(DocumentLoadError, match="decode"):
        load_document(invalid)


def test_raw_factories_do_not_invent_non_pdf_pages():
    assert LoadedDocument.from_text("Hello").page_count is None
    loaded = LoadedDocument.from_pages(["A", "", "C"])
    assert loaded.page_count == 3
    assert [b.page_number for b in loaded.blocks] == [1, 3]
    assert_offsets(loaded)
    with pytest.raises(ValueError, match="from_pages"):
        LoadedDocument.from_text("Text", doc_type="pdf")
    with pytest.raises(ValueError, match="PDF"):
        LoadedDocument.from_pages(["Text"], doc_type="docx")


def test_raw_page_hash_distinguishes_embedded_formfeeds_from_boundaries():
    single = LoadedDocument.from_pages(["alpha\f beta"])
    multiple = LoadedDocument.from_pages(["alpha", " beta"])
    assert single.source_hash != multiple.source_hash
    assert single.page_count == 1 and multiple.page_count == 2
    assert single.source_hash == LoadedDocument.from_pages(["alpha\f beta"]).source_hash


def test_pdf_loader_retains_original_file_hash_and_byte_size(tmp_path):
    path = write_pdf(tmp_path / "original.pdf", ["First", "Second"])
    loaded = load_document(path)
    assert loaded.source_hash == hashlib.sha256(path.read_bytes()).hexdigest()
    assert loaded.source_size == path.stat().st_size


def test_fixture_corpus_extracts_without_modification():
    corpus = Path(__file__).resolve().parents[1] / "data" / "documents"
    expected = {"sample_password_policy.txt": "txt", "sample_vpn_guide.md": "md", "sample_equipment_manual.pdf": "pdf", "sample_network_faq.docx": "docx"}
    for name, kind in expected.items():
        loaded = load_document(corpus / name)
        assert loaded.doc_type == kind
        assert loaded.blocks
        assert_offsets(loaded)
    with pytest.raises(DocumentLoadError):
        load_document(corpus / "sample_scanned_like.pdf")
    with pytest.raises(UnsupportedFormatError):
        load_document(corpus / "sample_unsupported.xyz")
