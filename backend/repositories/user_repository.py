import uuid

from sqlalchemy import func

from backend.database.models import User
from backend.utils.security import is_argon2id_hash, normalize_email


class UserRepository:
    """User persistence. The caller owns commits and password hashing."""

    def __init__(self, session):
        self.session = session

    def get_by_email(self, email: str) -> User | None:
        email = normalize_email(email)
        matches = self.session.query(User).filter(func.lower(User.email) == email).limit(2).all()
        # An ambiguous legacy pair must not authenticate as an arbitrary user.
        return matches[0] if len(matches) == 1 else None

    def get_by_id(self, user_id) -> User | None:
        try:
            return self.session.get(User, uuid.UUID(str(user_id)))
        except (ValueError, TypeError, AttributeError):
            return None

    def create(self, email, password_hash, role, department=None) -> User:
        email = normalize_email(email)
        if role not in ("admin", "employee"):
            raise ValueError("Role must be admin or employee")
        if not is_argon2id_hash(password_hash):
            raise ValueError("An Argon2id password hash is required")
        # Check all case variants, even when legacy duplicates make the single
        # user lookup intentionally return None.
        if self.session.query(User.user_id).filter(func.lower(User.email) == email).first():
            raise ValueError("An account with this email already exists")
        user = User(email=email, password_hash=password_hash, role=role, department=department)
        self.session.add(user)
        self.session.flush()
        return user

    def upgrade_password_hash(self, user_id, previous_hash, replacement_hash) -> bool:
        """Do not overwrite a password changed concurrently after verification."""
        if not is_argon2id_hash(replacement_hash):
            raise ValueError("An Argon2id password hash is required")
        updated = self.session.query(User).filter(
            User.user_id == user_id, User.password_hash == previous_hash,
        ).update({User.password_hash: replacement_hash}, synchronize_session="fetch")
        return updated == 1
