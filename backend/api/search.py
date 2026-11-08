import os

from fastapi import APIRouter, Depends, Request

from backend.api.dependencies import current_user
from backend.models.api_models import SearchRequest
from backend.services.search_service import SearchService

router = APIRouter(tags=["Retrieval"])


@router.post("/search")
def search(payload: SearchRequest, request: Request, mode: str | None = None, actor=Depends(current_user)):
    keyword_only = (mode or os.getenv("RETRIEVAL_MODE", "vector")) == "bm25"
    service = SearchService(session_factory=request.app.state.session_factory,
                            embedder=None if keyword_only else request.app.state.resources.get_embedder(),
                            retriever=None if keyword_only else request.app.state.resources.get_retriever())
    return service.search(payload.question, payload.top_k, mode=mode)
