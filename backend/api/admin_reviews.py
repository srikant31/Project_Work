"""Admin-only, bounded history reads. These routes load no AI resources."""

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from backend.api.dependencies import require_admin
from backend.services.admin_review_service import AdminReviewService
from backend.utils.date_ranges import CalendarDate, validate_range


router = APIRouter(prefix="/admin", tags=["Admin user feedback"])


def service(request: Request):
    return AdminReviewService(request.app.state.session_factory)


@router.get("/users")
def users(search: str = Query("", max_length=200), limit: int = Query(25, ge=1, le=100),
          offset: int = Query(0, ge=0), actor=Depends(require_admin), reviews=Depends(service)):
    return reviews.users(search, limit, offset)


@router.get("/conversations")
def conversations(user_id: UUID, limit: int = Query(50, ge=1, le=100),
                  offset: int = Query(0, ge=0), actor=Depends(require_admin), reviews=Depends(service)):
    return reviews.conversations(user_id, limit, offset)


@router.get("/reviews")
def review_list(user_id: UUID | None = None, conversation_id: UUID | None = None,
                feedback: Literal["all", "up", "down", "unrated"] = "all",
                status: Literal["all", "answered", "unanswered", "unknown"] = "all",
                question_from: CalendarDate | None = None, question_to: CalendarDate | None = None,
                search: str = Query("", max_length=200), limit: int = Query(20, ge=1, le=100),
                offset: int = Query(0, ge=0), actor=Depends(require_admin), reviews=Depends(service)):
    try:
        validate_range(question_from, question_to)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return reviews.reviews(user_id=user_id, conversation_id=conversation_id, feedback=feedback,
                           status=status, search=search, limit=limit, offset=offset,
                           question_from=question_from, question_to=question_to)


@router.get("/reviews/{message_id}")
def review_detail(message_id: UUID, actor=Depends(require_admin), reviews=Depends(service)):
    return reviews.review(message_id)
