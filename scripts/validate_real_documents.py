"""Validate authored documents and retrieval in disposable real stores.

    python -m scripts.validate_real_documents --strict

This creates a generated PostgreSQL schema and a temporary Chroma database.
It never migrates or ingests into the application's ordinary tables/index.
The corpus and questions are fixed inputs; retrieval misses are reported,
without changing the questions or claiming answer/refusal quality.
"""

import argparse
import json
import re
import tempfile
import time
import uuid
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path


DEFAULT_ROOT = Path("output/real_document_validation")


def normalized(text):
    return " ".join(text.split())


def normalized_with_offsets(text):
    """Match split/join whitespace normalization while preserving provenance."""
    parts, offsets = [], []
    previous_end = 0
    for word in re.finditer(r"\S+", text):
        if parts:
            parts.append(" ")
            offsets.append((previous_end, word.start()))
        parts.append(word.group())
        offsets.extend((position, position + 1) for position in range(word.start(), word.end()))
        previous_end = word.end()
    return "".join(parts), offsets


def coverage(text, intervals, *, start=0, end=None):
    """Measure source-character coverage without treating whitespace as lost."""
    end = len(text) if end is None else end
    merged = []
    for left, right in sorted(intervals):
        left, right = max(start, left), min(end, right)
        if left >= right:
            continue
        if merged and left <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], right)
        else:
            merged.append([left, right])
    total = missing = pointer = 0
    examples = []
    for position in range(start, end):
        if text[position].isspace():
            continue
        total += 1
        while pointer < len(merged) and merged[pointer][1] <= position:
            pointer += 1
        if pointer >= len(merged) or merged[pointer][0] > position:
            missing += 1
            if len(examples) < 20:
                examples.append(position)
    return {"nonwhitespace_characters": total, "covered_characters": total - missing,
            "missing_characters": missing, "missing_offset_examples": examples,
            "covered_intervals": merged}


def safe_error(exc):
    # Database/driver exception messages can contain connection details or
    # statement parameters. Keep reports independent of environment secrets.
    from backend.utils.loaders import DocumentLoadError

    result = {"type": type(exc).__name__}
    if isinstance(exc, (DocumentLoadError, FileNotFoundError, IsADirectoryError)):
        result["message"] = str(exc)
    return result


def source_path(root, filename):
    if not isinstance(filename, str) or not filename or Path(filename).is_absolute():
        raise ValueError("Manifest filenames must be nonempty relative paths")
    candidate = (root / filename).resolve()
    if not candidate.is_relative_to(root.resolve()):
        raise ValueError("Manifest filename escapes the document directory")
    return candidate


