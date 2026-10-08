from backend.database.models import DocumentCategory


class CategoryRepository:
    """Read-only lookups. Categories are seeded via the migration, not
    created ad-hoc by application code — that keeps the taxonomy a
    deliberate, reviewed list rather than whatever string a caller typed."""

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
