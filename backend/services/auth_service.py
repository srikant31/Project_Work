"""Eight-hour, revocable opaque sessions with no stored bearer tokens."""

from datetime import UTC, datetime, timedelta

from backend.database.connection import SessionLocal
from backend.repositories.auth_session_repository import AuthSessionRepository
from backend.repositories.user_repository import UserRepository
from backend.utils.security import (
    hash_password, hash_session_token, new_session_token, password_needs_rehash, verify_password,
)


class InvalidCredentialsError(ValueError):
    pass


class AuthService:
    """Owns authentication commits; repositories only flush.

    Returned User rows are detached with loaded scalar fields, so request
    serializers can safely use them after the service session closes.
    """

    def __init__(self, db_session=None, *, session_factory=None, session_hours=8):
        if not isinstance(session_hours, (int, float)) or not 0 < session_hours <= 24:
            raise ValueError("Session duration must be greater than zero and at most 24 hours")
        self.session = db_session if db_session is not None else (session_factory or SessionLocal)()
        self._owns_session = db_session is None
        self.session_hours = session_hours
        self.users = UserRepository(self.session)
        self.sessions = AuthSessionRepository(self.session)

    def close(self):
        if self._owns_session:
            self.session.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False

    def login(self, email, password):
        try:
            try:
                user = self.users.get_by_email(email)
            except ValueError:
                user = None
            previous_hash = user.password_hash if user else None
            valid_password = verify_password(password, previous_hash)
            if user is None or not valid_password or user.role not in ("admin", "employee"):
                raise InvalidCredentialsError("Invalid email or password")
            if password_needs_rehash(previous_hash):
                # Preserve access for valid legacy passwords below today's new-account
                # minimum. Upgrade and session creation share one transaction.
                replacement_hash = hash_password(password, enforce_policy=False)
                if not self.users.upgrade_password_hash(user.user_id, previous_hash, replacement_hash):
                    raise InvalidCredentialsError("Invalid email or password")
            raw_token = new_session_token()
            self.sessions.create(
                user.user_id,
                hash_session_token(raw_token),
                datetime.now(UTC).replace(tzinfo=None) + timedelta(hours=self.session_hours),
            )
            self.session.commit()
            self.session.refresh(user)
            self.session.expunge(user)
            self.session.rollback()  # release refresh's read transaction
            return user, raw_token
        except Exception:
            self.session.rollback()
            raise

    def current_user(self, raw_token):
        try:
            try:
                digest = hash_session_token(raw_token)
            except ValueError:
                return None
            row = self.sessions.get_valid(digest)
            if row is None:
                return None
            user = self.users.get_by_id(row.user_id)
            if user is None or user.role not in ("admin", "employee"):
                return None
            self.session.expunge(user)
            return user
        finally:
            self.session.rollback()

    def logout(self, raw_token):
        try:
            try:
                digest = hash_session_token(raw_token)
            except ValueError:
                return False
            removed = self.sessions.revoke(digest)
            self.session.commit()
            return bool(removed)
        except Exception:
            self.session.rollback()
            raise
