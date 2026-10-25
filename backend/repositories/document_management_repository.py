"""Admin read models and document availability; no commits or vector calls."""

from sqlalchemy import func

from backend.database.models import Document, DocumentCategory, DocumentChunk, IngestionJob


class DocumentManagementRepository:
    def __init__(self, session):
        self.session = session

    def get(self, document_id, *, lock=False):
        return self.session.get(Document, document_id, with_for_update=lock, populate_existing=True)

    def all_versions(self):
        return self.session.query(Document).order_by(Document.version.desc(), Document.created_at.desc()).all()

    def list_for_admin(self, *, uploaded_from=None, uploaded_before=None, version=None):
        query = self.session.query(Document).order_by(Document.version.desc(), Document.created_at.desc())
        filtered = uploaded_from is not None or uploaded_before is not None or version is not None
        if uploaded_from is not None:
            query = query.filter(Document.created_at >= uploaded_from)
        if uploaded_before is not None:
            query = query.filter(Document.created_at < uploaded_before)
        if version is not None:
            query = query.filter(Document.version == version)
        rows = query.all()
        if filtered:
            return rows
        # Preserve the previous latest-source selection, including the active
        # version taking precedence over a more recent failed staging attempt.
        selected = {}
        for document in rows:
            key = (document.category_id, document.source_key or str(document.doc_id))
            if key not in selected or document.status == "active":
                selected[key] = document
        return list(selected.values())

    def versions_for(self, document):
        if document.source_key is None:
            return [document]
        return self.session.query(Document).filter(
            Document.category_id == document.category_id,
            Document.source_key == document.source_key,
        ).order_by(Document.version.desc()).all()

    def chunks_for(self, document_id):
        return self.session.query(DocumentChunk).filter_by(document_id=document_id).order_by(DocumentChunk.chunk_index).all()

    def chunk_count(self, document_id):
        return self.session.query(func.count(DocumentChunk.chunk_id)).filter_by(document_id=document_id).scalar()

    def latest_job(self, document_id):
        return self.session.query(IngestionJob).filter_by(document_id=document_id).order_by(IngestionJob.started_at.desc()).first()

    def category_name(self, category_id):
        category = self.session.get(DocumentCategory, category_id)
        return category.category_name if category else "Unknown"

    def category_exists(self, category_id):
        return self.session.get(DocumentCategory, category_id) is not None

    def references_path(self, path):
        return self.session.query(Document.doc_id).filter(Document.doc_path == str(path)).first() is not None

    def set_online(self, document, online):
        document.is_online = online
        document.updated_at = func.now()
        self.session.flush()

    def adopt_legacy_identity(self, document):
        if document.source_key is None:
            document.source_key = "legacy/" + str(document.doc_id)
            self.session.flush()
        return document.source_key