def validate_inputs(documents_dir, manifest, queries):
    from backend.utils import loaders

    if not documents_dir.is_dir():
        raise ValueError("Document directory does not exist")
    sources, negatives = manifest.get("sources"), manifest.get("negatives", [])
    questions = queries.get("questions")
    if not isinstance(sources, list) or not sources or not isinstance(negatives, list):
        raise ValueError("Manifest requires a nonempty sources list and a negatives list")
    if not isinstance(questions, list) or not questions:
        raise ValueError("Queries require a nonempty questions list")
    names = set()
    for source in sources:
        filename = source["filename"]
        source_path(documents_dir, filename)
        if filename in names or source.get("doc_type") not in {"txt", "md", "pdf", "docx"}:
            raise ValueError("Source filenames must be unique and formats supported")
        names.add(filename)
        fragments = source.get("expected_fragments", [])
        if not fragments or any(not isinstance(f.get("text"), str) or not f["text"].strip() for f in fragments):
            raise ValueError("Each positive source requires authored nonempty fragments")
        for fragment in fragments:
            if "expected_page" in fragment and (
                not isinstance(fragment["expected_page"], int) or fragment["expected_page"] < 1
            ):
                raise ValueError("Expected pages must be positive physical PDF page numbers")
        if any(not isinstance(text, str) or not text.strip() for text in source.get("order_fragments", [])):
            raise ValueError("Order fragments must be nonempty strings")
    for negative in negatives:
        source_path(documents_dir, negative["filename"])
        expected = getattr(loaders, negative.get("expected_exception", ""), None)
        if negative.get("expected_exception") in {"FileNotFoundError", "IsADirectoryError"}:
            continue
        if not isinstance(expected, type) or not issubclass(expected, Exception):
            raise ValueError("Unknown expected extraction exception")
    ids = set()
    for question in questions:
        if question.get("id") in ids or not question.get("id"):
            raise ValueError("Question IDs must be nonempty and unique")
        ids.add(question["id"])
        if not isinstance(question.get("question"), str) or not question["question"].strip():
            raise ValueError("Questions must be nonempty strings")
        if not isinstance(question.get("answerable"), bool):
            raise ValueError("Questions require explicit boolean answerable labels")
        if "expected_page" in question and question["expected_page"] is not None and (
            not isinstance(question["expected_page"], int) or question["expected_page"] < 1
        ):
            raise ValueError("Question expected_page must be a positive physical PDF page number")
        if question["answerable"]:
            evidence = question.get("evidence")
            if question.get("source_file") not in names or not isinstance(evidence, list) or not evidence:
                raise ValueError("Answerable questions require a positive source and authored evidence")
            if any(not isinstance(text, str) or not text.strip() for text in evidence):
                raise ValueError("Evidence fragments must be nonempty strings")


def counts(factory, retriever):
    from backend.database.models import Document, DocumentChunk, IngestionJob

    with factory() as session:
        return {
            "documents": session.query(Document).count(),
            "chunks": session.query(DocumentChunk).count(),
            "jobs": session.query(IngestionJob).count(),
            "vectors": retriever.count(),
        }


def inspect_source(path, expected, embedder):
    from backend.utils.chunking import chunk_document
    from backend.utils.loaders import load_document

    loaded = load_document(path)
    chunks = chunk_document(loaded, embedder.tokenizer, embedder.max_sequence_length)
    text = normalized(loaded.source_text)
    fragments = []
    for authored in expected["expected_fragments"]:
        fragment = normalized(authored["text"])
        page = authored.get("expected_page")
        present = fragment in text
        on_page = None if page is None else (
            loaded.doc_type == "pdf" and page <= len(loaded.pages)
            and fragment in normalized(loaded.pages[page - 1])
        )
        fragments.append({**authored, "present": present, "on_expected_page": on_page,
                          "passed": present and on_page is not False})
    cursor = 0
    order = []
    for fragment in expected.get("order_fragments", []):
        position = text.find(normalized(fragment), cursor)
        order.append({"text": fragment, "normalized_position": position, "passed": position >= 0})
        if position >= 0:
            cursor = position + len(normalized(fragment))
    block_offsets = all(loaded.source_text[b.start_char:b.end_char] == b.text for b in loaded.blocks)
    whole_coverage = coverage(loaded.source_text, [(c.start_char, c.end_char) for c in chunks])
    page_count_matches = expected.get("expected_page_count", loaded.page_count) == loaded.page_count
    blank_pages = [number for number, text in enumerate(loaded.pages, start=1) if not text.strip()] if loaded.doc_type == "pdf" else []
    blank_pages_match = expected.get("expected_blank_pages", blank_pages) == blank_pages
    token_checks = []
    for chunk in chunks:
        payload = len(embedder.tokenizer(chunk.text, add_special_tokens=False, truncation=False)["input_ids"])
        full = len(embedder.tokenizer(chunk.text, add_special_tokens=True, truncation=False)["input_ids"])
        exact = loaded.source_text[chunk.start_char:chunk.end_char] == chunk.text
        page_valid = (
            chunk.page_number is not None and 1 <= chunk.page_number <= loaded.page_count
            if loaded.doc_type == "pdf" else chunk.page_number is None
        )
        token_checks.append({**asdict(chunk), "full_token_count": full,
                             "passed": exact and page_valid and payload == chunk.token_count
                             and payload <= 200 and full <= embedder.max_sequence_length})
    passed = (
        loaded.doc_type == expected["doc_type"] and bool(chunks) and block_offsets
        and page_count_matches and blank_pages_match and whole_coverage["missing_characters"] == 0
        and all(f["passed"] for f in fragments) and all(f["passed"] for f in order)
        and all(c["passed"] for c in token_checks)
    )
    return loaded, {"passed": passed, "doc_type": loaded.doc_type,
                    "source_hash": loaded.source_hash, "source_size": loaded.source_size,
                    "source_text": loaded.source_text, "page_count": loaded.page_count,
                    "expected_page_count": expected.get("expected_page_count"),
                    "page_count_matches": page_count_matches, "blank_physical_pages": blank_pages,
                    "expected_blank_pages": expected.get("expected_blank_pages"),
                    "blank_pages_match": blank_pages_match, "whole_source_coverage": whole_coverage,
                    "warnings": loaded.warnings, "blocks": [asdict(b) for b in loaded.blocks],
                    "block_offsets_valid": block_offsets, "fragments": fragments,
                    "order": order, "chunks": token_checks}


