"""Reproducible fictional 20-page extraction benchmark, no DB/LLM writes.

Run: python -m scripts.benchmark_pdf_extractors [--docling]
Times include conversion, not ingestion/embeddings; first-use costs are included.
Authored fact checks are an extraction smoke test, not formal gold RAG scoring.
"""
import argparse
import json
from pathlib import Path
import time
from importlib.metadata import version


def build_fixture(target):
    import pymupdf
    output = pymupdf.open()
    labels = []
    root = Path(__file__).resolve().parents[1]
    facts = ['24 working days', '15 sick days', '16 weeks', '2 days',
             '14 calendar days', '4500', '7 calendar days', '7 working days', '6 pm']
    for source, fact in zip(sorted((root/'data/documents/demo_corpus').glob('*.pdf')), facts, strict=True):
        with pymupdf.open(source) as doc:
            output.insert_pdf(doc)
        labels.append({'type': 'native_policy', 'facts': [fact]})
    for number in range(4):
        page = output.new_page()
        page.insert_text((45, 55), 'Fictional service limits')
        for row, (left, right) in enumerate([('Service', 'Limit'), ('Monitor loan', '14 days'), ('Repair desk', '4 hours')]):
            y = 100 + row * 32
            page.insert_text((50, y), left)
            page.insert_text((270, y), right)
            page.draw_line((45, y+8), (420, y+8))
        labels.append({'type': 'table', 'facts': ['Monitor loan', '14 days', 'Repair desk', '4 hours']})
    for number in range(3):
        page = output.new_page()
        page.insert_textbox((40, 50, 260, 300), 'Fictional leave policy\nAnnual leave is 24 days.\nSubmit requests 10 days ahead.', fontsize=12)
        page.insert_textbox((320, 50, 550, 300), 'Fictional asset policy\nReturn equipment within 7 days.\nKeep the return receipt.', fontsize=12)
        labels.append({'type': 'columns', 'facts': ['24 days', '10 days', '7 days']})
    for number in range(3):
        with pymupdf.open() as image_doc:
            page = image_doc.new_page(width=500, height=500)
            page.insert_text((40, 80), 'FICTIONAL OCR POLICY', fontsize=20)
            page.insert_text((40, 140), 'Return loan devices within 7 working days.', fontsize=16)
            page.insert_text((40, 180), 'The repair desk responds within 4 hours.', fontsize=16)
            pix = page.get_pixmap(matrix=pymupdf.Matrix(2, 2))
        page = output.new_page(width=500, height=600)
        if number == 2:
            page.insert_text((30, 30), 'Readable heading above scanned policy')
        page.insert_image((0, 65, 500, 565), pixmap=pix)
        labels.append({'type': 'mixed_image' if number == 2 else 'scan',
                       'facts': ['7 working days', '4 hours']})
    output.new_page()
    labels.append({'type': 'blank', 'facts': []})
    output.save(target)
    output.close()
    assert len(labels) == 20
    return labels


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--docling', action='store_true')
    args = parser.parse_args()
    out = Path('output/pdf_adapter_benchmark')
    out.mkdir(parents=True, exist_ok=True)
    source = out/'fictional_20_pages.pdf'
    labels = build_fixture(source)
    report = {'scope': '20 authored fictional pages; no LLM, database or corpus changes',
              'versions': {p: version(p) for p in ['pypdf', 'pymupdf', 'pymupdf4llm']},
              'labels': labels, 'results': []}

    def measure(name, function):
        start = time.perf_counter()
        try:
            pages = function()
            assert len(pages) == 20
            checks = []
            for number, (label, text) in enumerate(zip(labels, pages, strict=True), 1):
                normalized = ' '.join(text.split()).casefold()
                checks.append({'page': number, 'type': label['type'],
                               'facts_found': sum(f.casefold() in normalized for f in label['facts']),
                               'facts_expected': len(label['facts'])})
            row = {'parser': name, 'seconds': round(time.perf_counter()-start, 3),
                   'pages': len(pages), 'fact_checks': checks,
                   'facts_found': sum(c['facts_found'] for c in checks),
                   'facts_expected': sum(c['facts_expected'] for c in checks)}
            (out/(name+'_pages.json')).write_text(json.dumps(pages, indent=2), encoding='utf-8')
        except Exception as exc:
            row = {'parser': name, 'seconds': round(time.perf_counter()-start, 3),
                   'error_type': type(exc).__name__}
        report['results'].append(row)
        (out/'results.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
        print(json.dumps({k: v for k, v in row.items() if k != 'fact_checks'}), flush=True)

    from pypdf import PdfReader
    measure('pypdf', lambda: [p.extract_text() or '' for p in PdfReader(source).pages])
    import pymupdf
    def layout():
        with pymupdf.open(source) as doc:
            return [r['text'] for r in doc.to_markdown(page_chunks=True, use_ocr=False, show_progress=False)]
    measure('pymupdf4llm', layout)
    if args.docling:
        from backend.utils.docling_bridge import extract_pdf
        measure('docling_rapidocr', lambda: extract_pdf(source)['pages'])
        if 'error_type' not in report['results'][-1]:
            from backend.utils.loaders import _validate_ocr_coverage, DocumentLoadError
            pages = json.loads((out/'docling_rapidocr_pages.json').read_text(encoding='utf-8'))
            try:
                _validate_ocr_coverage(source.read_bytes(), pages)
                report['docling_production_coverage_guard'] = 'accepted'
            except DocumentLoadError as exc:
                report['docling_production_coverage_guard'] = 'rejected: ' + str(exc)
            (out/'results.json').write_text(json.dumps(report, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
