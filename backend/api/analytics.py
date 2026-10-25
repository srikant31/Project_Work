"""Admin analytics reads PostgreSQL only; it never initializes AI clients."""

from fastapi import APIRouter, Depends, HTTPException, Request

from backend.api.dependencies import require_admin
from backend.services.analytics_service import AnalyticsService
from backend.utils.date_ranges import CalendarDate, validate_range


router = APIRouter(prefix="/analytics", tags=["Admin analytics"])


def service(request: Request):
    return AnalyticsService(request.app.state.session_factory)


@router.get("")
def analytics(date_from: CalendarDate | None = None, date_to: CalendarDate | None = None,
              actor=Depends(require_admin), analytics_service=Depends(service)):
    try:
        validate_range(date_from, date_to)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return analytics_service.report(date_from, date_to)
