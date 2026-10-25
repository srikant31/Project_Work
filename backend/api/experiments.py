from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request

from backend.api.dependencies import require_admin
from backend.models.experiment_models import ExperimentRunRequest
from backend.repositories.experiment_repository import ExperimentNotFoundError
from backend.services.experiment_service import ExperimentService


router = APIRouter(prefix="/experiments", tags=["Retrieval experiments"])


def _service(request, *, with_model=False):
    return ExperimentService(
        session_factory=request.app.state.session_factory, storage=request.app.state.storage,
        embedder=request.app.state.resources.get_embedder() if with_model else None,
        output_root=getattr(request.app.state, "experiment_root", None),
    )


@router.get("/config")
def configuration(request: Request, actor=Depends(require_admin)):
    return _service(request).config()


@router.post("/run")
def run(payload: ExperimentRunRequest, request: Request, actor=Depends(require_admin)):
    return _service(request, with_model=True).run(payload)


@router.get("/{run_id}")
def saved_run(run_id: UUID, request: Request, actor=Depends(require_admin)):
    try:
        return _service(request).get(run_id)
    except ExperimentNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Experiment not found") from exc
