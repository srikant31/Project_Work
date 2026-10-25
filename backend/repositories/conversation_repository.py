"""Conversation/message persistence and evidence checks; callers own commits."""

from sqlalchemy import select

from backend.database.models import Conversation, Document, DocumentChunk, Message, RetrievedContext


class ConversationRepository:
    def __init__(self, session):
        self.session = session

    def get(self, conversation_id):
        return self.session.get(Conversation, conversation_id)

    def create(self, user_id, title):
        row = Conversation(user_id=user_id, title=title[:255])
        self.session.add(row)
        self.session.flush()
        return row

    def add_message(self, conversation_id, role, content, **fields):
        question_id = fields.get("reply_to_message_id")
        if question_id is not None:
            question = self.session.get(Message, question_id)
            if (role != "assistant" or question is None
                or question.message_provider != "user"
                or str(question.conversation_id) != str(conversation_id)):
                raise ValueError("An assistant reply must link to a user question in the same conversation")
        row = Message(conversation_id=conversation_id, message_provider=role, content=content, **fields)
        self.session.add(row)
        self.session.flush()
        return row

    def record_context(self, message_id, sources):
        seen = set()
        for rank, source in enumerate(sources, 1):
            if source["chunk_id"] in seen:
                continue
            seen.add(source["chunk_id"])
            self.session.add(RetrievedContext(message_id=message_id, chunk_id=source["chunk_id"],
                rank=rank, similarity_score=source.get("similarity_score")))
        self.session.flush()

    def sources_still_eligible(self, sources):
        """Recheck after model latency, holding read locks through persistence."""
        for source in sorted(sources, key=lambda item: str(item["doc_id"])):
            document = self.session.execute(select(Document).where(Document.doc_id == source["doc_id"])
                .with_for_update(read=True)).scalar_one_or_none()
            chunk = self.session.get(DocumentChunk, source["chunk_id"], populate_existing=True)
            if (document is None or chunk is None or document.status != "active" or not document.is_online
                or not chunk.is_active or str(chunk.document_id) != str(document.doc_id)
                or document.version != source.get("version") or document.source_hash != source.get("source_hash")
                or chunk.chunk_text != source["text"]):
                return False
        return True

    def source_snapshots(self, document_ids):
        rows = self.session.query(Document).filter(Document.doc_id.in_(document_ids),
            Document.status == "active", Document.is_online.is_(True)).all()
        return {str(row.doc_id): {"text": row.extracted_text, "source_hash": row.source_hash, "version": row.version}
                for row in rows if row.extracted_text is not None and row.source_hash is not None}

    def list_for_user(self, user_id, limit=50):
        return self.session.query(Conversation).filter_by(user_id=user_id).order_by(Conversation.started_at.desc()).limit(limit).all()

    def messages(self, conversation_id):
        return self.session.query(Message).filter_by(conversation_id=conversation_id).order_by(Message.created_at, Message.message_id).all()
