"""Admin-only category maintenance. No AI resources are loaded."""

from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response

from backend.api.dependencies import require_admin
from backend.models.category_models import CategoryCreate, CategoryUpdate
from backend.services.category_service import CategoryService


router = APIRouter(prefix="/categories", tags=["Category management"])


def service(request: Request):
    return CategoryService(request.app.state.session_factory)


@router.get("")
def list_categories(actor=Depends(require_admin), categories=Depends(service)):
    return categories.list()


@router.post("", status_code=201)
def create_category(payload: CategoryCreate, actor=Depends(require_admin), categories=Depends(service)):
    return categories.create(payload, actor)


@router.patch("/{category_id}")
def update_category(category_id: UUID, payload: CategoryUpdate, actor=Depends(require_admin),
                    categories=Depends(service)):
    return categories.update(category_id, payload, actor)


@router.delete("/{category_id}", status_code=204)
def delete_category(category_id: UUID, actor=Depends(require_admin), categories=Depends(service)):
    categories.delete(category_id, actor)
    return Response(status_code=204)
