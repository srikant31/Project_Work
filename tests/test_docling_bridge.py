import hashlib,json
from types import SimpleNamespace
import pytest
from test_loaders import write_pdf,assert_offsets
from backend.utils.loaders import load_document, DocumentLoadError, EncryptedPdfError, ScannedPdfError


def test_auto_ocr_preserves_physical_pages_checksums_and_exact_offsets(tmp_path,monkeypatch):
    from backend.utils import docling_bridge
    pdf=write_pdf(tmp_path/'scan.pdf',['Cover','image'])
    monkeypatch.setenv('DOCLING_PDF_MODE','auto')
    monkeypatch.setattr(docling_bridge,'extract_pdf',lambda path:{'pages':['Cover','Loan limit is 7 days.'],
        'warnings':['OCR requires review'],'extractor_version':'docling-test-v1'})
    result=load_document(pdf)
    assert result.page_count==2 and result.blocks[-1].page_number==2
    assert result.source_hash==hashlib.sha256(pdf.read_bytes()).hexdigest()
    assert result.extractor_version=='docling-test-v1'; assert_offsets(result)


def test_off_mode_rejects_scan_without_calling_docling(tmp_path,monkeypatch):
    monkeypatch.setenv('DOCLING_PDF_MODE','off')
    with pytest.raises(ScannedPdfError):load_document(write_pdf(tmp_path/'scan.pdf',['image']))


def test_auto_keeps_normal_pdf_and_rejects_encrypted_before_ocr(tmp_path,monkeypatch):
    from backend.utils import docling_bridge
    def forbidden(path):raise AssertionError('Unexpected OCR call')
    monkeypatch.setenv('DOCLING_PDF_MODE','auto');monkeypatch.setattr(docling_bridge,'extract_pdf',forbidden)
    assert load_document(write_pdf(tmp_path/'plain.pdf',['Text'])).source_text=='Text'
    with pytest.raises(EncryptedPdfError):load_document(write_pdf(tmp_path/'locked.pdf',['image'],password='private'))


def test_force_uses_docling_for_layout_pdf(tmp_path,monkeypatch):
    from backend.utils import docling_bridge
    monkeypatch.setenv('DOCLING_PDF_MODE','force')
    monkeypatch.setattr(docling_bridge,'extract_pdf',lambda path:{'pages':['Old order | Table | 7 days'], 'warnings':[], 'extractor_version':'docling-layout'})
    assert load_document(write_pdf(tmp_path/'layout.pdf',['Old order'])).extractor_version=='docling-layout'


def test_missing_ocr_runtime_is_clear_error(tmp_path,monkeypatch):
    from backend.utils.docling_bridge import extract_pdf
    monkeypatch.setenv('DOCLING_PYTHON',str(tmp_path/'missing.exe'))
    with pytest.raises(DocumentLoadError,match='runtime is unavailable'):extract_pdf(tmp_path/'scan.pdf')


@pytest.mark.parametrize('partial', [False, True])
def test_bridge_checks_worker_page_coverage(tmp_path, monkeypatch, partial):
    from backend.utils import docling_bridge
    pdf = write_pdf(tmp_path / 'mixed.pdf', ['Cover', 'image'])
    runtime = tmp_path / 'runtime.exe'
    runtime.touch()
    monkeypatch.setenv('DOCLING_PYTHON', str(runtime))

    def run(args, **kwargs):
        from pathlib import Path
        Path(args[-1]).write_text(json.dumps({
            'pages': ['Cover'] if partial else ['Cover', 'OCR text'],
            'warnings': ['Review OCR'], 'extractor_version': 'docling-test',
        }))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(docling_bridge.subprocess, 'run', run)
    if partial:
        with pytest.raises(DocumentLoadError, match='invalid output'):
            docling_bridge.extract_pdf(pdf)
    else:
        assert docling_bridge.extract_pdf(pdf)['pages'][1] == 'OCR text'


def test_bridge_timeout_is_sanitized(tmp_path, monkeypatch):
    from backend.utils import docling_bridge
    runtime = tmp_path / 'runtime.exe'
    runtime.touch()
    monkeypatch.setenv('DOCLING_PYTHON', str(runtime))

    def run(*args, **kwargs):
        raise docling_bridge.subprocess.TimeoutExpired('private-document-path', 600)

    monkeypatch.setattr(docling_bridge.subprocess, 'run', run)
    with pytest.raises(DocumentLoadError, match='time limit') as error:
        docling_bridge.extract_pdf(tmp_path / 'scan.pdf')
    assert 'private-document-path' not in str(error.value)
