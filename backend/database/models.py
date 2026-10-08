import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    department: Mapped[str | None] = mapped_column(String(100), nullable=True)


class DocumentCategory(Base):
    __tablename__ = "documentcategories"

    category_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    category_name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)


class Document(Base):
    __tablename__ = "documents"

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
    status: Mapped[str] = mapped_column(String(20), default="active", server_default="active")
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    doc_path: Mapped[str] = mapped_column(Text, nullable=False)
    doc_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    chunks: Mapped[list["DocumentChunk"]] = relationship(back_populates="document")
    ingestion_jobs: Mapped[list["IngestionJob"]] = relationship(back_populates="document")


class DocumentChunk(Base):
    __tablename__ = "documentchunks"

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
