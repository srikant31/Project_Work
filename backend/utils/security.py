"""Password and opaque-session primitives; never log their inputs or outputs."""

from functools import lru_cache
import hashlib
import re
import secrets

from argon2 import PasswordHasher, extract_parameters
from argon2.exceptions import InvalidHashError, VerificationError
from argon2.low_level import Type
from argon2.profiles import RFC_9106_LOW_MEMORY
import bcrypt


# Explicit, stable policy: Argon2id v19, 64 MiB, 3 passes, 4 lanes.
# bcrypt is retained only to verify existing accounts during login migration.
_PASSWORD_HASHER = PasswordHasher.from_parameters(RFC_9106_LOW_MEMORY)
_BCRYPT_PREFIXES = ("$2a$", "$2b$", "$2y$")


def normalize_email(email: str) -> str:
    if not isinstance(email, str):
        raise ValueError("A valid email address is required")
    normalized = email.strip().lower()
    if len(normalized) > 255 or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", normalized):
        raise ValueError("A valid email address is required")
    return normalized


def _password_bytes(password: str, *, creating: bool) -> bytes:
    if not isinstance(password, str):
        raise ValueError("Password must be text")
    try:
        encoded = password.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValueError("Password must be valid Unicode") from exc
    if creating and len(password) < 10:
        raise ValueError("Password must contain at least 10 characters")
    if len(password) > 256:
        raise ValueError("Password must contain at most 256 characters")
    return encoded


def hash_password(password: str, *, enforce_policy: bool = True) -> str:
    """Create Argon2id; only login migration may bypass the new-account minimum."""
    return _PASSWORD_HASHER.hash(_password_bytes(password, creating=enforce_policy))


def is_argon2id_hash(stored_hash: str | None) -> bool:
    if not isinstance(stored_hash, str) or not stored_hash.startswith("$argon2id$") or len(stored_hash) > 255:
        return False
    try:
        parameters = extract_parameters(stored_hash)
        return parameters.type == Type.ID and parameters.version == 19
    except (InvalidHashError, TypeError, ValueError):
        return False


def password_needs_rehash(stored_hash: str | None) -> bool:
    """Call only after successful verification; upgrade bcrypt/older Argon2 policy."""
    if not is_argon2id_hash(stored_hash):
        return True
    try:
        return _PASSWORD_HASHER.check_needs_rehash(stored_hash)
    except (InvalidHashError, TypeError, ValueError):
        return True


@lru_cache(maxsize=1)
def _dummy_hash() -> str:
    # Random, unknown, per-process material equalizes the expensive password
    # check for unknown accounts. This is not a usable default user password.
    return _PASSWORD_HASHER.hash(secrets.token_bytes(32))


def _dummy_verify(encoded: bytes) -> bool:
    try:
        _PASSWORD_HASHER.verify(_dummy_hash(), encoded)
    except (VerificationError, InvalidHashError):
        pass
    return False


def verify_password(password: str, stored_hash: str | None) -> bool:
    try:
        encoded = _password_bytes(password, creating=False)
    except ValueError:
        return False
    try:
        if not isinstance(stored_hash, str):
            return _dummy_verify(encoded)
        if stored_hash.startswith(("$argon2id$", "$argon2i$", "$argon2d$")):
            return _PASSWORD_HASHER.verify(stored_hash, encoded)
        if stored_hash.startswith(_BCRYPT_PREFIXES):
            # Never silently truncate a legacy bcrypt password to 72 bytes.
            if len(encoded) > 72:
                return False
            return bcrypt.checkpw(encoded, stored_hash.encode("ascii"))
        return _dummy_verify(encoded)
    except VerificationError:
        return False
    except (InvalidHashError, TypeError, ValueError, UnicodeEncodeError):
        # Preserve generic authentication failure for unusable legacy hashes.
        return _dummy_verify(encoded)


def new_session_token() -> str:
    return secrets.token_urlsafe(32)


def hash_session_token(raw_token: str) -> str:
    if not isinstance(raw_token, str) or not 32 <= len(raw_token) <= 256:
        raise ValueError("Invalid session token")
    if not re.fullmatch(r"[A-Za-z0-9_-]+", raw_token):
        raise ValueError("Invalid session token")
    return hashlib.sha256(raw_token.encode("ascii")).hexdigest()