def inspect_persisted(factory, doc_id, loaded, embedder):
    from backend.database.models import Document, DocumentChunk

    with factory() as session:
        document = session.get(Document, uuid.UUID(doc_id))
        rows = session.query(DocumentChunk).filter_by(document_id=document.doc_id).order_by(DocumentChunk.chunk_index).all()
        checks = []
        for row in rows:
            payload = len(embedder.tokenizer(row.chunk_text, add_special_tokens=False, truncation=False)["input_ids"])
            full = len(embedder.tokenizer(row.chunk_text, add_special_tokens=True, truncation=False)["input_ids"])
            exact = document.extracted_text[row.start_char:row.end_char] == row.chunk_text
            checks.append({"chunk_id": str(row.chunk_id), "index": row.chunk_index,
                           "token_count": row.token_count, "full_token_count": full,
                           "source_offsets_valid": exact, "page_number": row.page_number,
                           "start_char": row.start_char, "end_char": row.end_char,
                           "passed": exact and row.token_count == payload and payload <= 200
                           and full <= embedder.max_sequence_length and row.is_active})
        correct_document = (
            document.extracted_text == loaded.source_text and document.source_hash == loaded.source_hash
            and document.doc_type == loaded.doc_type and document.page_count == loaded.page_count
            and document.doc_size == loaded.source_size
        )
        whole_coverage = coverage(document.extracted_text, [(r.start_char, r.end_char) for r in rows])
        return {"passed": correct_document and bool(rows) and all(c["passed"] for c in checks)
                and whole_coverage["missing_characters"] == 0,
                "document_provenance_matches": correct_document, "chunks": checks,
                "whole_source_coverage": whole_coverage}


