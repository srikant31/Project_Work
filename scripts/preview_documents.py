"""Inspect extraction and token-bounded chunks without connecting to stores.

    python -m scripts.preview_documents --folder data/documents --output preview.json

JSON includes full chunk evidence for manual review and later evaluation data.
It contains document content: store the report alongside the approved corpus.
"""

import argparse
import json
from dataclasses import asdict
from pathlib import Path


def collect_paths(folder: Path, recursive: bool = False) -> list[Path]:
    folder = Path(folder)
    if not folder.exists():
        raise FileNotFoundError(f"Folder not found: {folder}")
    if not folder.is_dir():
        raise NotADirectoryError(f"Expected a folder: {folder}")
    paths = folder.rglob("*") if recursive else folder.iterdir()
    return sorted((path for path in paths if path.is_file()), key=lambda path: path.as_posix())


def create_embedder(allow_model_download=False):
    from backend.services.embedding_service import EmbeddingService

    return EmbeddingService(local_files_only=not allow_model_download)


def error_kind(exc: Exception) -> str:
    from backend.utils.loaders import ScannedPdfError, UnsupportedFormatError

    if isinstance(exc, ScannedPdfError):
        return "rejected_scanned"
    if isinstance(exc, UnsupportedFormatError):
        return "rejected_unsupported"
    return "failed"


def preview_file(path: Path, embedder, chunk_size=200, overlap=40) -> dict:
    from backend.utils.chunking import chunk_document
    from backend.utils.loaders import load_document

    loaded = load_document(path)
    chunks = chunk_document(
        loaded, embedder.tokenizer, embedder.max_sequence_length,
        chunk_size=chunk_size, overlap=overlap,
    )
    return {
        "file": str(path), "status": "previewed", "doc_type": loaded.doc_type,
        "title": loaded.title, "source_hash": loaded.source_hash,
        "source_size": loaded.source_size, "page_count": loaded.page_count,
        "extractor_version": loaded.extractor_version, "warnings": loaded.warnings,
        "source_text": loaded.source_text, "block_count": len(loaded.blocks),
        "chunk_count": len(chunks), "chunks": [asdict(chunk) for chunk in chunks],
    }


def preview_paths(paths, embedder, chunk_size=200, overlap=40, root=None) -> dict:
    rows = []
    for path in paths:
        label = path.relative_to(root).as_posix() if root else str(path)
        try:
            row = preview_file(path, embedder, chunk_size, overlap)
            row["file"] = label
            print(f"[PREVIEWED] {label}: {row['block_count']} blocks, {row['chunk_count']} chunks, {row['source_size']} bytes")
            for warning in row["warnings"]:
                print(f"  Warning: {warning}")
        except Exception as exc:
            row = {"file": label, "status": error_kind(exc), "error": f"{type(exc).__name__}: {exc}"}
            print(f"[{row['status'].upper()}] {label}: {row['error']}")
        rows.append(row)
    report = {
        "mode": "extraction_chunk_preview", "chunk_size": chunk_size,
        "overlap": overlap, "embedding": embedder.config,
        "previewed": sum(row["status"] == "previewed" for row in rows),
        "rejected": sum(row["status"].startswith("rejected") for row in rows),
        "failed": sum(row["status"] == "failed" for row in rows), "files": rows,
    }
    print(f"\nPreviewed: {report['previewed']}; rejected: {report['rejected']}; failed: {report['failed']}")
    return report


def write_report(path: Path, report) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    print(f"JSON report: {path.resolve()}")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--folder", type=Path, default=Path("data/documents"))
    parser.add_argument("--recursive", action="store_true")
    parser.add_argument("--chunk-size", type=int, default=200)
    parser.add_argument("--overlap", type=int, default=40)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--allow-model-download", action="store_true")
    args = parser.parse_args(argv)
    try:
        paths = collect_paths(args.folder, args.recursive)
    except (FileNotFoundError, NotADirectoryError) as exc:
        parser.error(str(exc))
    if not paths:
        parser.error(f"No files found in {args.folder}")
    report = preview_paths(paths, create_embedder(args.allow_model_download), args.chunk_size, args.overlap, root=args.folder)
    if args.output:
        write_report(args.output, report)
    return 1 if report["failed"] or report["rejected"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
