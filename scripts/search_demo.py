"""Search active corpus evidence: python -m scripts.search_demo "your question"."""

import argparse
import json


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("question")
    parser.add_argument("--top-k", type=int, default=3)
    parser.add_argument("--json", action="store_true", help="Print complete source metadata and passage text as JSON")
    args = parser.parse_args(argv)
    if args.top_k <= 0:
        parser.error("--top-k must be positive")
    if not args.question.strip():
        parser.error("question must contain text")

    from backend.services.document_processing_service import DocumentProcessingService

    with DocumentProcessingService() as service:
        hits = service.search(args.question, top_k=args.top_k)
    if args.json:
        print(json.dumps({"question": args.question, "hits": hits}, ensure_ascii=False, indent=2, default=str))
    else:
        print(f"Q: {args.question}\n")
        if not hits:
            print("No eligible evidence found.")
        for hit in hits:
            location = f"page {hit['page_number']}" if hit.get("page_number") is not None else hit.get("section_heading") or "document text"
            print(f"#{hit['rank']} score={hit['similarity_score']:.3f} doc={hit.get('doc_name', '<unknown>')!r} v{hit.get('version', '?')} {location}")
            print(f"  doc_id={hit.get('doc_id')} source_hash={hit.get('source_hash')}")
            print(f"  offsets={hit.get('start_char')}:{hit.get('end_char')}  {hit['text'][:300]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