def evaluate_question(service, question, ingested, loaded_sources):
    started = time.perf_counter()
    source_names = {result["doc_id"]: name for name, result in ingested.items()}
    hits = [{**hit, "source_file": source_names.get(hit["doc_id"])}
            for hit in service.search(question["question"], top_k=3)]
    result = {"definition": question, "top_hits": hits,
              "latency_ms": round((time.perf_counter() - started) * 1000, 3)}
    if not question["answerable"]:
        result.update({"included_in_metrics": False,
                       "diagnostic": "Retrieval always returns similar passages; answer/refusal behavior requires an LLM evaluation."})
        return result
    expected_id = ingested.get(question["source_file"], {}).get("doc_id")
    relevant = [h for h in hits if expected_id is not None and h["doc_id"] == expected_id]
    page = question.get("expected_page")
    page_hits = [h for h in relevant if page is None or h["page_number"] == page]
    source = loaded_sources.get(question["source_file"])
    source_text = source.source_text if source is not None else ""
    normalized_source, offset_map = normalized_with_offsets(source_text)
    intervals_1 = [(h["start_char"], h["end_char"]) for h in page_hits if h["rank"] == 1]
    intervals_3 = [(h["start_char"], h["end_char"]) for h in page_hits]
    spans = []
    for evidence in question["evidence"]:
        matching = [h for h in page_hits if normalized(evidence) in normalized(h["text"])]
        needle, occurrences, cursor = normalized(evidence), [], 0
        while True:
            position = normalized_source.find(needle, cursor)
            if position < 0:
                break
            left, right = offset_map[position][0], offset_map[position + len(needle) - 1][1]
            at_1 = coverage(source_text, intervals_1, start=left, end=right)
            at_3 = coverage(source_text, intervals_3, start=left, end=right)
            occurrences.append({"start_char": left, "end_char": right,
                                "coverage_at_1": at_1, "coverage_at_3": at_3})
            cursor = position + 1
        spans.append({"text": evidence,
                      "covered_at_1": any(o["coverage_at_1"]["missing_characters"] == 0 for o in occurrences),
                      "covered_at_3": any(o["coverage_at_3"]["missing_characters"] == 0 for o in occurrences),
                      "expected_occurrences": occurrences,
                      "single_chunk_containment_at_1": any(h["rank"] == 1 for h in matching),
                      "single_chunk_containment_at_3": bool(matching),
                      "single_chunk_matching_ranks": [h["rank"] for h in matching],
                      "single_chunk_matching_ids": [h["chunk_id"] for h in matching]})
    result.update({"included_in_metrics": True, "expected_doc_id": expected_id,
                   "source_at_1": any(h["rank"] == 1 for h in relevant),
                   "source_at_3": bool(relevant), "evidence_spans": spans,
                   "single_chunk_containment_at_1": all(s["single_chunk_containment_at_1"] for s in spans),
                   "single_chunk_containment_at_3": all(s["single_chunk_containment_at_3"] for s in spans),
                   "evidence_coverage_at_1": all(s["covered_at_1"] for s in spans),
                   "evidence_coverage_at_3": all(s["covered_at_3"] for s in spans)})
    return result


