from typing import Literal
from uuid import UUID

from pydantic import Field

from backend.models.api_models import RequestModel, SearchRequest


class ChatRequest(SearchRequest):
    conversation_id: UUID | None = None


class FeedbackRequest(RequestModel):
    response_id: UUID
    rating: Literal["up", "down"]
