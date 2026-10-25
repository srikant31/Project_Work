from fastapi import APIRouter, Depends, Request, Response

from backend.api.dependencies import auth_service, current_user
from backend.models.api_models import LoginRequest

router = APIRouter(prefix="/auth", tags=["Authentication"])


def user_response(user):
    return {"user": {"user_id": str(user.user_id), "email": user.email,
                     "role": user.role, "department": user.department}}


@router.post("/login")
def login(payload: LoginRequest, response: Response, request: Request, service=Depends(auth_service)):
    user, token = service.login(payload.email, payload.password)
    response.set_cookie("helpdesk_session", token, max_age=8 * 3600, httponly=True,
                        samesite="lax", secure=request.app.state.cookie_secure, path="/")
    return user_response(user)


@router.get("/me")
def me(user=Depends(current_user)):
    return user_response(user)


@router.post("/logout", status_code=204)
def logout(request: Request, response: Response, service=Depends(auth_service)):
    token = request.cookies.get("helpdesk_session")
    if token:
        service.logout(token)
    response.delete_cookie("helpdesk_session", path="/")
