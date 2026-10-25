"""Request-scoped services and server-side permission checks."""

from fastapi import Depends, HTTPException, Request

from backend.services.auth_service import AuthService
from backend.services.document_service import DocumentService


def auth_service(request: Request):
    with AuthService(session_factory=request.app.state.session_factory) as service:
        yield service


def current_user(request: Request, service=Depends(auth_service)):
    token = request.cookies.get("helpdesk_session")
    user = service.current_user(token) if token else None
    if user is None:
        raise HTTPException(status_code=401, detail="Please sign in")
    return user


def require_admin(user=Depends(current_user)):
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Administrator access is required")
    return user


def document_service(request: Request):
    with DocumentService(session_factory=request.app.state.session_factory,
                         retriever=request.app.state.resources.get_retriever(),
                         storage=request.app.state.storage,
                         embedding_provider=request.app.state.resources.get_embedder) as service:
        yield service
