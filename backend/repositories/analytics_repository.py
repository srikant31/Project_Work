"""Bounded aggregate queries over existing chat records, not inference results."""

from sqlalchemy import and_, case, cast, func, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import aliased

from backend.database.models import (
    Conversation, Document, DocumentCategory, DocumentChunk, Feedback, Message,
    RetrievedContext, User,
)


class AnalyticsRepository:
    def __init__(self, session):
        self.session = session

    @staticmethod
    def _period(statement, column, lower, upper):
        if lower is not None:
            statement = statement.where(column >= lower)
        if upper is not None:
            statement = statement.where(column < upper)
        return statement

    @staticmethod
    def _reason():
        value = cast(Message.generation_metadata, JSONB)["grounding"]["reason"]
        # Malformed legacy nested objects must not be serialized as diagnostics.
        return case((func.jsonb_typeof(value) == "string", value.as_string()), else_=None)

    @staticmethod
    def _responses():
        # A legacy third-party vote must not become the conversation owner's vote.
        return (select(Message.message_id.label("message_id"), Message.created_at.label("created_at"),
                       Message.response_time_ms.label("latency"),
                       Message.insufficient_context.label("insufficient_context"),
                       Feedback.rating.label("rating"),
                       AnalyticsRepository._reason().label("reason"))
                .select_from(Message)
                .join(Conversation, Conversation.id == Message.conversation_id)
                .outerjoin(Feedback, and_(Feedback.message_id == Message.message_id,
                                          Feedback.user_id == Conversation.user_id))
                .where(Message.message_provider == "assistant"))

    def question_count(self, lower=None, upper=None):
        query = select(func.count()).select_from(Message).where(Message.message_provider == "user")
        return self.session.scalar(self._period(query, Message.created_at, lower, upper))

    def response_summary(self, lower, upper):
        cohort = self._period(self._responses(), Message.created_at, lower, upper).subquery()
        valid_time = cohort.c.latency >= 0
        count_if = lambda condition: func.count().filter(condition)
        query = select(func.count().label("total_responses"),
            count_if(cohort.c.rating == "up").label("positive_feedback"),
            count_if(cohort.c.rating == "down").label("negative_feedback"),
            count_if(cohort.c.rating.is_(None)).label("unrated_responses"),
            func.avg(cohort.c.latency).filter(valid_time).label("average_response_time_ms"),
            count_if(valid_time).label("timed_response_count"),
            count_if(cohort.c.insufficient_context.is_(True)).label("unsupported_responses"),
            count_if(cohort.c.reason == "no_eligible_sources").label("no_useful_retrieval_responses"),
            count_if(cohort.c.insufficient_context.is_(None)).label("unknown_outcome_responses"))
        return dict(self.session.execute(query).one()._mapping)

    def daily(self, lower, upper):
        questions = self.session.execute(self._period(
            select(func.date(Message.created_at).label("day"), func.count().label("questions"))
                .where(Message.message_provider == "user"), Message.created_at, lower, upper)
            .group_by(func.date(Message.created_at))).all()
        cohort = self._period(self._responses(), Message.created_at, lower, upper).subquery()
        responses = self.session.execute(select(func.date(cohort.c.created_at).label("day"),
            func.count().label("responses"),
            func.count().filter(cohort.c.rating == "up").label("positive_feedback"),
            func.count().filter(cohort.c.rating == "down").label("negative_feedback"))
            .group_by(func.date(cohort.c.created_at))).all()
        return questions, responses

    def frequent_questions(self, lower, upper):
        normalized = func.lower(func.trim(func.regexp_replace(Message.content, r"\s+", " ", "g")))
        count = func.count()
        query = select(normalized.label("question"), count.label("count"))\
            .where(Message.message_provider == "user", normalized != "")
        query = self._period(query, Message.created_at, lower, upper)
        return self.session.execute(query.group_by(normalized)
            .order_by(count.desc(), normalized).limit(10)).all()

    def document_usage(self, lower, upper):
        # Multiple retained chunks from one document count only once per response.
        pairs = self._period(select(RetrievedContext.message_id, Document.doc_id)
            .select_from(RetrievedContext)
            .join(Message, Message.message_id == RetrievedContext.message_id)
            .join(DocumentChunk, DocumentChunk.chunk_id == RetrievedContext.chunk_id)
            .join(Document, Document.doc_id == DocumentChunk.document_id)
            .where(Message.message_provider == "assistant"), Message.created_at, lower, upper)
        pairs = pairs.distinct().subquery()
        count = func.count()
        return self.session.execute(select(Document.doc_id, Document.doc_name, Document.version,
            DocumentCategory.category_name, count.label("responses"))
            .select_from(pairs).join(Document, Document.doc_id == pairs.c.doc_id)
            .join(DocumentCategory, DocumentCategory.category_id == Document.category_id)
            .group_by(Document.doc_id, Document.doc_name, Document.version, DocumentCategory.category_name)
            .order_by(count.desc(), Document.doc_name, Document.doc_id).limit(10)).all()

    def unsupported_questions(self, lower, upper):
        question = aliased(Message, name="unsupported_question")
        valid_link = and_(question.message_id == Message.reply_to_message_id,
                          question.conversation_id == Message.conversation_id,
                          question.message_provider == "user")
        query = (select(Message.message_id.label("review_id"), Message.conversation_id,
                        User.email.label("user_email"), question.content.label("question"),
                        Message.content.label("answer"), Message.created_at,
                        self._reason().label("reason"))
                 .select_from(Message).join(Conversation, Conversation.id == Message.conversation_id)
                 .join(User, User.user_id == Conversation.user_id).outerjoin(question, valid_link)
                 .where(Message.message_provider == "assistant", Message.insufficient_context.is_(True)))
        query = self._period(query, Message.created_at, lower, upper)
        return self.session.execute(query.order_by(Message.created_at.desc().nulls_last(),
                                                   Message.message_id.desc()).limit(10)).all()
