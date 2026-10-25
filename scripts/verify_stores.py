"""Read-only PostgreSQL/Chroma consistency and source-traceability audit.

    python -m scripts.verify_stores
    python -m scripts.verify_stores --doc-id UUID --json

Legacy rows are reported separately: missing historical provenance cannot be
reconstructed by a successful health check. No schema migration, deletion,
collection creation or repair is performed by this command.
"""

import argparse
import hashlib
import json
from datetime import timedelta
from pathlib import Path
from uuid import UUID


def audit_stores(session, collection, *, stale_minutes=60, doc_id=None, embedder=None, recount_tokens=True):
    """Return checks without modifying either store (also usable in isolated tests)."""
    from sqlalchemy import func, select
    from backend.database.models import Document, DocumentChunk, IngestionJob

    documents = session.query(Document).all()
    chunks = session.query(DocumentChunk).all()
    jobs = session.query(IngestionJob).all()
    all_docs = {str(doc.doc_id): doc for doc in documents}
    all_chunks = {str(chunk.chunk_id): chunk for chunk in chunks}
    scoped_docs = {key: doc for key, doc in all_docs.items() if doc_id is None or key == str(doc_id)}
    scoped_chunks = {key: chunk for key, chunk in all_chunks.items() if str(chunk.document_id) in scoped_docs}
    vectors = collection.get(include=["metadatas", "documents"])
    vector_rows = dict(zip(vectors.get("ids") or [], vectors.get("metadatas") or []))
    vector_texts = dict(zip(vectors.get("ids") or [], vectors.get("documents") or []))
    checks = []

    def record(name, failures, detail, *, warning=False):
        checks.append({"name": name, "status": ("WARN" if warning else "FAIL") if failures else "PASS", "count": len(failures), "detail": detail, "ids": list(failures)[:20]})

    if doc_id is not None and not scoped_docs:
        record("Requested document exists", [str(doc_id)], "No matching document version")
        return {"documents": 0, "managed_documents": 0, "legacy_documents": 0, "checks": checks}

    indexed = {key: chunk for key, chunk in scoped_chunks.items() if chunk.is_active and scoped_docs[str(chunk.document_id)].status == "active"}
    eligible = {key: chunk for key, chunk in indexed.items() if scoped_docs[str(chunk.document_id)].is_online}
    populated_documents = {str(chunk.document_id) for chunk in indexed.values()}
    empty_active = [key for key, doc in scoped_docs.items() if doc.status == "active" and key not in populated_documents]
    record("Active documents contain eligible chunks", empty_active, "A published document must contain retrievable passages")
    missing = sorted(set(eligible) - set(vector_rows))
    record("Eligible PostgreSQL chunks have vectors", missing, "Active document plus active chunk defines eligibility")
    wrong_reference = [key for key, chunk in eligible.items() if chunk.embedding_vector_id != f"{collection.name}:{key}"]
    record("Eligible chunks reference this collection", wrong_reference, "Stored vector references must identify the selected collection and chunk")
    wrong_owner = [key for key in eligible if key in vector_rows and str((vector_rows[key] or {}).get("doc_id")) != str(eligible[key].document_id)]
    record("Vector ownership matches PostgreSQL", wrong_owner, "Vector metadata doc_id must match its chunk")
    mismatched_text = [key for key in eligible if key in vector_rows and vector_texts.get(key) != eligible[key].chunk_text]
    record("Vector passage text matches PostgreSQL", mismatched_text, "Retrieved evidence text must agree between stores")
    inactive_vectors = [key for key in eligible if key in vector_rows and (vector_rows[key] or {}).get("is_active") is not True]
    record("Eligible vectors are searchable", inactive_vectors, "Published vectors must not remain staged/inactive")

    if doc_id is None:
        ghosts = sorted(set(vector_rows) - set(all_chunks))
    else:
        ghosts = [key for key, metadata in vector_rows.items() if str((metadata or {}).get("doc_id")) == str(doc_id) and key not in all_chunks]
    record("Vectors resolve to PostgreSQL chunks", ghosts, "Unknown vectors require explicit scoped investigation/reconciliation")
    obsolete = [key for key in vector_rows if key in scoped_chunks and key not in indexed]
    record("Noneligible vectors awaiting cleanup", obsolete, "Staged/replaced/failed versions cannot be returned by PostgreSQL-authoritative search", warning=True)

    seen_indices, duplicate_indices = set(), []
    for key, chunk in scoped_chunks.items():
        identity = (str(chunk.document_id), chunk.chunk_index)
        if identity in seen_indices:
            duplicate_indices.append(key)
        seen_indices.add(identity)
    record("Chunk indices are unique per document version", duplicate_indices, "Indices must uniquely identify a chunk within its version")

    managed = {key: doc for key, doc in scoped_docs.items() if doc.pipeline_fingerprint}
    legacy = sorted(set(scoped_docs) - set(managed))
    record("Legacy provenance is explicitly unknown", legacy, "Legacy documents need deliberate reingestion before token/source guarantees apply", warning=True)
    source_versions, duplicates = set(), []
    for key, doc in managed.items():
        identity = (str(doc.category_id), doc.source_key)
        if doc.status == "active":
            if identity in source_versions:
                duplicates.append(key)
            source_versions.add(identity)
    record("One active managed version per category/source", duplicates, "Document title alone does not define source identity")

    provenance_errors = []
    for key, doc in managed.items():
        if not doc.source_key or not doc.source_hash or not doc.text_hash or not doc.pipeline_config or not doc.extracted_text:
            provenance_errors.append(key)
        elif hashlib.sha256(doc.extracted_text.encode("utf-8")).hexdigest() != doc.text_hash:
            provenance_errors.append(key)
    record("Managed documents have complete provenance", provenance_errors, "Source key/checksums, extracted source and pipeline configuration are mandatory")

    source_errors, page_errors, token_errors = [], [], []
    token_documents = {key: doc for key, doc in managed.items() if doc.status == "active"}
    if recount_tokens and token_documents and embedder is None:
        from scripts.preview_documents import create_embedder

        embedder = create_embedder()
    for key, chunk in eligible.items():
        doc = managed.get(str(chunk.document_id))
        if doc is None:
            continue
        start, end = chunk.start_char, chunk.end_char
        if start is None or end is None or not (0 <= start < end <= len(doc.extracted_text or "")) or doc.extracted_text[start:end] != chunk.chunk_text:
            source_errors.append(key)
        if doc.doc_type == "pdf":
            if chunk.page_number is None or chunk.page_number < 1 or doc.page_count is None or chunk.page_number > doc.page_count:
                page_errors.append(key)
        elif chunk.page_number is not None:
            page_errors.append(key)
        embedding_config = (doc.pipeline_config or {}).get("embedding", {})
        limit = embedding_config.get("max_sequence_length")
        chunk_limit = (doc.pipeline_config or {}).get("chunk_size")
        if not isinstance(chunk.token_count, int) or chunk.token_count <= 0 or not isinstance(limit, int) or chunk.token_count > limit or not isinstance(chunk_limit, int) or chunk.token_count > chunk_limit:
            token_errors.append(key)
        elif recount_tokens:
            current_config = embedder.config
            if any(embedding_config.get(field) != current_config.get(field) for field in ("model_name", "revision", "tokenizer", "max_sequence_length")):
                token_errors.append(key)
                continue
            tokenizer = embedder.tokenizer
            payload = len(tokenizer.encode(chunk.chunk_text, add_special_tokens=False))
            full = len(tokenizer.encode(chunk.chunk_text, add_special_tokens=True))
            if payload != chunk.token_count or full > limit:
                token_errors.append(key)
    record("Managed chunk offsets map to exact source text", source_errors, "Offsets refer to the stored normalized extracted source")
    record("Managed page references reflect file format", page_errors, "Only PDFs carry physical page numbers; other formats use sections and offsets")
    record("Managed chunks satisfy tokenizer/model limits", token_errors, "Payload counts exclude special tokens; final model input must include them within the limit")
    if not recount_tokens:
        checks.append({"name": "Tokenizer recount skipped", "status": "WARN", "count": len(token_documents), "detail": "Only stored token metadata was inspected", "ids": []})

    # Compare naive timestamp columns using PostgreSQL's session timezone,
    # rather than assuming the database stores naive UTC values.
    cutoff = session.execute(select(func.now())).scalar_one() - timedelta(minutes=stale_minutes)
    stale = []
    for job in jobs:
        if str(job.document_id) not in scoped_docs or job.status != "processing":
            continue
        started = job.started_at
        threshold = cutoff.replace(tzinfo=None) if started is not None and started.tzinfo is None else cutoff
        if started is None or started < threshold:
            stale.append(str(job.id))
    record("No stale processing jobs", stale, f"Only jobs older than {stale_minutes} minutes count as stale")
    pending = [key for key, doc in scoped_docs.items() if doc.cleanup_pending]
    record("No pending cleanup", pending, "Use report-only reconciliation, then apply to an explicit document if needed", warning=True)
    return {"documents": len(scoped_docs), "managed_documents": len(managed), "legacy_documents": len(legacy), "eligible_chunks": len(eligible), "checks": checks}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--doc-id", type=UUID)
    parser.add_argument("--chroma-path", type=Path, default=Path("chroma_db"))
    parser.add_argument("--collection", default="chunks_minilm_v1")
    parser.add_argument("--stale-minutes", type=int, default=60)
    parser.add_argument("--skip-token-recount", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if args.stale_minutes <= 0:
        parser.error("--stale-minutes must be positive")
    if not (args.chroma_path / "chroma.sqlite3").is_file():
        parser.error(f"Existing Chroma database not found: {args.chroma_path}; no collection was created")

    import chromadb
    from sqlalchemy import text
    from backend.database.connection import SessionLocal

    client = chromadb.PersistentClient(path=str(args.chroma_path))
    collection = client.get_collection(args.collection)
    with SessionLocal() as session:
        session.execute(text("SET TRANSACTION READ ONLY"))
        report = audit_stores(session, collection, stale_minutes=args.stale_minutes, doc_id=args.doc_id, recount_tokens=not args.skip_token_recount)
        session.rollback()
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    else:
        print(f"Documents: {report['documents']}; managed: {report['managed_documents']}; legacy: {report['legacy_documents']}")
        for row in report["checks"]:
            print(f"[{row['status']}] {row['name']} ({row['count']})")
            if row["status"] != "PASS":
                print(f"  {row['detail']}")
                if row["ids"]:
                    print(f"  ids: {', '.join(row['ids'])}")
    return 1 if any(row["status"] == "FAIL" for row in report["checks"]) else 0


if __name__ == "__main__":
    raise SystemExit(main())
