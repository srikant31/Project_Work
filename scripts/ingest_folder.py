"""Ingest approved documents, or inspect extraction/chunks without store access.

    python -m scripts.ingest_folder --folder data/documents --dry-run
    python -m scripts.ingest_folder --folder data/documents --category-name "IT Support"

Source identity is the file's relative POSIX path (including extension) within
the supplied folder, scoped by category. Reuse the same corpus root on reruns.
"""

import argparse
from pathlib import Path

from scripts.preview_documents import collect_paths, create_embedder, error_kind, preview_paths, write_report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--folder", type=Path, default=Path("data/documents"))
    parser.add_argument("--recursive", action="store_true")
    parser.add_argument("--category-name", default="IT Support")
    parser.add_argument("--title-prefix", default="")
    parser.add_argument("--chunk-size", type=int, default=200)
    parser.add_argument("--overlap", type=int, default=40)
    parser.add_argument("--dry-run", action="store_true", help="Extract and chunk only; no database/vector connection")
    parser.add_argument("--output", type=Path, help="Optional JSON report (includes extracted chunk text in dry run)")
    parser.add_argument("--allow-model-download", action="store_true", help="Allow downloading an uncached embedding model")
    args = parser.parse_args(argv)
    try:
        paths = collect_paths(args.folder, recursive=args.recursive)
    except (FileNotFoundError, NotADirectoryError, ValueError) as exc:
        parser.error(str(exc))
    if not paths:
        parser.error(f"No files found in {args.folder}")

    embedder = create_embedder(args.allow_model_download)
    if args.dry_run:
        report = preview_paths(paths, embedder, args.chunk_size, args.overlap, root=args.folder)
        if args.output:
            write_report(args.output, report)
        return 1 if report["failed"] or report["rejected"] else 0

    # Import only on the write path: --dry-run must not open PostgreSQL/Chroma.
    from backend.services.document_processing_service import DocumentProcessingService

    rows = []
    with DocumentProcessingService(embedder=embedder) as service:
        for path in paths:
            source_key = path.relative_to(args.folder).as_posix()
            try:
                result = service.process_file(
                    path,
                    category_name=args.category_name,
                    source_key=source_key,
                    doc_name=args.title_prefix + path.stem,
                    chunk_size=args.chunk_size,
                    overlap=args.overlap,
                )
                status = "skipped" if result.get("skipped") else "ingested"
                rows.append({"file": source_key, "status": status, **result})
                print(f"[{status.upper()}] {source_key}: version {result['version']}, {result['chunk_count']} chunks")
                for warning in result.get("extraction_warnings", result.get("warnings", [])):
                    print(f"  Warning: {warning}")
                if result.get("cleanup_pending"):
                    print(f"  Cleanup pending; inspect python -m scripts.reconcile_stores --doc-id {result['doc_id']}")
            except Exception as exc:
                status = error_kind(exc)
                rows.append({"file": source_key, "status": status, "error": f"{type(exc).__name__}: {exc}"})
                print(f"[{status.upper()}] {source_key}: {type(exc).__name__}: {exc}")

    report = {
        "mode": "ingestion",
        "category_name": args.category_name,
        "ingested": sum(row["status"] == "ingested" for row in rows),
        "skipped": sum(row["status"] == "skipped" for row in rows),
        "rejected": sum(row["status"].startswith("rejected") for row in rows),
        "failed": sum(row["status"] == "failed" for row in rows),
        "files": rows,
    }
    print(f"\nIngested: {report['ingested']}; skipped: {report['skipped']}; rejected: {report['rejected']}; failed: {report['failed']}")
    if args.output:
        write_report(args.output, report)
    return 1 if report["failed"] or report["rejected"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
