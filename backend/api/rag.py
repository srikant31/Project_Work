"""Thin prototype endpoints; all orchestration and persistence live below routes."""

from uuid import UUID

from fastapi import APIRouter, Depends, Request

from backend.api.dependencies import current_user
from backend.models.api_models import SearchRequest
from backend.models.rag_models import ChatRequest, FeedbackRequest
from backend.services.conversation_service import ConversationService
from backend.services.feedback_service import FeedbackService
from backend.services.rag_service import RAGService

router = APIRouter(tags=["Grounded assistant"])


def rag_service(request: Request):
    return RAGService(session_factory=request.app.state.session_factory,
        embedder=None, retriever=None, llm=request.app.state.resources.get_llm(),
        embedding_provider=request.app.state.resources.get_embedder,
        retrieval_provider=request.app.state.resources.get_retriever)


@router.get("/rag/config")
def config(request: Request, actor=Depends(current_user)):
    # Config/login do not load an embedding model or open/create Chroma.
    service = RAGService(session_factory=request.app.state.session_factory,
                         embedder=None, retriever=None, llm=request.app.state.resources.get_llm())
    return service.config()


@router.post("/rag/preview")
def preview(payload: SearchRequest, actor=Depends(current_user), service=Depends(rag_service)):
    return service.preview(payload.question, payload.top_k)


@router.post("/chat")
def chat(payload: ChatRequest, actor=Depends(current_user), service=Depends(rag_service)):
    return service.answer(payload.question, actor, payload.top_k, payload.conversation_id)


@router.post("/feedback")
def feedback(payload: FeedbackRequest, request: Request, actor=Depends(current_user)):
    return FeedbackService(request.app.state.session_factory).rate(payload.response_id, actor, payload.rating)


@router.get("/conversations")
def conversations(request: Request, actor=Depends(current_user)):
    return ConversationService(request.app.state.session_factory).list(actor)


@router.get("/conversations/{conversation_id}")
def conversation(conversation_id: UUID, request: Request, actor=Depends(current_user)):
    return ConversationService(request.app.state.session_factory).get(conversation_id, actor)