def run_validation(documents_dir, manifest_path, queries_path):
    report = {"started_at_utc": datetime.now(UTC).isoformat(), "mode": "isolated_real_document_validation",
              "inputs": {"documents_dir": str(documents_dir.resolve()), "manifest": str(manifest_path.resolve()),
                         "queries": str(queries_path.resolve())},
              "sources": [], "negatives": [], "idempotency": [], "questions": [],
              "cleanup": {"schema_created": False, "schema_removed": None, "temporary_index_removed": None}}
    admin = engine = retriever = temporary = None
    schema = "codex_real_docs_" + uuid.uuid4().hex
    report["isolated_schema"] = schema
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
        queries = json.loads(queries_path.read_text(encoding="utf-8-sig"))
        validate_inputs(documents_dir, manifest, queries)
        report["generator_note"] = manifest.get("generator_note")

        from sqlalchemy import text
        from sqlalchemy.orm import sessionmaker
        from backend.database.connection import build_database_url, make_engine
        from backend.database.models import Base, DocumentCategory
        from backend.services.document_processing_service import DocumentProcessingService
        from backend.services.embedding_service import EmbeddingService
        from backend.services.retrieval_service import RetrievalService
        from backend.utils import loaders
        from scripts.verify_stores import audit_stores

        embedder = EmbeddingService(local_files_only=True)
        report["embedding_configuration"] = embedder.config
        admin = make_engine(build_database_url())
        with admin.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        report["cleanup"]["schema_created"] = True
        engine = make_engine(build_database_url(), schema=schema)
        Base.metadata.create_all(engine)
        factory = sessionmaker(bind=engine, expire_on_commit=False)
        with factory() as session:
            session.add(DocumentCategory(category_name="Validation"))
            session.commit()
        temporary = tempfile.TemporaryDirectory(prefix="codex_real_docs_")
        index_path = Path(temporary.name) / "chroma"
        report["temporary_index_path"] = str(index_path)
        retriever = RetrievalService(persist_path=index_path, collection_name="real_document_validation_v1")
        ingested, loaded_sources = {}, {}
        with DocumentProcessingService(session_factory=factory, embedder=embedder, retriever=retriever) as service:
            for source in manifest["sources"]:
                row = {"filename": source["filename"], "passed": False}
                try:
                    path = source_path(documents_dir, source["filename"])
                    loaded, extraction = inspect_source(path, source, embedder)
                    loaded_sources[source["filename"]] = loaded
                    row["extraction"] = extraction
                    result = service.process_file(path, category_name="Validation", source_key=source["filename"])
                    row["ingestion"] = result
                    ingested[source["filename"]] = result
                    row["persisted"] = inspect_persisted(factory, result["doc_id"], loaded, embedder)
                    row["passed"] = extraction["passed"] and row["persisted"]["passed"]
                except Exception as exc:
                    service.session.rollback()
                    row["error"] = safe_error(exc)
                report["sources"].append(row)

            for negative in manifest.get("negatives", []):
                row = {**negative, "passed": False}
                before = counts(factory, retriever)
                try:
                    service.process_file(source_path(documents_dir, negative["filename"]),
                                         category_name="Validation", source_key=negative["filename"])
                    row["unexpected_success"] = True
                except Exception as exc:
                    service.session.rollback()
                    expected = getattr(loaders, negative["expected_exception"], None)
                    if expected is None:
                        expected = {"FileNotFoundError": FileNotFoundError, "IsADirectoryError": IsADirectoryError}.get(negative["expected_exception"])
                    row["error"] = safe_error(exc)
                    row["exception_matches"] = expected is not None and isinstance(exc, expected)
                after = counts(factory, retriever)
                row.update({"counts_before": before, "counts_after": after,
                            "no_ingestion_writes": before == after})
                row["passed"] = row.get("exception_matches", False) and before == after
                report["negatives"].append(row)

            for filename, original in ingested.items():
                before = counts(factory, retriever)
                row = {"filename": filename, "passed": False}
                try:
                    repeated = service.process_file(source_path(documents_dir, filename), category_name="Validation", source_key=filename)
                    after = counts(factory, retriever)
                    row.update({"result": repeated, "counts_before": before, "counts_after": after})
                    row["passed"] = bool(repeated["skipped"] and repeated["doc_id"] == original["doc_id"]
                                         and repeated["version"] == original["version"] and before == after)
                except Exception as exc:
                    service.session.rollback()
                    row["error"] = safe_error(exc)
                report["idempotency"].append(row)

            for question in queries["questions"]:
                try:
                    row = evaluate_question(service, question, ingested, loaded_sources)
                except Exception as exc:
                    service.session.rollback()
                    row = {"definition": question, "included_in_metrics": question["answerable"],
                           "source_at_1": False, "source_at_3": False,
                           "evidence_coverage_at_1": False, "evidence_coverage_at_3": False,
                           "error": safe_error(exc)}
                report["questions"].append(row)
            report["store_audit"] = audit_stores(service.session, retriever.collection, embedder=embedder)
            service.session.rollback()
        answerable = [q for q in report["questions"] if q["included_in_metrics"]]
        report["metrics"] = {}
        for metric in ("source_at_1", "source_at_3", "evidence_coverage_at_1", "evidence_coverage_at_3"):
            hits = sum(bool(q.get(metric)) for q in answerable)
            report["metrics"][metric] = {"hits": hits, "total": len(answerable),
                                         "rate": hits / len(answerable) if answerable else None}
        report["answerable_questions"] = len(answerable)
        report["unanswerable_diagnostics"] = len(report["questions"]) - len(answerable)
    except Exception as exc:
        report["fatal_error"] = safe_error(exc)
    finally:
        if retriever is not None:
            try:
                retriever.client.close()
            except Exception as exc:
                report["cleanup"]["index_close_error"] = safe_error(exc)
        if temporary is not None:
            try:
                temporary.cleanup()
                report["cleanup"]["temporary_index_removed"] = not Path(temporary.name).exists()
            except Exception as exc:
                report["cleanup"]["temporary_index_removed"] = False
                report["cleanup"]["index_cleanup_error"] = safe_error(exc)
        if engine is not None:
            try:
                engine.dispose()
            except Exception as exc:
                report["cleanup"]["schema_engine_close_error"] = safe_error(exc)
        if admin is not None:
            try:
                if report["cleanup"]["schema_created"]:
                    from sqlalchemy import text
                    with admin.begin() as connection:
                        # UUID-generated identifier; never a command-line value.
                        connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
                        remains = connection.execute(text("SELECT EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = :name)"), {"name": schema}).scalar_one()
                    report["cleanup"]["schema_removed"] = not remains
            except Exception as exc:
                report["cleanup"]["schema_removed"] = False
                report["cleanup"]["schema_cleanup_error"] = safe_error(exc)
            finally:
                try:
                    admin.dispose()
                except Exception as exc:
                    report["cleanup"]["admin_engine_close_error"] = safe_error(exc)
        report["finished_at_utc"] = datetime.now(UTC).isoformat()
    audit = report.get("store_audit", {}).get("checks", [])
    report["passed"] = bool(
        not report.get("fatal_error") and report["sources"] and all(s["passed"] for s in report["sources"])
        and all(n["passed"] for n in report["negatives"])
        and len(report["idempotency"]) == len(report["sources"]) and all(r["passed"] for r in report["idempotency"])
        and audit and all(check["status"] == "PASS" for check in audit)
        and report.get("answerable_questions", 0) > 0
        and all(not q.get("error") for q in report["questions"])
        and all(q.get("evidence_coverage_at_3", False) for q in report["questions"] if q["included_in_metrics"])
        and report["cleanup"]["schema_removed"] is True and report["cleanup"]["temporary_index_removed"] is True
    )
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--documents-dir", type=Path, default=DEFAULT_ROOT / "documents")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_ROOT / "manifest.json")
    parser.add_argument("--queries", type=Path, default=DEFAULT_ROOT / "evaluation_queries.json")
    parser.add_argument("--output", type=Path, default=DEFAULT_ROOT / "results.json")
    parser.add_argument("--strict", action="store_true", help="Exit 1 for any positive/negative/idempotency/audit failure or evidence@3 miss")
    args = parser.parse_args(argv)
    if args.output.resolve() in {args.manifest.resolve(), args.queries.resolve()}:
        parser.error("Output must not overwrite a manifest or query input")
    if args.output.resolve().is_relative_to(args.documents_dir.resolve()):
        parser.error("Output must be outside the input document directory")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    report = run_validation(args.documents_dir, args.manifest, args.queries)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    for row in report["sources"]:
        print(f"[{'PASS' if row['passed'] else 'FAIL'}] source {row['filename']}")
    for name, metric in report.get("metrics", {}).items():
        print(f"{name}: {metric['hits']}/{metric['total']}")
    print(f"Validation {'PASS' if report['passed'] else 'FAIL'}; report: {args.output.resolve()}")
    cleanup_failed = report["cleanup"]["schema_removed"] is False or report["cleanup"]["temporary_index_removed"] is False
    return 1 if report.get("fatal_error") or cleanup_failed or (args.strict and not report["passed"]) else 0


if __name__ == "__main__":
    raise SystemExit(main())
