import hashlib

from sqlalchemy import func, text

from backend.database.models import Document


class DocumentRepository:
    """Data access only. NOTHING here commits — the service owns the transaction."""

    def __init__(self, session):
        self.session = session

    def create_document(
        self,
        doc_name: str,
        doc_type: str,
        category_id,
        uploaded_by=None,
        doc_path: str = "",
        page_count: int | None = None,
        doc_size: int | None = None,
        version: int = 1,
        status: str = "staging",
        source_key: str | None = None,
        source_hash: str | None = None,
        text_hash: str | None = None,
        pipeline_fingerprint: str | None = None,
        pipeline_config: dict | None = None,
        extracted_text: str | None = None,
        extraction_warnings: list[str] | None = None,
        cleanup_pending: bool = False,
        is_online: bool = True,
    ) -> Document:
        # category_id has no default — Postgres would reject a NULL here
        # anyway (see migration 002), but failing in Python first gives a
        # clearer error than a raw psycopg2 IntegrityError.
        if category_id is None:
            raise ValueError(
                "category_id is required. Look one up via "
                "CategoryRepository.require_by_name(...) first."
            )
        document = Document(
            doc_name=doc_name,
            doc_type=doc_type,
            uploaded_by=uploaded_by,
            doc_path=doc_path,
            category_id=category_id,
            page_count=page_count,
            doc_size=doc_size,
            version=version,
            status=status,
            source_key=source_key,
            source_hash=source_hash,
            text_hash=text_hash,
            pipeline_fingerprint=pipeline_fingerprint,
            pipeline_config=pipeline_config,
            extracted_text=extracted_text,
            extraction_warnings=extraction_warnings,
            cleanup_pending=cleanup_pending,
            is_online=is_online,
        )
        self.session.add(document)
        self.session.flush()
        return document

    def get_document_by_id(self, doc_id) -> Document | None:
        return self.session.get(Document, doc_id)

    def get_latest_version_by_name(self, doc_name: str) -> Document | None:
        """Legacy display-name lookup. New ingestion must use source identity."""
        return (
            self.session.query(Document)
            .filter(Document.doc_name == doc_name)
            .order_by(Document.version.desc())
            .first()
        )

    def lock_source(self, category_id, source_key: str) -> None:
        """Serialize source reservation/publication until this transaction ends.

        Hash collisions can only cause extra serialization, never mix sources.
        Never use Python's randomized hash() for a cross-process lock key.
        """
        identity = f"{category_id}\0{source_key}".encode("utf-8")
        lock_key = int.from_bytes(hashlib.sha256(identity).digest()[:8], "big", signed=True)
        self.session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock_key})

    def get_active_by_source(self, category_id, source_key: str) -> Document | None:
        return (
            self.session.query(Document)
            .filter(
                Document.category_id == category_id,
                Document.source_key == source_key,
                Document.status == "active",
            )
            .one_or_none()
        )

    def get_latest_by_source(self, category_id, source_key: str) -> Document | None:
        """Include failed/staging attempts so reserved version numbers stay unique."""
        return (
            self.session.query(Document)
            .filter(Document.category_id == category_id, Document.source_key == source_key)
            .order_by(Document.version.desc())
            .first()
        )

    def get_active_document_ids(self) -> list[str]:
        return [
            str(row[0])
            for row in self.session.query(Document.doc_id).filter(Document.status == "active", Document.is_online.is_(True)).all()
        ]

    def list_cleanup_pending(self) -> list[Document]:
        return (
            self.session.query(Document)
            .filter(Document.cleanup_pending.is_(True))
            .order_by(Document.created_at)
            .all()
        )

    def activate(self, document: Document) -> None:
        document.status = "active"
        document.updated_at = func.now()
        self.session.flush()

    def mark_failed(self, document: Document) -> None:
        document.status = "failed"
        document.updated_at = func.now()
        self.session.flush()

    def deactivate(self, document: Document) -> None:
        document.status = "deactivated"
        document.updated_at = func.now()
        self.session.flush()
