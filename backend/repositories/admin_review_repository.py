"""Bounded PostgreSQL reads for the admin review dashboard.

An assistant is linked to a question only by a valid explicit reference. Old
rows are never paired by timestamp or by their position in a conversation.
"""

from sqlalchemy import and_, case, exists, func, literal, or_, select, union_all
from sqlalchemy.orm import aliased

from backend.database.models import (
    Conversation, Document, DocumentCategory, DocumentChunk, Feedback, Message,
    RetrievedContext, User,
)


class AdminReviewRepository:
    def __init__(self, session):
        self.session = session

    def user_exists(self, user_id):
        return self.session.get(User, user_id) is not None

    def conversation_exists(self, conversation_id):
        return self.session.get(Conversation, conversation_id) is not None

    def users(self, search, limit, offset):
        counts = (select(Conversation.user_id.label("user_id"),
                         func.count(Conversation.id).label("conversation_count"),
                         func.max(Conversation.started_at).label("last_started_at"))
                  .group_by(Conversation.user_id).subquery())
        activity = (select(Conversation.user_id.label("user_id"),
                           func.max(Message.created_at).label("last_message_at"))
                    .join(Message, Message.conversation_id == Conversation.id)
                    .group_by(Conversation.user_id).subquery())
        negative = (select(Conversation.user_id.label("user_id"),
                           func.count(Feedback.message_id).label("negative_count"))
                    .join(Message, Message.conversation_id == Conversation.id)
                    .join(Feedback, and_(Feedback.message_id == Message.message_id,
                                         Feedback.user_id == Conversation.user_id))
                    .where(Message.message_provider == "assistant", Feedback.rating == "down")
                    .group_by(Conversation.user_id).subquery())
        query = (select(User, func.coalesce(counts.c.conversation_count, 0),
                        func.coalesce(negative.c.negative_count, 0),
                        func.greatest(activity.c.last_message_at, counts.c.last_started_at))
                 .outerjoin(counts, counts.c.user_id == User.user_id)
                 .outerjoin(activity, activity.c.user_id == User.user_id)
                 .outerjoin(negative, negative.c.user_id == User.user_id))
        if search:
            query = query.where(or_(User.email.icontains(search, autoescape=True),
                                    User.department.icontains(search, autoescape=True)))
        total = self.session.scalar(select(func.count()).select_from(query.subquery()))
        rows = self.session.execute(query.order_by(User.email, User.user_id).limit(limit).offset(offset)).all()
        return rows, total

    def conversations(self, user_id, limit, offset):
        activity = (select(Message.conversation_id.label("conversation_id"),
                           func.count(Message.message_id).label("message_count"),
                           func.max(Message.created_at).label("last_message_at"))
                    .group_by(Message.conversation_id).subquery())
        query = (select(Conversation, func.coalesce(activity.c.message_count, 0),
                        func.greatest(activity.c.last_message_at, Conversation.started_at).label("last_activity"))
                 .outerjoin(activity, activity.c.conversation_id == Conversation.id)
                 .where(Conversation.user_id == user_id))
        total = self.session.scalar(select(func.count()).select_from(query.subquery()))
        rows = self.session.execute(query.order_by(
            func.greatest(activity.c.last_message_at, Conversation.started_at).desc().nulls_last(),
            Conversation.id.desc()).limit(limit).offset(offset)).all()
        return rows, total

    @staticmethod
    def _review_index():
        answer = aliased(Message, name="indexed_answer")
        question = aliased(Message, name="indexed_question")
        valid_link = and_(question.message_id == answer.reply_to_message_id,
                          question.conversation_id == answer.conversation_id,
                          question.message_provider == "user")
        answers = (select(answer.message_id.label("review_id"),
                          question.message_id.label("question_id"),
                          answer.message_id.label("answer_id"),
                          answer.conversation_id.label("conversation_id"),
                          answer.created_at.label("event_at"),
                          case((question.message_id.is_not(None), "explicit"),
                               else_="legacy_unlinked").label("linkage"),
                          case((answer.insufficient_context.is_(True), "unanswered"),
                               (answer.insufficient_context.is_(False), "answered"),
                               else_="unknown").label("status"))
                   .outerjoin(question, valid_link)
                   .where(answer.message_provider == "assistant"))

        orphan = aliased(Message, name="orphan_question")
        linked_answer = aliased(Message, name="linked_answer")
        legacy_answer = aliased(Message, name="legacy_answer")
        legacy_target = aliased(Message, name="legacy_target")
        has_valid_answer = exists(select(linked_answer.message_id).where(
            linked_answer.message_provider == "assistant",
            linked_answer.conversation_id == orphan.conversation_id,
            linked_answer.reply_to_message_id == orphan.message_id))
        has_legacy_answer = exists(select(legacy_answer.message_id)
            .outerjoin(legacy_target, and_(
                legacy_target.message_id == legacy_answer.reply_to_message_id,
                legacy_target.conversation_id == legacy_answer.conversation_id,
                legacy_target.message_provider == "user"))
            .where(legacy_answer.conversation_id == orphan.conversation_id,
                   legacy_answer.message_provider == "assistant",
                   legacy_target.message_id.is_(None)))
        questions = select(orphan.message_id.label("review_id"),
                           orphan.message_id.label("question_id"),
                           literal(None, type_=Message.message_id.type).label("answer_id"),
                           orphan.conversation_id.label("conversation_id"),
                           orphan.created_at.label("event_at"),
                           literal("question_without_response").label("linkage"),
                           case((has_legacy_answer, "unknown"), else_="unanswered").label("status"))\
            .where(orphan.message_provider == "user", ~has_valid_answer)
        return union_all(answers, questions).subquery("review_index")

    def _reviews(self, *, user_id=None, conversation_id=None, feedback="all", status="all", search="",
                 question_lower=None, question_upper=None):
        index = self._review_index()
        question = aliased(Message, name="review_question")
        answer = aliased(Message, name="review_answer")
        query = (select(index.c.review_id, index.c.linkage, index.c.status,
                        Conversation, User, question, answer, Feedback)
                 .select_from(index)
                 .join(Conversation, Conversation.id == index.c.conversation_id)
                 .join(User, User.user_id == Conversation.user_id)
                 .outerjoin(question, question.message_id == index.c.question_id)
                 .outerjoin(answer, answer.message_id == index.c.answer_id)
                 .outerjoin(Feedback, and_(Feedback.message_id == index.c.answer_id,
                                          Feedback.user_id == Conversation.user_id)))
        if user_id is not None:
            query = query.where(Conversation.user_id == user_id)
        if conversation_id is not None:
            query = query.where(Conversation.id == conversation_id)
        if feedback == "unrated":
            query = query.where(Feedback.rating.is_(None))
        elif feedback != "all":
            query = query.where(Feedback.rating == feedback)
        if status != "all":
            query = query.where(index.c.status == status)
        if search:
            query = query.where(or_(question.content.icontains(search, autoescape=True),
                                    answer.content.icontains(search, autoescape=True)))
        if question_lower is not None:
            query = query.where(question.created_at >= question_lower)
        if question_upper is not None:
            query = query.where(question.created_at < question_upper)
        return query, index

    def reviews(self, *, limit, offset, **filters):
        query, index = self._reviews(**filters)
        total = self.session.scalar(select(func.count()).select_from(query.subquery()))
        rows = self.session.execute(query.order_by(index.c.event_at.desc().nulls_last(),
            index.c.review_id.desc()).limit(limit).offset(offset)).all()
        return rows, total

    def review(self, message_id):
        query, index = self._reviews()
        return self.session.execute(query.where(index.c.review_id == message_id)).one_or_none()

    def categories(self, answer_ids):
        if not answer_ids:
            return {}
        rows = self.session.execute(select(RetrievedContext.message_id, DocumentCategory.category_name)
            .join(DocumentChunk, DocumentChunk.chunk_id == RetrievedContext.chunk_id)
            .join(Document, Document.doc_id == DocumentChunk.document_id)
            .join(DocumentCategory, DocumentCategory.category_id == Document.category_id)
            .where(RetrievedContext.message_id.in_(answer_ids)).distinct()).all()
        result = {}
        for message_id, name in rows:
            result.setdefault(message_id, set()).add(name)
        return {message_id: sorted(names) for message_id, names in result.items()}

    def context(self, message_id):
        # Retained historical chunks remain visible even after a document is
        # offline/deactivated; its current availability is returned explicitly.
        return self.session.execute(select(RetrievedContext, DocumentChunk, Document, DocumentCategory)
            .join(DocumentChunk, DocumentChunk.chunk_id == RetrievedContext.chunk_id)
            .join(Document, Document.doc_id == DocumentChunk.document_id)
            .join(DocumentCategory, DocumentCategory.category_id == Document.category_id)
            .where(RetrievedContext.message_id == message_id)
            .order_by(RetrievedContext.rank.asc().nulls_last(), RetrievedContext.chunk_id)).all()
