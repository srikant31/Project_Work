"""Administrative taxonomy changes with atomic audit records."""

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError

from backend.repositories.category_management_repository import CategoryManagementRepository
from backend.services.audit_service import AuditService


class CategoryService:
    def __init__(self, session_factory):
        self.session_factory = session_factory

    @staticmethod
    def dto(row, count):
        return {"category_id": str(row.category_id), "category_name": row.category_name,
                "description": row.description, "document_count": count}

    def list(self):
        with self.session_factory() as session:
            return {"categories": [self.dto(row, count) for row, count in
                                   CategoryManagementRepository(session).list_counts()]}

    def create(self, payload, actor):
        with self.session_factory() as session:
            try:
                repository = CategoryManagementRepository(session)
                repository.lock_writes()
                if repository.duplicate(payload.category_name):
                    raise HTTPException(409, "A category with this name already exists")
                row = repository.create(payload.category_name, payload.description)
                AuditService(session).record(actor, "category.created", "category", row.category_id,
                                             {"category_name": row.category_name})
                result = self.dto(row, 0)
                session.commit()
                return {"category": result}
            except IntegrityError as exc:
                session.rollback()
                raise HTTPException(409, "Category could not be created because its name is already in use") from exc

    def update(self, category_id, payload, actor):
        with self.session_factory() as session:
            try:
                repository = CategoryManagementRepository(session)
                repository.lock_writes()
                row = repository.get(category_id, lock=True)
                if row is None:
                    raise HTTPException(404, "Category not found")
                before_name = row.category_name
                changes = payload.model_dump(exclude_unset=True)
                if "category_name" in changes:
                    if repository.duplicate(changes["category_name"], excluding=category_id):
                        raise HTTPException(409, "A category with this name already exists")
                    row.category_name = changes["category_name"]
                if "description" in changes:
                    row.description = changes["description"]
                session.flush()
                AuditService(session).record(actor, "category.updated", "category", row.category_id,
                    {"previous_name": before_name, "category_name": row.category_name,
                     "changed_fields": sorted(changes)})
                result = self.dto(row, repository.count_documents(category_id))
                session.commit()
                return {"category": result}
            except IntegrityError as exc:
                session.rollback()
                raise HTTPException(409, "Category could not be updated because its name is already in use") from exc

    def delete(self, category_id, actor):
        with self.session_factory() as session:
            try:
                repository = CategoryManagementRepository(session)
                repository.lock_writes()
                row = repository.get(category_id, lock=True)
                if row is None:
                    raise HTTPException(404, "Category not found")
                if repository.count_documents(category_id):
                    raise HTTPException(409, "Category is referenced by document versions and cannot be removed")
                AuditService(session).record(actor, "category.deleted", "category", category_id,
                                             {"category_name": row.category_name})
                repository.delete(row)
                session.commit()
            except IntegrityError as exc:
                session.rollback()
                raise HTTPException(409, "Category is referenced by document versions and cannot be removed") from exc
