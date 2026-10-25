from backend.database.models import AuditLog, User


class AuditRepository:
    """Audit persistence. Commits belong to the operation being audited."""

    def __init__(self, session):
        self.session = session

    def create(self, user_id, action, entity_type, entity_id, details=None):
        row = AuditLog(
            user_id=user_id, action=action, entity_type=entity_type,
            entity_id=entity_id, details=details,
        )
        self.session.add(row)
        self.session.flush()
        return row

    def list_recent(self, limit=50):
        return (
            self.session.query(AuditLog, User.email)
            .join(User, User.user_id == AuditLog.user_id)
            .order_by(AuditLog.created_at.desc(), AuditLog.audit_log_id.desc())
            .limit(limit)
            .all()
        )
