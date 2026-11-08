import hashlib
import json

import pytest

from backend.utils import docling_bridge, pdf_adapter
from backend.utils.loaders import load_document, DocumentLoadError
from test_loaders import write_pdf, assert_offsets


def test_real_layout_adapter_preserves_blank_pages_and_exact_offsets(tmp_path, monkeypatch):
    monkeypatch.setenv('PDF_TEXT_EXTRACTOR', 'pymupdf4llm')
    path = write_pdf(tmp_path / 'real.pdf', ['Policy return 7 days', '', 'Approval 4 hours'])
    result = load_document(path)
    assert result.page_count == 3
    assert {b.page_number for b in result.blocks} == {1, 3}
    assert result.source_hash == hashlib.sha256(path.read_bytes()).hexdigest()
    assert result.extractor_version.startswith('pymupdf4llm-2.1')
    assert result.extraction_metadata['route'] == 'pymupdf4llm'
    json.dumps(result.extraction_metadata)
    assert_offsets(result)


@pytest.mark.parametrize('mode', ['auto', 'off'])
def test_quality_failure_falls_back_or_rejects_without_partial_text(tmp_path, monkeypatch, mode):
    monkeypatch.setenv('PDF_TEXT_EXTRACTOR', 'pymupdf4llm')
    monkeypatch.setenv('DOCLING_PDF_MODE', mode)
    def fail(*args):
        raise pdf_adapter.PdfQualityError('page 1: native-word coverage below 80%')
    monkeypatch.setattr(pdf_adapter, 'extract_pdf', fail)
    calls = []
    def ocr(path):
        calls.append(path)
        return {'pages': ['Native policy recovered'], 'extractor_version': 'docling-test', 'warnings': []}
    monkeypatch.setattr(docling_bridge, 'extract_pdf', ocr)
    path = write_pdf(tmp_path / 'quality.pdf', ['Native policy'])
    if mode == 'off':
        with pytest.raises(DocumentLoadError, match='requires review'):
            load_document(path)
        assert not calls
    else:
        result = load_document(path)
        assert result.source_text == 'Native policy recovered'
        assert result.extraction_metadata['route'] == 'docling'
        assert '80%' in result.extraction_metadata['reason']
        assert len(calls) == 1


def test_substantial_image_with_text_requests_ocr(tmp_path):
    import pymupdf
    path = tmp_path / 'mixed.pdf'
    with pymupdf.open() as doc:
        page = doc.new_page(width=300, height=300)
        page.insert_text((20, 20), 'A readable cover heading')
        pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 20, 20), False)
        pix.clear_with(255)
        page.insert_image(pymupdf.Rect(20, 40, 280, 280), pixmap=pix)
        doc.save(path)
    with pytest.raises(pdf_adapter.PdfQualityError, match='image_content'):
        pdf_adapter.extract_pdf(path, ['A readable cover heading'])


@pytest.mark.parametrize('recovered', ['', 'Policy'])
def test_ocr_success_cannot_hide_native_text_loss(tmp_path, monkeypatch, recovered):
    monkeypatch.setenv('DOCLING_PDF_MODE', 'force')
    monkeypatch.setattr(docling_bridge, 'extract_pdf', lambda path: {
        'pages': [recovered], 'warnings': [], 'extractor_version': 'docling-test'})
    with pytest.raises(DocumentLoadError, match='OCR'):
        load_document(write_pdf(tmp_path/'loss.pdf', ['Policy entitlement 15 working days']))


def test_layout_rejects_word_loss_and_incorrect_page_order(tmp_path, monkeypatch):
    import pymupdf
    path = write_pdf(tmp_path / 'loss.pdf', ['The entitlement is 15 days'])
    for row in [
        {'metadata': {'page_number': 1}, 'text': 'entitlement'},
        {'metadata': {'page_number': 2}, 'text': 'The entitlement is 15 days'},
    ]:
        monkeypatch.setattr(pymupdf.Document, 'to_markdown', lambda *args, **kwargs: [row])
        with pytest.raises(pdf_adapter.PdfQualityError):
            pdf_adapter.extract_pdf(path, ['The entitlement is 15 days'])


@pytest.mark.integration
def test_layout_ingestion_versioning_and_real_retrieval(tmp_path, monkeypatch, db_factory,
                                                       real_embedder, real_retriever):
    from backend.database.models import Document
    from backend.services.document_processing_service import DocumentProcessingService
    path = write_pdf(tmp_path / 'policy.pdf', ['Loan devices return within 7 working days'])
    monkeypatch.setenv('PDF_TEXT_EXTRACTOR', 'pymupdf4llm')
    with DocumentProcessingService(session_factory=db_factory, embedder=real_embedder,
                                   retriever=real_retriever) as service:
        first = service.process_file(path, category_name='IT Support', source_key='layout-policy')
        again = service.process_file(path, category_name='IT Support', source_key='layout-policy')
        assert again['skipped'] and again['doc_id'] == first['doc_id']
        hits = service.search('When should loan devices be returned?', top_k=3)
        assert hits[0]['page_number'] == 1 and '7 working days' in hits[0]['text']
        with db_factory() as session:
            doc = session.get(Document, first['doc_id'])
            assert doc.pipeline_config['extraction']['route'] == 'pymupdf4llm'
            assert doc.pipeline_config['extraction']['pages'][0]['layout_boxes']
        monkeypatch.setenv('PDF_TEXT_EXTRACTOR', 'pypdf')
        changed = service.process_file(path, category_name='IT Support', source_key='layout-policy')
        assert changed['doc_id'] != first['doc_id']
