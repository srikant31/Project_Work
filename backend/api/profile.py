"""An authenticated employee or admin may inspect only their own profile."""

from fastapi import APIRouter, Depends, Request

from backend.api.dependencies import current_user
from backend.services.profile_service import ProfileService


router = APIRouter(prefix="/profile", tags=["Profile"])


@router.get("")
def profile(request: Request, actor=Depends(current_user)):
    return ProfileService(request.app.state.session_factory).profile(
        actor, request.cookies.get("helpdesk_session"))
