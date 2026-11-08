"""Optional isolated Docling runtime: local PDF -> physical-page Markdown JSON."""
import argparse
import json
import os
from importlib.metadata import version
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()

    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions, RapidOcrOptions
    from docling.datamodel.accelerator_options import AcceleratorOptions, AcceleratorDevice
    from docling.document_converter import DocumentConverter, PdfFormatOption

    options = PdfPipelineOptions()
    options.do_ocr = True
    options.do_table_structure = True
    options.accelerator_options = AcceleratorOptions(num_threads=2, device=AcceleratorDevice.CPU)
    ocr = RapidOcrOptions(backend="onnxruntime")
    ocr_mode = os.getenv('DOCLING_OCR_MODE', 'selective').strip().lower()
    if ocr_mode not in {'selective', 'full'}:
        raise ValueError('DOCLING_OCR_MODE must be selective or full')
    if "mode" in type(ocr).model_fields:
        from docling.datamodel.pipeline_options import OcrMode
        ocr.mode = OcrMode.FULL_PAGE if ocr_mode == 'full' else OcrMode.PDF_AWARE_LAYOUT_REGIONS
    else:
        ocr.force_full_page_ocr = ocr_mode == 'full'
    options.ocr_options = ocr
    converter = DocumentConverter(format_options={
        InputFormat.PDF: PdfFormatOption(pipeline_options=options),
    })
    result = converter.convert(args.source, raises_on_error=True)
    if result.status.value != "success":
        raise ValueError("Docling reported incomplete conversion")
    doc = result.document
    if sorted(doc.pages) != list(range(1, len(doc.pages) + 1)):
        raise ValueError("Docling did not return complete physical page numbering")
    pages = [doc.export_to_markdown(page_no=n) for n in sorted(doc.pages)]
    if not pages or not any(p.strip() for p in pages):
        raise ValueError("OCR produced no text")
    geometry = []
    for item, _level in doc.iterate_items():
        for location in getattr(item, 'prov', []):
            geometry.append({'class': str(getattr(item, 'label', 'unknown')),
                             'page_number': location.page_no,
                             'bbox': location.bbox.model_dump(mode='json')})
    args.output.write_text(json.dumps({
        "pages": pages,
        "extractor_version": "docling-" + version("docling") + "-rapidocr-" + ocr_mode + "-v2",
        "metadata": {"layout_elements": geometry},
        "warnings": [
            "Docling local layout/table analysis and " + ocr_mode + " OCR were used. "
            "OCR may misread numbers, names and table structure; inspect the "
            "extracted evidence before relying on it.",
        ],
    }), encoding="utf-8")


if __name__ == "__main__":
    main()
