"""Read-only own-profile serialization; never returns credentials or token digests."""

from datetime import UTC

from backend.repositories.profile_repository import ProfileRepository
from backend.utils.security import hash_session_token


class ProfileService:
    def __init__(self, session_factory):
        self.session_factory = session_factory

    def profile(self, actor, raw_token):
        with self.session_factory() as session:
            repository = ProfileRepository(session)
            try:
                digest = hash_session_token(raw_token)
            except ValueError:
                row = None
            else:
                row = repository.current_session(actor.user_id, digest)
            expiry = None
            if row is not None:
                stamp = row.expires_at
                expiry = (stamp.replace(tzinfo=UTC) if stamp.tzinfo is None else
                          stamp.astimezone(UTC)).isoformat().replace("+00:00", "Z")
            return {"user": {"user_id": str(actor.user_id), "email": actor.email,
                             "role": actor.role, "department": actor.department},
                    "activity": repository.activity(actor.user_id),
                    "session": {"expires_at": expiry, "mechanism": "opaque_cookie", "duration_hours": 8}}
