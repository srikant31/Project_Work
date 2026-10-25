import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Float,
    Index,
    Integer,
    JSON,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("role IN ('admin','employee')", name="users_role_check"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    department: Mapped[str | None] = mapped_column(String(100), nullable=True)


class AuthSession(Base):
    """Revocable sessions. Only the SHA-256 digest of the bearer token is stored."""

    __tablename__ = "authsessions"
    __table_args__ = (
        CheckConstraint("length(token_hash) = 64", name="authsessions_token_hash_length"),
        Index("idx_authsessions_user_id", "user_id"),
        Index("idx_authsessions_expires_at", "expires_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)


class AuditLog(Base):
    __tablename__ = "auditlogs"
    __table_args__ = (Index("idx_auditlogs_created_at", "created_at"),)

    audit_log_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.user_id"), nullable=False
    )
    action: Mapped[str] = mapped_column(String(50), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(50), nullable=False)
    entity_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    details: Mapped[dict | None] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"), nullable=True
    )


class DocumentCategory(Base):
    __tablename__ = "documentcategories"

    category_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    category_name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (
        CheckConstraint(
            "status IN ('staging','active','deactivated','failed')",
            name="documents_status_check",
        ),
        Index(
            "uq_documents_active_source",
            "category_id", "source_key",
            unique=True,
            postgresql_where=text("status = 'active' AND source_key IS NOT NULL"),
            sqlite_where=text("status = 'active' AND source_key IS NOT NULL"),
        ),
        Index(
            "uq_documents_source_version",
            "category_id", "source_key", "version",
            unique=True,
            postgresql_where=text("source_key IS NOT NULL"),
            sqlite_where=text("source_key IS NOT NULL"),
        ),
    )

    doc_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    doc_name: Mapped[str] = mapped_column(String(255), nullable=False)
    doc_type: Mapped[str] = mapped_column(String(10), nullable=False)
    category_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("documentcategories.category_id"), nullable=False
    )
    uploaded_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.user_id"), nullable=True
    )
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    status: Mapped[str] = mapped_column(String(20), default="staging", server_default="staging")
    # Administrative visibility is independent of ingestion/publication state.
    # Taking a source offline must not destroy its chunks or vector history.
    is_online: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true", nullable=False)
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    doc_path: Mapped[str] = mapped_column(Text, nullable=False)
    doc_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # NULL identifies historical rows whose source identity/provenance was not
    # captured. A migration must not invent hashes from a mutable current path.
    source_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    text_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    pipeline_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    pipeline_config: Mapped[dict | None] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"), nullable=True
    )
    extracted_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    extraction_warnings: Mapped[list | None] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"), nullable=True
    )
    cleanup_pending: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    chunks: Mapped[list["DocumentChunk"]] = relationship(back_populates="document")
    ingestion_jobs: Mapped[list["IngestionJob"]] = relationship(back_populates="document")


class DocumentChunk(Base):
    __tablename__ = "documentchunks"
    __table_args__ = (
        Index("uq_documentchunks_document_index", "document_id", "chunk_index", unique=True),
    )

    chunk_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("documents.doc_id"), nullable=False
    )
    chunk_text: Mapped[str] = mapped_column(Text, nullable=False)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    section_heading: Mapped[str | None] = mapped_column(String(255), nullable=True)
    page_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    embedding_model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # Format: "<collection_name>:<chunk_id>". Identifies WHICH vector store holds
    # this chunk's embedding, so multiple embedding models can coexist.
    embedding_vector_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    token_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Half-open offsets in Document.extracted_text, never byte offsets or
    # positions in the original DOCX/PDF container.
    start_char: Mapped[int | None] = mapped_column(Integer, nullable=True)
    end_char: Mapped[int | None] = mapped_column(Integer, nullable=True)
    chunk_created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")

    document: Mapped["Document"] = relationship(back_populates="chunks")


class IngestionJob(Base):
    __tablename__ = "ingestionjobs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("documents.doc_id"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    error_log: Mapped[str | None] = mapped_column(Text, nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")

    document: Mapped["Document"] = relationship(back_populates="ingestion_jobs")


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.user_id"), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (
        CheckConstraint("message_provider IN ('user','assistant')", name="messages_message_provider_check"),
        Index("idx_messages_reply_to_message_id", "reply_to_message_id"),
    )

    message_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    conversation_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("conversations.id"), nullable=False)
    # Historical answers remain unlinked: temporal ordering is not proof that a
    # particular question produced an answer. New turns record the exact link.
    reply_to_message_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("messages.message_id", name="messages_reply_to_message_id_fkey"),
        nullable=True,
    )
    message_provider: Mapped[str] = mapped_column(String(10), nullable=False)
    model_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    response_time_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    top_k_used: Mapped[int | None] = mapped_column(Integer, nullable=True)
    insufficient_context: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    generation_metadata: Mapped[dict | None] = mapped_column(JSON().with_variant(JSONB, "postgresql"), nullable=True)


class RetrievedContext(Base):
    __tablename__ = "retrievedcontext"

    message_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("messages.message_id"), primary_key=True)
    chunk_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("documentchunks.chunk_id"), primary_key=True)
    similarity_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    rank: Mapped[int | None] = mapped_column(Integer, nullable=True)


class Feedback(Base):
    __tablename__ = "feedback"
    __table_args__ = (CheckConstraint("rating IN ('up','down')", name="feedback_rating_check"),)

    message_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("messages.message_id"), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.user_id"), primary_key=True)
    rating: Mapped[str | None] = mapped_column(String(10), nullable=True)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    # NULL is truthful for historical votes whose submission time is unknown.
    # Application writes set UTC times; migration must not backfill them.
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
