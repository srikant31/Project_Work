from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse

from backend.api.dependencies import current_user, document_service, require_admin
from backend.models.api_models import AvailabilityRequest, ReindexRequest
from backend.services.document_service import DocumentService
from backend.utils.date_ranges import CalendarDate, utc_bounds

router = APIRouter(prefix="/documents", tags=["Document management"])


def document_read_service(request: Request):
    # Metadata listing and upload choices require PostgreSQL only. Do not load
    # embedding weights or connect to the vector store just to browse documents.
    with DocumentService(session_factory=request.app.state.session_factory,
                         storage=request.app.state.storage) as service:
        yield service


@router.get("")
def list_documents(uploaded_from: CalendarDate | None = None, uploaded_to: CalendarDate | None = None,
                   version: int | None = Query(None, ge=1), actor=Depends(require_admin),
                   service=Depends(document_read_service)):
    try:
        lower, upper = utc_bounds(uploaded_from, uploaded_to)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return service.list_documents(uploaded_from=lower, uploaded_before=upper, version=version)


@router.get("/categories")
def categories(actor=Depends(require_admin), service=Depends(document_read_service)):
    return service.categories()


@router.post("/upload", status_code=201)
def upload(actor=Depends(require_admin), service=Depends(document_service),
           file: UploadFile = File(...), category_id: UUID = Form(...),
           doc_name: str | None = Form(None), is_online: bool = Form(True)):
    return service.upload(file.file, file.filename, category_id, actor,
                          doc_name=doc_name, is_online=is_online)


@router.get("/{document_id}")
def details(document_id: UUID, actor=Depends(require_admin), service=Depends(document_service)):
    return service.details(document_id)


@router.post("/{document_id}/replace")
def replace(document_id: UUID, actor=Depends(require_admin), service=Depends(document_service),
            file: UploadFile = File(...)):
    return service.replace(document_id, file.file, file.filename, actor)


@router.post("/{document_id}/reindex")
def reindex(document_id: UUID, payload: ReindexRequest, actor=Depends(require_admin),
            service=Depends(document_service)):
    return service.reindex(document_id, actor, chunk_size=payload.chunk_size, overlap=payload.overlap)


@router.patch("/{document_id}/availability")
def availability(document_id: UUID, payload: AvailabilityRequest, actor=Depends(require_admin),
                 service=Depends(document_service)):
    return service.set_availability(document_id, payload.is_online, actor)


@router.get("/{document_id}/file")
def source_file(document_id: UUID, actor=Depends(current_user), service=Depends(document_service)):
    path, name = service.source_file(document_id, actor)
    return FileResponse(path, filename=name)
