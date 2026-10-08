from sqlalchemy import func

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
        )
        self.session.add(document)
        self.session.flush()
        return document

    def get_document_by_id(self, doc_id) -> Document | None:
        return self.session.get(Document, doc_id)

    def get_latest_version_by_name(self, doc_name: str) -> Document | None:
        return (
            self.session.query(Document)
            .filter(Document.doc_name == doc_name)
            .order_by(Document.version.desc())
            .first()
        )

    def deactivate(self, document: Document) -> None:
        document.status = "deactivated"
        document.updated_at = func.now()
        self.session.flush()
