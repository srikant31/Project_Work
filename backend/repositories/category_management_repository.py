"""Category maintenance; writes serialize and commits belong to the service."""

from sqlalchemy import func, text

from backend.database.models import Document, DocumentCategory


class CategoryManagementRepository:
    def __init__(self, session):
        self.session = session

    def lock_writes(self):
        # One stable transaction lock covers duplicate checks and mutations.
        # PostgreSQL FK locking independently protects concurrent ingestion.
        self.session.execute(text("SELECT pg_advisory_xact_lock(73571048219017)"))

    def get(self, category_id, *, lock=False):
        return self.session.get(DocumentCategory, category_id, with_for_update=lock)

    def list_counts(self):
        return self.session.query(DocumentCategory, func.count(Document.doc_id)).outerjoin(
            Document, Document.category_id == DocumentCategory.category_id
        ).group_by(DocumentCategory.category_id).order_by(
            func.lower(DocumentCategory.category_name), DocumentCategory.category_id
        ).all()

    def count_documents(self, category_id):
        return self.session.query(func.count(Document.doc_id)).filter(
            Document.category_id == category_id
        ).scalar()

    def duplicate(self, name, excluding=None):
        query = self.session.query(DocumentCategory.category_id).filter(
            func.lower(DocumentCategory.category_name) == func.lower(name)
        )
        if excluding is not None:
            query = query.filter(DocumentCategory.category_id != excluding)
        return query.first() is not None

    def create(self, name, description):
        row = DocumentCategory(category_name=name, description=description)
        self.session.add(row)
        self.session.flush()
        return row

    def delete(self, row):
        self.session.delete(row)
        self.session.flush()
