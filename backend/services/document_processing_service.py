"""Safe, recoverable publication of immutable document versions.

PostgreSQL is the visibility authority; Chroma is an independently recoverable
index. Cross-store crash atomicity is deliberately not claimed.
"""

import hashlib
import json
import math
import os
import uuid
from datetime import UTC, datetime, timedelta
from importlib.metadata import version as package_version
from pathlib import Path

from sqlalchemy import or_
from sqlalchemy.orm import sessionmaker

from backend.database.connection import SessionLocal
from backend.database.models import Document, DocumentCategory, DocumentChunk, IngestionJob
from backend.repositories.category_repository import CategoryRepository
from backend.repositories.chunk_repository import ChunkRepository
from backend.repositories.document_repository import DocumentRepository
from backend.repositories.ingestion_job_repository import IngestionJobRepository
from backend.utils.loaders import LoadedDocument, load_document
from backend.utils.chunking import CHUNKER_VERSION, chunk_document


class ConcurrentIngestionError(RuntimeError):
    pass


class RecoveryPendingError(RuntimeError):
    pass


# Omitted means unrestricted service/CLI ingestion. Explicit None means that an
# administrative retry expects no active predecessor; it is a real CAS guard.
_EXPECTED_CURRENT_UNSET = object()


class DocumentProcessingService:
    def __init__(self, db_session=None, embedder=None, retriever=None, session_factory=None):
        self.session_factory = session_factory or (
            sessionmaker(bind=db_session.get_bind(), expire_on_commit=False)
            if db_session is not None else SessionLocal
        )
        self.session = db_session if db_session is not None else self.session_factory()
        self._owns_session = db_session is None
        self._embedder, self._retriever = embedder, retriever
        self.doc_repo = DocumentRepository(self.session)
        self.chunk_repo = ChunkRepository(self.session)
        self.job_repo = IngestionJobRepository(self.session)
        self.category_repo = CategoryRepository(self.session)

    @property
    def embedder(self):
        if self._embedder is None:
            from backend.services.embedding_service import EmbeddingService
            self._embedder = EmbeddingService()
        return self._embedder

    @property
    def retriever(self):
        if self._retriever is None:
            from backend.services.retrieval_service import RetrievalService
            self._retriever = RetrievalService()
        return self._retriever

    def close(self):
        if self._owns_session:
            self.session.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False

    def process_file(self, path, *, category_name=None, category_id=None, source_key=None,
                     doc_name=None, uploaded_by=None, chunk_size=200, overlap=40,
                     publish_online=None, force_reindex=False,
                     expected_current_doc_id=_EXPECTED_CURRENT_UNSET, on_publish=None):
        loaded = load_document(path)
        return self._ingest(
            loaded, doc_name=doc_name or loaded.title, doc_path=str(Path(path)),
            uploaded_by=uploaded_by, category_id=category_id, category_name=category_name,
            source_key=source_key if source_key is not None else os.path.normcase(str(Path(path).resolve())).replace(chr(92), "/"),
            chunk_size=chunk_size, overlap=overlap,
            publish_online=publish_online, force_reindex=force_reindex,
            expected_current_doc_id=expected_current_doc_id, on_publish=on_publish,
        )

    def process_document(self, doc_name, doc_type, uploaded_by=None, doc_path="", raw_text=None,
                         pages=None, category_id=None, category_name=None,
                         chunk_size=200, overlap=40, source_key=None,
                         publish_online=None, force_reindex=False,
                         expected_current_doc_id=_EXPECTED_CURRENT_UNSET, on_publish=None):
        if (raw_text is None) == (pages is None):
            raise ValueError("Provide exactly one of raw_text or pages")
        loaded = (
            LoadedDocument.from_text(raw_text, title=doc_name, doc_type=doc_type)
            if raw_text is not None else
            LoadedDocument.from_pages(pages, title=doc_name, doc_type=doc_type)
        )
        return self._ingest(
            loaded, doc_name=doc_name, doc_path=doc_path, uploaded_by=uploaded_by,
            category_id=category_id, category_name=category_name,
            source_key=source_key if source_key is not None else doc_name, chunk_size=chunk_size, overlap=overlap,
            publish_online=publish_online, force_reindex=force_reindex,
            expected_current_doc_id=expected_current_doc_id, on_publish=on_publish,
        )

    def reingest_document(self, doc_name, **kwargs):
        # Compatibility entry point. All callers now use the same safe lifecycle.
        return self.process_document(doc_name=doc_name, **kwargs)

    def _resolve_category(self, category_id, category_name):
        if (category_id is None) == (category_name is None):
            raise ValueError("Provide exactly one of category_id or category_name")
        if self.session.new or self.session.dirty or self.session.deleted:
            raise ValueError("The ingestion service requires a session without pending caller changes")
        if category_name is not None:
            found = self.category_repo.require_by_name(category_name)
        else:
            found = self.session.get(DocumentCategory, uuid.UUID(str(category_id)))
            if found is None:
                raise ValueError("Unknown category_id")
        result = found.category_id
        self.session.rollback()  # release the read transaction before model work
        return result

    def _ingest(self, loaded, *, doc_name, doc_path, uploaded_by, category_id,
                category_name, source_key, chunk_size, overlap, publish_online=None,
                force_reindex=False, expected_current_doc_id=_EXPECTED_CURRENT_UNSET,
                on_publish=None):
        if publish_online is not None and not isinstance(publish_online, bool):
            raise ValueError("publish_online must be a boolean")
        if on_publish is not None and not callable(on_publish):
            raise ValueError("on_publish must be callable")
        if expected_current_doc_id is not _EXPECTED_CURRENT_UNSET and expected_current_doc_id is not None:
            expected_current_doc_id = uuid.UUID(str(expected_current_doc_id))
        if not isinstance(source_key, str) or not source_key.strip() or chr(0) in source_key:
            raise ValueError("source_key must be a nonempty logical source identifier")
        if not doc_name or len(doc_name) > 255:
            raise ValueError("doc_name must contain 1 to 255 characters")
        if loaded.doc_type not in {"txt", "md", "pdf", "docx"}:
            raise ValueError("Unsupported document type")
        category_id = self._resolve_category(category_id, category_name)
        chunks = chunk_document(loaded, self.embedder.tokenizer, self.embedder.max_sequence_length,
                                chunk_size=chunk_size, overlap=overlap)
        if not chunks:
            raise ValueError("Document produced no meaningful chunks")
        text_hash = hashlib.sha256(loaded.source_text.encode("utf-8")).hexdigest()
        parser_package = {"pdf": "pypdf", "docx": "python-docx", "md": "markdown-it-py"}.get(loaded.doc_type)
        config = {
            "doc_type": loaded.doc_type,
            "extractor": loaded.extractor_version, "chunker": CHUNKER_VERSION,
            "parser_dependency": {parser_package: package_version(parser_package)} if parser_package else {},
            "chunk_size": chunk_size, "overlap": overlap,
            "embedding": self.embedder.config, "collection": self.retriever.collection_name,
        }
        fingerprint = hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()
        # Fast repeat-import path avoids an unnecessary embedding forward pass.
        self.doc_repo.lock_source(category_id, source_key)
        active = self.doc_repo.get_active_by_source(category_id, source_key)
        try:
            self._require_expected_current(active, expected_current_doc_id)
        except Exception:
            self.session.rollback()
            raise
        if not force_reindex and active and active.source_hash == loaded.source_hash and active.text_hash == text_hash and active.pipeline_fingerprint == fingerprint:
            return self._existing_result(active, on_publish=on_publish)
        self.session.rollback()
        embeddings = self.embedder.embed_batch([c.text for c in chunks])
        if len(embeddings) != len(chunks):
            raise ValueError("Embedding count differs from chunk count")
        for vector in embeddings:
            if len(vector) != self.embedder.dimension or not all(math.isfinite(float(v)) for v in vector):
                raise ValueError("Embedding has invalid dimensions or nonfinite values")

        # Reserve immutable source/version and expected chunks in a durable stage.
        try:
            self.doc_repo.lock_source(category_id, source_key)
            active = self.doc_repo.get_active_by_source(category_id, source_key)
            self._require_expected_current(active, expected_current_doc_id)
            if not force_reindex and active and active.source_hash == loaded.source_hash and active.text_hash == text_hash and active.pipeline_fingerprint == fingerprint:
                return self._existing_result(active, on_publish=on_publish)
            latest = self.doc_repo.get_latest_by_source(category_id, source_key)
            if latest and latest.status == "staging":
                raise ConcurrentIngestionError("A staged attempt exists; inspect scoped reconciliation before retrying")
            predecessor_id = active.doc_id if active else None
            document = self.doc_repo.create_document(
                doc_name=doc_name, doc_type=loaded.doc_type, category_id=category_id,
                uploaded_by=uploaded_by, doc_path=doc_path, page_count=loaded.page_count,
                doc_size=loaded.source_size, version=latest.version + 1 if latest else 1,
                status="staging", source_key=source_key, source_hash=loaded.source_hash,
                text_hash=text_hash,
                pipeline_fingerprint=fingerprint, pipeline_config=config,
                extracted_text=loaded.source_text, extraction_warnings=loaded.warnings,
                is_online=publish_online if publish_online is not None else (active.is_online if active else True),
            )
            doc_id = document.doc_id
            job = self.job_repo.start_job(doc_id)
            job_id = job.id
            records = []
            for chunk, vector in zip(chunks, embeddings, strict=True):
                row = self.chunk_repo.create_chunk(
                    document_id=doc_id, chunk_text=chunk.text, chunk_index=chunk.index,
                    token_count=chunk.token_count, embedding_model=self.embedder.model_name,
                    page_number=chunk.page_number, section_heading=chunk.section_heading,
                    start_char=chunk.start_char, end_char=chunk.end_char, is_active=False,
                )
                cid = str(row.chunk_id)
                row.embedding_vector_id = self.retriever.vector_id(cid)
                records.append((cid, vector, chunk.text))
            self.session.commit()
        except Exception:
            self.session.rollback()
            raise

        publication_attempted = False
        published = False
        try:
            # Hold the source lock across vector I/O. Reconciliation cannot retire
            # a long-running worker even when it exceeds the stale-job threshold.
            self.doc_repo.lock_source(category_id, source_key)
            self.session.refresh(document)
            self.session.refresh(job)
            if document.status != "staging" or job.status != "processing":
                raise ConcurrentIngestionError("The staging attempt was retired before vector publication")
            self.retriever.add_chunks(records, str(doc_id))
            self.retriever.verify_document(str(doc_id), [r[0] for r in records])
            current = self.doc_repo.get_active_by_source(category_id, source_key)
            if current is not None:
                # expire_on_commit=False may retain the predecessor loaded at
                # reservation. Refresh after the publication lock so a toggle
                # committed during the stage gap is actually inherited.
                self.session.refresh(current)
            if (current.doc_id if current else None) != predecessor_id:
                raise ConcurrentIngestionError("The active predecessor changed during ingestion; retry")
            if current:
                if publish_online is None:
                    # Honor an admin toggle made after staging but before the
                    # worker acquired its publication lock.
                    document.is_online = current.is_online
                self.chunk_repo.deactivate_chunks_for_document(current.doc_id)
                current.cleanup_pending = True
                self.doc_repo.deactivate(current)
            self.doc_repo.activate(document)
            self.chunk_repo.activate_chunks_for_document(doc_id)
            self.job_repo.mark_completed(job)
            if on_publish is not None:
                # The caller adds audit records using this exact transaction.
                # A callback must flush only; publication owns the commit.
                on_publish(self.session, document, False)
            publication_attempted = True
            self.session.commit()
            published = True
        except Exception as exc:
            self.session.rollback()
            if publication_attempted:
                state = self._publication_state(doc_id, job_id)
                if state is None:
                    raise RecoveryPendingError(
                        f"Publication outcome unknown for {doc_id}; run scoped reconciliation when PostgreSQL is available"
                    ) from exc
                published = state
            if not published:
                recovered = self._fail_attempt(doc_id, job_id, type(exc).__name__ + ": " + str(exc))
                if not recovered:
                    exc.add_note(f"Recovery/cleanup pending for {doc_id}; inspect scoped reconciliation")
                raise

        cleanup_pending = False
        if predecessor_id is not None:
            cleanup_pending = not self._cleanup_document(predecessor_id)
        # Refresh after a lost acknowledgement was confirmed in a fresh session.
        self.session.expire_all()
        document = self.session.get(Document, doc_id)
        result = self._result(document, [r[0] for r in records])
        result["cleanup_pending"] = cleanup_pending
        self.session.rollback()
        return result

    @staticmethod
    def _require_expected_current(active, expected):
        if expected is _EXPECTED_CURRENT_UNSET:
            return
        actual = active.doc_id if active is not None else None
        if actual != expected:
            raise ConcurrentIngestionError(
                "The selected document was replaced while this operation was preparing. Select the current version and retry."
            )

    def _existing_result(self, document, *, on_publish=None):
        try:
            ids = self.chunk_repo.get_all_chunk_ids(document.doc_id)
            try:
                if not ids:
                    raise RuntimeError("Published version has no chunks")
                self.retriever.verify_document(str(document.doc_id), ids)
            except Exception as exc:
                raise RecoveryPendingError(
                    f"Existing index is incomplete for {document.doc_id}; run scoped reconciliation"
                ) from exc
            result = self._result(document, ids, skipped=True)
            result["cleanup_pending"] = self.session.query(Document).filter(
                Document.category_id == document.category_id,
                Document.source_key == document.source_key,
                Document.cleanup_pending.is_(True),
            ).count() > 0
            if on_publish is not None:
                on_publish(self.session, document, True)
                self.session.commit()
            return result
        finally:
            self.session.rollback()

    def _result(self, document, chunk_ids, *, skipped=False):
        return {
            "doc_id": str(document.doc_id), "chunk_ids": chunk_ids,
            "chunk_count": len(chunk_ids), "version": document.version,
            "skipped": skipped, "cleanup_pending": bool(document.cleanup_pending),
            "source_hash": document.source_hash, "warnings": document.extraction_warnings or [],
        }

    def _publication_state(self, doc_id, job_id):
        try:
            with self.session_factory() as fresh:
                document = fresh.get(Document, doc_id)
                job = fresh.get(IngestionJob, job_id)
                if document is None or job is None:
                    return None
                return job.status == "completed" and document.status in ("active", "deactivated")
        except Exception:
            return None

    def _fail_attempt(self, doc_id, job_id, error):
        try:
            with self.session_factory() as fresh:
                document = fresh.get(Document, doc_id)
                repo = DocumentRepository(fresh)
                repo.lock_source(document.category_id, document.source_key)
                fresh.refresh(document)
                job = fresh.get(IngestionJob, job_id)
                if job.status == "completed" or document.status == "active":
                    return False
                repo.mark_failed(document)
                document.cleanup_pending = True
                IngestionJobRepository(fresh).mark_failed(job, error)
                fresh.commit()
            return self._cleanup_document(doc_id)
        except Exception:
            # Durable staging remains invisible and reportable. Never guess that
            # a failed connection means publication did not commit.
            return False

    def _cleanup_document(self, doc_id):
        try:
            with self.session_factory() as fresh:
                document = fresh.get(Document, doc_id)
                if document is None:
                    return False
                DocumentRepository(fresh).lock_source(document.category_id, document.source_key)
                fresh.refresh(document)
                if document.status not in ("failed", "deactivated"):
                    return False
                if (document.pipeline_config or {}).get("collection") != self.retriever.collection_name:
                    return False  # preserve pending state until original collection is selected
                self.retriever.delete_by_doc(str(doc_id))
                if self.retriever.get_by_doc(str(doc_id)).get("ids"):
                    raise RuntimeError("Obsolete vectors remain after deletion")
                document.cleanup_pending = False
                fresh.commit()
                return True
        except Exception:
            return False

    def _repair_active_document(self, doc_id):
        with self.session_factory() as fresh:
            document = fresh.get(Document, doc_id)
            DocumentRepository(fresh).lock_source(document.category_id, document.source_key)
            fresh.refresh(document)
            if document.status != "active":
                return "Version no longer active; repair skipped"
            if document.pipeline_config.get("embedding") != self.embedder.config:
                raise ValueError("Repair requires the original embedding configuration; reindex with a new version instead")
            if document.pipeline_config.get("collection") != self.retriever.collection_name:
                raise ValueError("Repair must target the original vector collection")
            rows = ChunkRepository(fresh).get_chunks_by_document(doc_id, active_only=False)
            if not rows:
                raise RecoveryPendingError("No stored active chunks; reindex the source document")
            vectors = self.embedder.embed_batch([row.chunk_text for row in rows])
            if len(vectors) != len(rows) or any(
                len(v) != self.embedder.dimension or not all(math.isfinite(float(x)) for x in v)
                for v in vectors
            ):
                raise ValueError("Invalid repair embeddings")
            records = [(str(row.chunk_id), vector, row.chunk_text) for row, vector in zip(rows, vectors, strict=True)]
            self.retriever.add_chunks(records, str(doc_id))
            self.retriever.verify_document(str(doc_id), [r[0] for r in records])
            fresh.commit()
            return "Active vectors restored from immutable stored chunks"

    def search(self, question, top_k=None, *, _retry_on_version_change=True):
        k = self.retriever.default_top_k if top_k is None else top_k
        if not isinstance(k, int) or isinstance(k, bool) or k <= 0:
            raise ValueError("top_k must be a positive integer")
        self.session.expire_all()
        allowed = self.doc_repo.get_active_document_ids()
        if not allowed:
            return []
        vector = self.embedder.embed_text(question)
        limit, maximum = k, max(k, self.retriever.count())
        while True:
            res = self.retriever.query(vector, n_results=limit, doc_ids=allowed)
            ids, distances = (res.get("ids") or [[]])[0], (res.get("distances") or [[]])[0]
            valid_ids = []
            for cid in ids:
                try:
                    valid_ids.append(str(uuid.UUID(cid)))
                except ValueError:
                    continue
            rows = {str(r.chunk_id): r for r in self.chunk_repo.get_chunks_by_ids(valid_ids)}
            out = []
            for cid, distance in zip(ids, distances):
                row = rows.get(cid)
                if row is None:
                    continue
                doc = row.document
                out.append({
                    "rank": len(out) + 1, "chunk_id": cid,
                    "similarity_score": 1.0 - distance, "text": row.chunk_text,
                    "page_number": row.page_number, "section_heading": row.section_heading,
                    "start_char": row.start_char, "end_char": row.end_char,
                    "doc_id": str(doc.doc_id), "doc_name": doc.doc_name,
                    "version": doc.version, "source_hash": doc.source_hash,
                })
                if len(out) == k:
                    break
            if len(out) >= k or len(ids) < limit or limit >= maximum:
                if _retry_on_version_change and len(out) < k and set(self.doc_repo.get_active_document_ids()) != set(allowed):
                    return self.search(question, k, _retry_on_version_change=False)
                return out
            limit = min(maximum, limit * 2)

    def reconcile(self, doc_id=None, *, apply=False, stale_minutes=60):
        if stale_minutes < 0:
            raise ValueError("stale_minutes must be nonnegative")
        if apply and doc_id is None:
            raise ValueError("Applying reconciliation requires an explicit doc_id")
        query = self.session.query(Document).filter(Document.source_key.is_not(None))
        if doc_id is not None:
            query = query.filter(Document.doc_id == uuid.UUID(str(doc_id)))
        else:
            query = query.filter(or_(Document.status == "staging", Document.cleanup_pending.is_(True)))
        documents = query.all()
        candidates = [(d.doc_id, d.status, d.cleanup_pending, d.category_id, d.source_key) for d in documents]
        self.session.rollback()
        results = []
        for candidate_id, status, pending, category_id, source_key in candidates:
            detail, action = "No recovery action required", "none"
            if status == "staging":
                with self.session_factory() as fresh:
                    job = fresh.query(IngestionJob).filter_by(document_id=candidate_id).first()
                    stale = job is not None and job.started_at < datetime.now(UTC).replace(tzinfo=None) - timedelta(minutes=stale_minutes)
                action = "retire_stale_stage" if stale else "wait"
                detail = "Staged version is not searchable"
                if apply and stale:
                    # Serialize against the worker, then refresh its state. A worker
                    # that published while we waited must never be rolled back.
                    with self.session_factory() as fresh:
                        repo = DocumentRepository(fresh)
                        repo.lock_source(category_id, source_key)
                        document = fresh.get(Document, candidate_id)
                        job = fresh.query(IngestionJob).filter_by(document_id=candidate_id).first()
                        if document.status == "staging" and job.status != "completed":
                            repo.mark_failed(document)
                            document.cleanup_pending = True
                            IngestionJobRepository(fresh).mark_failed(job, "Retired stale staging attempt by scoped reconciliation")
                            fresh.commit()
                            pending, status = True, "failed"
                        else:
                            action, detail = "none", "Worker already published; preserved"
            elif pending:
                action, detail = "cleanup_vectors", "Obsolete/failed version vectors can be removed"
            elif status == "active":
                with self.session_factory() as fresh:
                    expected = ChunkRepository(fresh).get_all_chunk_ids(candidate_id)
                try:
                    if not expected:
                        raise RuntimeError("No expected chunks")
                    self.retriever.verify_document(str(candidate_id), expected)
                except Exception:
                    action, detail = "repair_active_vectors", "Active vector index is incomplete or unavailable"
                    if apply:
                        detail = self._repair_active_document(candidate_id)
            if apply and pending and status in ("failed", "deactivated"):
                success = self._cleanup_document(candidate_id)
                detail = "Cleanup complete" if success else "Cleanup pending; retry when stores are available"
            results.append({"doc_id": str(candidate_id), "status": status, "action": action, "detail": detail, "applied": apply})
        return results
