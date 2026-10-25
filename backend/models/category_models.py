"""Validated category maintenance inputs; unknown fields are never accepted."""

from pydantic import field_validator, model_validator

from backend.models.api_models import RequestModel


class CategoryFields(RequestModel):
    @field_validator("category_name", check_fields=False)
    @classmethod
    def validate_name(cls, value):
        if value is None:
            raise ValueError("Category name cannot be null")
        value = value.strip()
        if not 1 <= len(value) <= 100 or not value.isprintable():
            raise ValueError("Category name must contain 1 to 100 printable characters")
        return value

    @field_validator("description", check_fields=False)
    @classmethod
    def validate_description(cls, value):
        if value is not None and len(value) > 1000:
            raise ValueError("Description must contain at most 1000 characters")
        return value


class CategoryCreate(CategoryFields):
    category_name: str
    description: str | None = None


class CategoryUpdate(CategoryFields):
    category_name: str | None = None
    description: str | None = None

    @model_validator(mode="after")
    def require_changes(self):
        if not self.model_fields_set:
            raise ValueError("Provide a category name or description")
        return self
