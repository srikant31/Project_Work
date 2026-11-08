"""Layout-aware PDF extraction; OCR is explicitly delegated to Docling.

Routing signals are conservative heuristics, not semantic quality guarantees.
No parser is allowed to silently discard physical pages or most native words.
"""
from collections import Counter
from importlib.metadata import version
import re


class PdfQualityError(ValueError):
    pass


def _words(text):
    return Counter(re.findall(r"\w+", text.casefold()))


def extract_pdf(path, native_pages):
    import pymupdf

    with pymupdf.open(path) as document:
        if document.page_count != len(native_pages):
            raise PdfQualityError('Physical page count differs between parsers')
        diagnostics = []
        reasons = []
        for index, page in enumerate(document):
            area = max(page.rect.get_area(), 1)
            image_fraction = min(1, sum(
                (pymupdf.Rect(item['bbox']) & page.rect).get_area()
                for item in page.get_image_info()
            ) / area)
            text = native_pages[index]
            signals = []
            if image_fraction >= 0.10:
                signals.append('substantial_image_content')
            if text.count('\ufffd') / max(len(text), 1) > 0.01:
                signals.append('replacement_characters')
            if image_fraction >= 0.02 and len(text.strip()) < 20:
                signals.append('sparse_text_with_image')
            diagnostics.append({'page_number': index + 1,
                                'image_area_fraction': round(image_fraction, 4),
                                'signals': signals})
            reasons.extend(f'page {index + 1}: {signal}' for signal in signals)
        if reasons:
            raise PdfQualityError('; '.join(reasons))

        # Version 2.x integrates PyMuPDF4LLM into Document. Disable its optional
        # implicit OCR engines so this application always chooses OCR explicitly.
        rows = document.to_markdown(page_chunks=True, use_ocr=False,
                                    show_progress=False, header=True, footer=True,
                                    write_images=False, embed_images=False)
        if not isinstance(rows, list) or len(rows) != len(native_pages):
            raise PdfQualityError('Incomplete page output')
        pages = []
        for index, row in enumerate(rows):
            if row.get('metadata', {}).get('page_number') != index + 1:
                raise PdfQualityError('Incorrect physical page order')
            text = row.get('text')
            if not isinstance(text, str):
                raise PdfQualityError('Invalid page text')
            expected = _words(native_pages[index])
            actual = _words(text)
            coverage = sum((expected & actual).values()) / max(sum(expected.values()), 1)
            if expected and coverage < 0.8:
                raise PdfQualityError(f'page {index + 1}: native-word coverage below 80%')
            diagnostics[index]['native_word_coverage'] = round(coverage, 4)
            # Retain layout objects independently of Markdown; these boxes refer
            # to source geometry, not the normalized character offset space.
            boxes = row.get('page_boxes', [])
            diagnostics[index]['layout_boxes'] = [
                {'class': str(box.get('class', 'unknown')),
                 'bbox': [float(v) for v in box.get('bbox', [])]}
                for box in boxes if isinstance(box, dict)
            ]
            pages.append(text)
        return {'pages': pages,
                'extractor_version': f'pymupdf4llm-{version("pymupdf")}-layout-v1',
                'warnings': ['PyMuPDF4LLM layout extraction used; OCR disabled in this adapter. Review tables and reading order.'],
                'metadata': {'route': 'pymupdf4llm', 'routing_version': 'pdf-router-v1',
                             'pages': diagnostics}}
