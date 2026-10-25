from fastapi import APIRouter, Depends, Query, Request

from backend.api.dependencies import require_admin
from backend.services.audit_service import AuditService

router = APIRouter(tags=["Administration"])


@router.get("/audit-logs")
def logs(request: Request, limit: int = Query(50, ge=1, le=200), actor=Depends(require_admin)):
    with request.app.state.session_factory() as session:
        return {"events": AuditService(session).list_recent(limit)}
