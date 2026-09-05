from backend.database.models import Document


class DocumentRepository:
    def __init__(self, session):
        self.session = session

    def create_document(self, doc_name: str, doc_type: str, uploaded_by: str = None, doc_path: str = "", category_id: str = None) -> Document:
        document = Document(
            doc_name=doc_name,
            doc_type=doc_type,
            uploaded_by=uploaded_by,
            doc_path=doc_path,
            category_id=category_id,
        )
        self.session.add(document)
        self.session.commit()
        self.session.refresh(document)
        return document

    def get_document_by_id(self, doc_id: str) -> Document | None:
        return self.session.get(Document, doc_id)