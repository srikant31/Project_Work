"""Run optional OCR dependencies outside the main RAG virtual environment."""
import json
import os
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory


def extract_pdf(path):
    from backend.utils.loaders import DocumentLoadError
    from pypdf import PdfReader

    root = Path(__file__).resolve().parents[2]
    runtime = Path(os.getenv('DOCLING_PYTHON') or root / '.venv-docling/Scripts/python.exe')
    if not runtime.is_file():
        raise DocumentLoadError('Docling OCR runtime is unavailable. Install the optional OCR environment or upload a text-readable document.')
    with TemporaryDirectory(prefix='helpdesk-ocr-result-') as temp:
        result_path = Path(temp) / 'pages.json'
        try:
            completed = subprocess.run(
                [str(runtime), str(root / 'scripts/docling_extract_worker.py'),
                 str(Path(path).resolve()), str(result_path)],
                capture_output=True, timeout=600, cwd=root,
            )
            if completed.returncode or not result_path.is_file():
                raise DocumentLoadError('Docling OCR failed. Check the optional runtime/model setup; no partial extraction was indexed.')
            data = json.loads(result_path.read_text(encoding='utf-8'))
            if not isinstance(data, dict):
                raise ValueError('Invalid OCR response')
            if not isinstance(data.get('pages'), list) or not all(isinstance(p, str) for p in data['pages']):
                raise ValueError('Invalid OCR page response')
            reader = PdfReader(path)
            if len(data['pages']) != len(reader.pages) or not any(p.strip() for p in data['pages']):
                raise ValueError('Incomplete OCR page response')
            if not isinstance(data.get('extractor_version'), str) or not data['extractor_version'].strip():
                raise ValueError('Missing OCR version')
            if not isinstance(data.get('warnings'), list) or not all(isinstance(w, str) for w in data['warnings']):
                raise ValueError('Invalid OCR warnings')
            return data
        except subprocess.TimeoutExpired:
            raise DocumentLoadError('Docling OCR exceeded its time limit; no partial extraction was indexed.') from None
        except (OSError,ValueError):
            raise DocumentLoadError('Docling OCR returned invalid output; no partial extraction was indexed.') from None
