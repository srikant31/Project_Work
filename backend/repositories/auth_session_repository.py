from datetime import UTC, datetime

from backend.database.models import AuthSession


class AuthSessionRepository:
    def __init__(self, session):
        self.session = session

    def create(self, user_id, token_hash: str, expires_at: datetime) -> AuthSession:
        if len(token_hash) != 64:
            raise ValueError("Session digest must be a SHA-256 hex value")
        if expires_at.tzinfo is not None:
            expires_at = expires_at.astimezone(UTC).replace(tzinfo=None)
        row = AuthSession(user_id=user_id, token_hash=token_hash, expires_at=expires_at)
        self.session.add(row)
        self.session.flush()
        return row

    def get_valid(self, token_hash: str, now: datetime | None = None) -> AuthSession | None:
        now = now or datetime.now(UTC).replace(tzinfo=None)
        if now.tzinfo is not None:
            now = now.astimezone(UTC).replace(tzinfo=None)
        return self.session.query(AuthSession).filter(
            AuthSession.token_hash == token_hash, AuthSession.expires_at > now
        ).one_or_none()

    def revoke(self, token_hash: str) -> int:
        return self.session.query(AuthSession).filter(
            AuthSession.token_hash == token_hash
        ).delete(synchronize_session="fetch")
