"""Admin document operations. Routes supply inputs; repositories own data access."""

import uuid
from datetime import UTC
from pathlib import Path

from backend.database.connection import SessionLocal
from backend.repositories.category_repository import CategoryRepository
from backend.repositories.document_management_repository import DocumentManagementRepository
from backend.repositories.document_repository import DocumentRepository
from backend.services.audit_service import AuditService
from backend.services.document_processing_service import DocumentProcessingService
from backend.utils.file_storage import FileStorage


class DocumentNotFoundError(LookupError):
    pass


class DocumentConflictError(ValueError):
    pass


class DocumentService:
    def __init__(self, *, session_factory=None, embedder=None, retriever=None, storage=None,
                 embedding_provider=None):
        self.session_factory = session_factory or SessionLocal
        self.session = self.session_factory()
        self.repository = DocumentManagementRepository(self.session)
        self.storage = storage or FileStorage(Path(__file__).resolve().parents[2] / "data/uploads")
        self.embedder, self.retriever = embedder, retriever
        self.embedding_provider = embedding_provider

    def close(self):
        self.session.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def _get(self, document_id, *, lock=False):
        row = self.repository.get(uuid.UUID(str(document_id)), lock=lock)
        if row is None:
            raise DocumentNotFoundError("Document not found")
        return row

    def _dto(self, document):
        job = self.repository.latest_job(document.doc_id)
        return {
            "doc_id": str(document.doc_id), "doc_name": document.doc_name,
            "doc_type": document.doc_type, "category_id": str(document.category_id),
            "category_name": self.repository.category_name(document.category_id),
            "version": document.version, "status": document.status,
            "is_online": document.status == "active" and document.is_online,
            "chunk_count": self.repository.chunk_count(document.doc_id),
            "created_at": document.created_at.replace(tzinfo=UTC) if document.created_at else None,
            "source_key": document.source_key,
            "source_hash": document.source_hash,
            "last_job_status": job.status if job else None,
            "last_error": job.error_log if job else None,
        }

    def list_documents(self, *, uploaded_from=None, uploaded_before=None, version=None):
        documents = self.repository.list_for_admin(uploaded_from=uploaded_from,
                         uploaded_before=uploaded_before, version=version)
        rows = [self._dto(doc) for doc in documents]
        filtered = uploaded_from is not None or uploaded_before is not None or version is not None
        return {"documents": rows, "scope": "matching_versions" if filtered else "latest_sources", "summary": {
            "documents": len(rows), "online": sum(row["is_online"] for row in rows),
            "offline": sum(row["status"] == "active" and not row["is_online"] for row in rows),
            "chunks": sum(row["chunk_count"] for row in rows if filtered or row["status"] == "active"),
        }}

    def categories(self):
        return {"categories": [
            {"category_id": str(c.category_id), "category_name": c.category_name}
            for c in CategoryRepository(self.session).list_all()
        ]}

    def details(self, document_id):
        document = self._get(document_id)
        return {
            "document": self._dto(document),
            "chunks": [{"chunk_id": str(c.chunk_id), "index": c.chunk_index,
                        "text": c.chunk_text, "token_count": c.token_count,
                        "page_number": c.page_number, "section_heading": c.section_heading,
                        "start_char": c.start_char, "end_char": c.end_char}
                       for c in self.repository.chunks_for(document.doc_id)],
            "versions": [self._dto(v) for v in self.repository.versions_for(document)],
            "pipeline": document.pipeline_config or {},
            "source_characters": len(document.extracted_text or ""),
            "warnings": document.extraction_warnings or [],
        }

    @staticmethod
    def _title(title):
        title = (title or "").strip()
        if not title or len(title) > 255 or any(ord(c) < 32 for c in title):
            raise ValueError("Document name must contain 1 to 255 printable characters")
        return title

    def _processing(self):
        if self.embedder is None and self.embedding_provider is not None:
            self.embedder = self.embedding_provider()
        return DocumentProcessingService(session_factory=self.session_factory,
                                         embedder=self.embedder, retriever=self.retriever)

    def _process_saved(self, path, **kwargs):
        try:
            with self._processing() as processing:
                result = processing.process_file(path, **kwargs)
            # An unchanged replacement keeps the original immutable source.
            # Discard only this request's unused newly saved copy.
            self.session.rollback()
            if result["skipped"] and not self.repository.references_path(path):
                self.storage.remove_unreferenced_upload(path)
            return result
        except Exception:
            # Failed persisted attempts retain their source for diagnosis/retry;
            # invalid inputs that never created a document leave no orphan file.
            try:
                self.session.rollback()
                if not self.repository.references_path(path):
                    self.storage.remove_unreferenced_upload(path)
            except Exception:
                pass
            raise

    @staticmethod
    def _publication_audit(actor, action, details=None):
        def record(session, document, skipped):
            metadata = {**(details or {}), "version": document.version, "skipped": skipped}
            AuditService(session).record(actor, action, "document", document.doc_id, metadata)
        return record

    def upload(self, stream, filename, category_id, actor, *, doc_name=None, is_online=True):
        category_id = uuid.UUID(str(category_id))
        if not self.repository.category_exists(category_id):
            raise ValueError("Unknown document category")
        path, name = self.storage.save(stream, filename)
        try:
            title = self._title(doc_name or Path(name).stem)
        except Exception:
            self.storage.remove_unreferenced_upload(path)
            raise
        self.session.rollback()
        result = self._process_saved(path, category_id=category_id,
                source_key="upload/" + uuid.uuid4().hex, doc_name=title,
                uploaded_by=actor.user_id, publish_online=is_online,
                on_publish=self._publication_audit(actor, "document.upload", {"name": title, "is_online": is_online}))
        return self._operation_result(result)

    def _current_identity(self, document_id):
        document = self._get(document_id)
        source_key = document.source_key or "legacy/" + str(document.doc_id)
        repository = DocumentRepository(self.session)
        repository.lock_source(document.category_id, source_key)
        document = self._get(document_id, lock=True)
        source_key = self.repository.adopt_legacy_identity(document)
        current = repository.get_active_by_source(document.category_id, source_key)
        if current is not None and current.doc_id != document.doc_id:
            raise DocumentConflictError("This version was replaced. Select the current version.")
        if document.status not in ("active", "failed"):
            raise DocumentConflictError("This document version cannot be changed")
        data = {"category_id": document.category_id, "source_key": source_key,
                "doc_name": document.doc_name, "doc_path": document.doc_path,
                "config": document.pipeline_config or {},
                "expected_current_doc_id": current.doc_id if current is not None else None}
        self.session.commit()
        return data

    def replace(self, document_id, stream, filename, actor):
        data = self._current_identity(document_id)
        path, _ = self.storage.save(stream, filename)
        result = self._process_saved(path, category_id=data["category_id"],
                source_key=data["source_key"], doc_name=data["doc_name"], uploaded_by=actor.user_id,
                chunk_size=data["config"].get("chunk_size", 200), overlap=data["config"].get("overlap", 40),
                expected_current_doc_id=data["expected_current_doc_id"],
                on_publish=self._publication_audit(actor, "document.replace", {"previous_doc_id": str(document_id)}))
        return self._operation_result(result)

    def reindex(self, document_id, actor, *, chunk_size=200, overlap=40):
        data = self._current_identity(document_id)
        try:
            path = self.storage.resolve_source(data["doc_path"])
        except (ValueError, FileNotFoundError) as exc:
            raise DocumentConflictError(str(exc)) from exc
        with self._processing() as processing:
            result = processing.process_file(path, category_id=data["category_id"],
                source_key=data["source_key"], doc_name=data["doc_name"], uploaded_by=actor.user_id,
                chunk_size=chunk_size, overlap=overlap, force_reindex=True,
                expected_current_doc_id=data["expected_current_doc_id"],
                on_publish=self._publication_audit(actor, "document.reindex", {"previous_doc_id": str(document_id)}))
        return self._operation_result(result)

    def set_availability(self, document_id, online, actor):
        document = self._get(document_id)
        DocumentRepository(self.session).lock_source(document.category_id, document.source_key or "legacy/" + str(document.doc_id))
        document = self._get(document_id, lock=True)
        if document.status != "active":
            raise DocumentConflictError("Only a current indexed version can be taken online or offline")
        if online:
            ids = [str(row.chunk_id) for row in self.repository.chunks_for(document.doc_id)]
            if not ids:
                raise DocumentConflictError("The document has no indexed chunks; re-upload it first")
            try:
                self.retriever.verify_document(str(document.doc_id), ids)
            except Exception as exc:
                raise DocumentConflictError("The vector index is incomplete; re-index the document first") from exc
        self.repository.set_online(document, online)
        AuditService(self.session).record(actor, "document.online" if online else "document.offline",
                                         "document", document.doc_id, {"is_online": online})
        self.session.commit()
        return {"document": self._dto(document)}

    def source_file(self, document_id, actor):
        document = self._get(document_id)
        if actor.role != "admin" and (document.status != "active" or not document.is_online):
            raise PermissionError("This document is offline")
        try:
            path = self.storage.resolve_source(document.doc_path)
        except (ValueError, FileNotFoundError) as exc:
            raise DocumentNotFoundError(str(exc)) from exc
        return path, self._title(document.doc_name) + path.suffix

    def _operation_result(self, result):
        self.session.expire_all()
        return {"document": self._dto(self._get(result["doc_id"])),
                "skipped": result["skipped"], "cleanup_pending": result["cleanup_pending"]}
