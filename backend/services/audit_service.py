"""Bounded, secret-free audit details for administrative operations."""

from datetime import UTC, datetime
import math
import uuid

from backend.repositories.audit_repository import AuditRepository


_SECRET_PARTS = ("password", "token", "secret", "cookie", "authorization", "credential")


def sanitize_details(value, depth=0):
    if depth > 4:
        return "[depth limit]"
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, str):
        return value[:1000]
    if isinstance(value, dict):
        return {
            str(key)[:100]: sanitize_details(item, depth + 1)
            for key, item in list(value.items())[:40]
            if not any(part in str(key).lower() for part in _SECRET_PARTS)
        }
    if isinstance(value, (list, tuple)):
        return [sanitize_details(item, depth + 1) for item in value[:100]]
    if isinstance(value, (uuid.UUID, datetime)):
        return str(value)
    # Never serialize arbitrary application objects (which might expose secrets).
    return "[unsupported value]"


class AuditService:
    def __init__(self, session):
        self.repository = AuditRepository(session)

    def record(self, actor, action, entity_type, entity_id, details=None):
        if not isinstance(action, str) or not 1 <= len(action) <= 50:
            raise ValueError("Audit action must contain 1 to 50 characters")
        if not isinstance(entity_type, str) or not 1 <= len(entity_type) <= 50:
            raise ValueError("Audit entity type must contain 1 to 50 characters")
        if details is not None and not isinstance(details, dict):
            raise ValueError("Audit details must be an object")
        actor_id = getattr(actor, "user_id", actor)
        return self.repository.create(
            uuid.UUID(str(actor_id)), action, entity_type, uuid.UUID(str(entity_id)),
            sanitize_details(details),
        )

    def list_recent(self, limit=50):
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 200:
            raise ValueError("Audit limit must be between 1 and 200")
        result = []
        for row, email in self.repository.list_recent(limit):
            stamp = row.created_at
            if stamp is not None:
                stamp = (
                    stamp.replace(tzinfo=UTC) if stamp.tzinfo is None else stamp.astimezone(UTC)
                ).isoformat().replace("+00:00", "Z")
            result.append({
                "id": str(row.audit_log_id),
                "audit_log_id": str(row.audit_log_id), "user_id": str(row.user_id),
                "user_email": email, "actor": email, "action": row.action,
                "entity_type": row.entity_type, "entity_id": str(row.entity_id),
                "created_at": stamp, "ts": stamp, "details": sanitize_details(row.details),
            })
        return result
