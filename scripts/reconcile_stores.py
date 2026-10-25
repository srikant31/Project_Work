"""Report interrupted ingestion/cleanup; apply only to an explicit document.

    python -m scripts.reconcile_stores
    python -m scripts.reconcile_stores --doc-id UUID --apply

Default is report-only. No global reset or broad automatic repair is performed.
"""

import argparse
import json
from pathlib import Path
from uuid import UUID


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--doc-id", type=UUID, help="Limit reconciliation to one document version")
    parser.add_argument("--apply", action="store_true", help="Apply only safe recovery operations for --doc-id")
    parser.add_argument("--stale-minutes", type=int, default=60)
    parser.add_argument("--chroma-path", type=Path, default=Path("chroma_db"), help="Existing vector-store directory")
    parser.add_argument("--collection", default="chunks_minilm_v1", help="Original vector collection for the document version")
    parser.add_argument("--allow-model-download", action="store_true", help="Allow an uncached model download only when --apply repairs active vectors")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if args.apply and args.doc_id is None:
        parser.error("--apply requires --doc-id; review the report for that document first")
    if args.stale_minutes < 0:
        parser.error("--stale-minutes must be nonnegative")

    from backend.services.document_processing_service import DocumentProcessingService
    from backend.services.retrieval_service import RetrievalService

    # Recovery must target an existing index. A mistyped path/collection must
    # never create a new empty store and make cleanup appear successful.
    try:
        retriever = RetrievalService(
            persist_path=args.chroma_path, collection_name=args.collection, create=False,
        )
    except Exception as exc:
        parser.error(f"Cannot open existing vector store ({type(exc).__name__}): {exc}")
    embedder = None
    if args.apply and args.allow_model_download:
        with DocumentProcessingService(retriever=retriever) as probe:
            plan = probe.reconcile(doc_id=args.doc_id, apply=False, stale_minutes=args.stale_minutes)
        if any(row.get("action") == "repair_active_vectors" for row in plan):
            from scripts.preview_documents import create_embedder

            embedder = create_embedder(allow_model_download=True)
    with DocumentProcessingService(retriever=retriever, embedder=embedder) as service:
        rows = service.reconcile(doc_id=args.doc_id, apply=args.apply, stale_minutes=args.stale_minutes)
    if args.json:
        print(json.dumps({"applied": args.apply, "rows": rows}, ensure_ascii=False, indent=2, default=str))
    else:
        print("Recovery actions applied" if args.apply else "Report only; no store contents changed")
        for row in rows:
            print(f"[{row.get('status', 'unknown')}] doc_id={row.get('doc_id')} action={row.get('action')} {row.get('detail', '')}")
        if not rows:
            print("No reconciliation actions found.")
    # status is the DOCUMENT lifecycle: a retired stage correctly becomes
    # 'failed' even when reconciliation and vector cleanup succeeded.
    unresolved = any(
        row.get("error") or row.get("status") == "error"
        or (args.apply and "cleanup pending" in row.get("detail", "").lower())
        for row in rows
    )
    return 1 if unresolved else 0


if __name__ == "__main__":
    raise SystemExit(main())
