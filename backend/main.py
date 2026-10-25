"""FastAPI composition root: routes -> services -> repositories -> stores."""

import os
from pathlib import Path
from threading import Lock

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.exc import OperationalError

from backend.api import admin_reviews, analytics, audit, auth, categories, documents, experiments, profile, rag, search
from backend.database.connection import SessionLocal
from backend.services.auth_service import InvalidCredentialsError
from backend.services.document_processing_service import ConcurrentIngestionError, RecoveryPendingError
from backend.services.document_service import DocumentConflictError, DocumentNotFoundError
from backend.services.llm_service import LLMNotConfiguredError, LLMProviderError, LLMResponseError, configured_llm
from backend.utils.file_storage import FileStorage, UploadTooLargeError
from backend.utils.request_limits import RequestBodyLimitMiddleware


class RuntimeResources:
    """Load one cached model/client lazily, without slowing login or startup."""

    def __init__(self, embedder=None, retriever=None, llm=None):
        self.embedder, self.retriever = embedder, retriever
        self.llm = llm
        self.lock = Lock()

    def get_embedder(self):
        with self.lock:
            if self.embedder is None:
                from backend.services.embedding_service import EmbeddingService
                self.embedder = EmbeddingService()
            return self.embedder

    def get_retriever(self):
        with self.lock:
            if self.retriever is None:
                from backend.services.retrieval_service import RetrievalService
                self.retriever = RetrievalService()
            return self.retriever

    def get_llm(self):
        with self.lock:
            if self.llm is None:
                self.llm = configured_llm()
            return self.llm


def create_app(*, session_factory=None, embedder=None, retriever=None, upload_root=None,
               max_upload_bytes=20 * 1024 * 1024, llm=None, experiment_root=None):
    app = FastAPI(title="Internal Helpdesk Knowledge API", version="0.1.0")
    app.state.session_factory = session_factory or SessionLocal
    app.state.resources = RuntimeResources(embedder, retriever, llm)
    app.state.experiment_root = experiment_root
    app.state.storage = FileStorage(upload_root or Path(__file__).resolve().parents[1] / "data/uploads", max_upload_bytes)
    app.state.cookie_secure = os.getenv("AUTH_COOKIE_SECURE", "false").lower() == "true"
    origins = [o.strip() for o in os.getenv("FRONTEND_ORIGINS", "http://127.0.0.1:5173,http://localhost:5173").split(",") if o.strip()]
    app.add_middleware(CORSMiddleware, allow_origins=origins, allow_credentials=True,
                       allow_methods=["GET", "POST", "PATCH", "DELETE"],
                       allow_headers=["Content-Type", "X-Requested-With"])

    @app.middleware("http")
    async def browser_origin_check(request: Request, call_next):
        if request.method in {"POST", "PATCH", "DELETE", "PUT"}:
            origin = request.headers.get("origin")
            if origin and origin not in origins and origin != str(request.base_url).rstrip("/"):
                return JSONResponse(status_code=403, content={"detail": "Untrusted request origin"})
            if origin and request.cookies.get("helpdesk_session") and request.headers.get("x-requested-with") != "helpdesk-ui":
                return JSONResponse(status_code=403, content={"detail": "Missing request verification header"})
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    # Include bounded multipart metadata overhead. The per-file storage check
    # still enforces the exact limit for the accepted single source file.
    app.add_middleware(RequestBodyLimitMiddleware, max_bytes=max_upload_bytes + 1024 * 1024)

    def register_error(exception_type, status):
        async def handler(request, exc):
            return JSONResponse(status_code=status, content={"detail": str(exc)})
        app.add_exception_handler(exception_type, handler)

    for error, status in ((InvalidCredentialsError, 401), (DocumentNotFoundError, 404),
                          (DocumentConflictError, 409), (ConcurrentIngestionError, 409),
                          (RecoveryPendingError, 503), (UploadTooLargeError, 413),
                          (LLMNotConfiguredError, 503), (LLMProviderError, 502), (LLMResponseError, 502),
                          (PermissionError, 403), (ValueError, 400)):
        register_error(error, status)

    @app.exception_handler(OperationalError)
    async def database_unavailable(request, exc):
        return JSONResponse(status_code=503, content={"detail": "The database is unavailable. Check the local backend setup."})

    @app.exception_handler(RuntimeError)
    async def index_unavailable(request, exc):
        return JSONResponse(status_code=503, content={"detail": "The indexing operation could not complete. Review the document status and retry."})

    @app.get("/api/health", tags=["Status"])
    def health():
        configured = bool(getattr(app.state.resources.get_llm(), "configured", True))
        return {"status": "ok", "retrieval_only": not configured, "generation_configured": configured}

    for router in (auth.router, documents.router, search.router, audit.router, rag.router, experiments.router, admin_reviews.router, analytics.router, categories.router, profile.router):
        app.include_router(router, prefix="/api")
    dist = Path(__file__).resolve().parents[1] / "frontend/dist"
    if dist.is_dir():
        app.mount("/", StaticFiles(directory=dist, html=True), name="frontend")
    return app


app = create_app()
