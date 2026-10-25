from fastapi import APIRouter, Depends, Request

from backend.api.dependencies import current_user
from backend.models.api_models import SearchRequest
from backend.services.search_service import SearchService

router = APIRouter(tags=["Retrieval"])


@router.post("/search")
def search(payload: SearchRequest, request: Request, actor=Depends(current_user)):
    service = SearchService(session_factory=request.app.state.session_factory,
                            embedder=request.app.state.resources.get_embedder(),
                            retriever=request.app.state.resources.get_retriever())
    return service.search(payload.question, payload.top_k)
