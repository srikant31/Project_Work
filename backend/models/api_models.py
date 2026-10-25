"""HTTP contracts; database mappings remain in backend/database/models.py."""

from pydantic import BaseModel, ConfigDict, Field, StrictBool, model_validator


class RequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LoginRequest(RequestModel):
    email: str = Field(min_length=3, max_length=255)
    password: str = Field(min_length=1, max_length=256)


class AvailabilityRequest(RequestModel):
    is_online: StrictBool


class ReindexRequest(RequestModel):
    chunk_size: int = Field(default=200, ge=8, le=4096)
    overlap: int = Field(default=40, ge=0, le=4095)

    @model_validator(mode="after")
    def check_overlap(self):
        if self.overlap >= self.chunk_size:
            raise ValueError("overlap must be smaller than chunk_size")
        return self


class SearchRequest(RequestModel):
    question: str = Field(min_length=1, max_length=4000)
    top_k: int = Field(default=3, ge=1, le=20)
