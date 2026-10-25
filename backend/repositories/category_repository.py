from backend.database.models import DocumentCategory


class CategoryRepository:
    """Read-only ingestion lookups. Taxonomy maintenance is handled separately
    by CategoryService with admin authorization, validation and audit records."""

    def __init__(self, session):
        self.session = session

    def get_by_name(self, category_name: str) -> DocumentCategory | None:
        return (
            self.session.query(DocumentCategory)
            .filter(DocumentCategory.category_name == category_name)
            .first()
        )

    def list_all(self) -> list[DocumentCategory]:
        return (
            self.session.query(DocumentCategory)
            .order_by(DocumentCategory.category_name)
            .all()
        )

    def require_by_name(self, category_name: str) -> DocumentCategory:
        """Raises with the full valid list in the message — so a typo fails
        loudly and tells you what you should have typed, instead of a bare
        'not found' or, worse, a NULL sneaking through."""
        category = self.get_by_name(category_name)
        if category is None:
            known = [c.category_name for c in self.list_all()]
            raise ValueError(
                f"Unknown category '{category_name}'. Known categories: {known}"
            )
        return category
