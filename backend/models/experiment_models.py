"""Strict request contracts for isolated retrieval configuration experiments."""

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, field_validator, model_validator


class ExperimentQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question_id: str = Field(min_length=1, max_length=100)
    question: str = Field(min_length=1, max_length=4000)
    expected_doc_id: UUID | None = None
    expected_source_key: str | None = Field(default=None, max_length=2000)
    evidence_quote: str | None = Field(default=None, max_length=12000)
    answerable: StrictBool

    @field_validator("question_id", "question")
    @classmethod
    def nonblank(cls, value):
        value = value.strip()
        if not value or "\x00" in value:
            raise ValueError("Question identifiers and text must be nonempty and contain no NUL")
        return value

    @field_validator("expected_source_key", "evidence_quote")
    @classmethod
    def optional_nonblank(cls, value):
        if value is None:
            return None
        if "\x00" in value:
            raise ValueError("Source keys and evidence must contain no NUL")
        # Source identity is exact: a filename/key can legitimately contain
        # surrounding spaces. Whitespace-only optional fields still mean absent.
        return value if value.strip() else None

    @model_validator(mode="after")
    def validate_labels(self):
        if self.answerable and self.expected_doc_id is None and self.expected_source_key is None:
            raise ValueError("An answerable question requires an expected document ID or source key")
        if not self.answerable and any(
            value is not None for value in (self.expected_doc_id, self.expected_source_key, self.evidence_quote)
        ):
            raise ValueError("Unsupported questions must not have expected source or evidence labels")
        return self


class ExperimentConfiguration(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chunk_size: StrictInt = Field(ge=8, le=4096)
    overlap: StrictInt = Field(ge=0, le=4095)
    top_k: StrictInt = Field(ge=1, le=20)

    @model_validator(mode="after")
    def validate_overlap(self):
        if self.overlap >= self.chunk_size:
            raise ValueError("overlap must be smaller than chunk_size")
        return self


class ExperimentRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_ids: list[UUID] = Field(min_length=1, max_length=10)
    questions: list[ExperimentQuestion] = Field(min_length=1, max_length=100)
    configurations: list[ExperimentConfiguration] = Field(min_length=1, max_length=12)

    @model_validator(mode="after")
    def validate_duplicates(self):
        if len(set(self.document_ids)) != len(self.document_ids):
            raise ValueError("Document IDs must be unique")
        question_ids = [question.question_id for question in self.questions]
        if len(set(question_ids)) != len(question_ids):
            raise ValueError("Question IDs must be unique")
        settings = [(c.chunk_size, c.overlap, c.top_k) for c in self.configurations]
        if len(set(settings)) != len(settings):
            raise ValueError("Configurations must be unique")
        if not any(question.answerable for question in self.questions):
            raise ValueError("At least one answerable question is required for comparative metrics")
        selected = set(self.document_ids)
        if any(q.expected_doc_id is not None and q.expected_doc_id not in selected for q in self.questions):
            raise ValueError("Expected document IDs must belong to the selected corpus")
        return self
