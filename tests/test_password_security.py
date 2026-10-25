"""Argon2id password migration and unchanged opaque-session guarantees.

All password checks use real cryptography at production settings. Database
cases are opt-in and use the existing throwaway PostgreSQL schema fixture.
No credentials or password hashes are logged by these tests.
"""

import pytest


PASSWORD = "Migration-validation-2026!"


@pytest.fixture(scope="module")
def current_hash():
    from backend.utils.security import hash_password

    return hash_password(PASSWORD)


@pytest.fixture(scope="module")
def legacy_hash():
    import bcrypt

    return bcrypt.hashpw(PASSWORD.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("ascii")


@pytest.fixture(scope="module")
def outdated_hash():
    from argon2 import PasswordHasher

    # A real, different parameter profile, without lowering the memory or CPU
    # cost just to make the test run faster.
    return PasswordHasher(time_cost=4, memory_cost=65536, parallelism=4).hash(PASSWORD)


def test_new_hashes_are_salted_argon2id_at_the_configured_profile(current_hash):
    from argon2 import Type, extract_parameters
    from backend.utils.security import hash_password, password_needs_rehash, verify_password

    parameters = extract_parameters(current_hash)
    assert parameters.type is Type.ID
    assert parameters.version == 19
    assert (parameters.memory_cost, parameters.time_cost, parameters.parallelism) == (65536, 3, 4)
    assert hash_password(PASSWORD) != current_hash
    assert verify_password(PASSWORD, current_hash)
    assert not verify_password("A-different-password!", current_hash)
    assert not password_needs_rehash(current_hash)


def test_unicode_passwords_over_the_old_bcrypt_byte_limit_do_not_alias():
    from backend.utils.security import hash_password, verify_password

    first = "\u5bc6\u78bc" * 20 + "A"
    second = "\u5bc6\u78bc" * 20 + "B"
    assert len(first.encode("utf-8")) > 72
    stored = hash_password(first)
    assert verify_password(first, stored)
    assert not verify_password(second, stored)


def test_maximum_256_unicode_characters_are_supported():
    from backend.utils.security import hash_password, verify_password

    password = "\U0001f511" * 256
    assert len(password.encode("utf-8")) > 256
    assert verify_password(password, hash_password(password))


@pytest.mark.parametrize("password", ["short", "a" * 257, "\U0001f511" * 257, None, 123, "invalid\ud800unicode"])
def test_creation_rejects_invalid_passwords(password):
    from backend.utils.security import hash_password

    with pytest.raises(ValueError):
        hash_password(password)


@pytest.mark.parametrize("stored_hash", [None, "", "plaintext-password", "$argon2id$broken", "$2b$12$invalid"])
def test_missing_or_malformed_hashes_fail_authentication(stored_hash):
    from backend.utils.security import verify_password

    assert verify_password(PASSWORD, stored_hash) is False


@pytest.mark.parametrize("password", [None, 123, "invalid\ud800unicode", "a" * 257])
def test_invalid_verification_inputs_return_false(password, current_hash):
    from backend.utils.security import verify_password

    assert verify_password(password, current_hash) is False


def test_legacy_bcrypt_remains_verifiable_and_requires_rehash(legacy_hash):
    from backend.utils.security import password_needs_rehash, verify_password

    assert verify_password(PASSWORD, legacy_hash)
    assert not verify_password("Incorrect-legacy-password!", legacy_hash)
    assert password_needs_rehash(legacy_hash)


def test_legacy_bcrypt_rejects_any_password_past_72_utf8_bytes():
    import bcrypt
    from backend.utils.security import verify_password

    boundary_password = "\u5bc6" * 24
    assert len(boundary_password.encode("utf-8")) == 72
    stored = bcrypt.hashpw(boundary_password.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("ascii")
    assert verify_password(boundary_password, stored)
    assert not verify_password(boundary_password + "extra", stored)


def test_outdated_argon2_parameters_are_verified_and_flagged(outdated_hash):
    from backend.utils.security import password_needs_rehash, verify_password

    assert verify_password(PASSWORD, outdated_hash)
    assert password_needs_rehash(outdated_hash)


def _seed_user(factory, password_hash, email="migration.validation@example.test"):
    from backend.database.models import User

    # Legacy rows are deliberately seeded through the ORM: the public account
    # creation repository must no longer accept bcrypt for new accounts.
    with factory() as session:
        user = User(email=email, role="employee", password_hash=password_hash)
        session.add(user)
        session.flush()
        user_id = user.user_id
        session.commit()
    return user_id


@pytest.mark.integration
class TestPasswordMigration:
    def test_new_user_repository_accepts_argon2id(self, db_factory, current_hash):
        from backend.repositories.user_repository import UserRepository

        with db_factory() as session:
            user = UserRepository(session).create("new.validation@example.test", current_hash, "employee")
            session.commit()
            assert user.password_hash == current_hash
            assert user.email == "new.validation@example.test"

    @pytest.mark.parametrize("invalid_kind", ["plaintext", "bcrypt", "malformed_argon2id"])
    def test_new_user_repository_rejects_unusable_or_legacy_hashes(self, db_factory, legacy_hash, invalid_kind):
        from backend.database.models import User
        from backend.repositories.user_repository import UserRepository

        value = {"plaintext": PASSWORD, "bcrypt": legacy_hash, "malformed_argon2id": "$argon2id$broken"}[invalid_kind]
        with db_factory() as session:
            with pytest.raises(ValueError):
                UserRepository(session).create("new.validation@example.test", value, "employee")
            assert session.query(User).count() == 0

    def test_successful_legacy_login_upgrades_hash_and_keeps_session_revocable(self, db_factory, legacy_hash):
        from backend.database.models import AuthSession, User
        from backend.services.auth_service import AuthService
        from backend.utils.security import hash_session_token, password_needs_rehash, verify_password

        user_id = _seed_user(db_factory, legacy_hash)
        with AuthService(session_factory=db_factory) as auth:
            user, token = auth.login("migration.validation@example.test", PASSWORD)
            assert user.user_id == user_id
            assert auth.current_user(token).user_id == user_id
            with db_factory() as session:
                stored = session.get(User, user_id).password_hash
                assert stored.startswith("$argon2id$")
                assert verify_password(PASSWORD, stored)
                assert not password_needs_rehash(stored)
                session_record = session.query(AuthSession).one()
                assert session_record.token_hash == hash_session_token(token)
                assert session_record.token_hash != token
            assert auth.logout(token)
            assert auth.current_user(token) is None
            assert not auth.logout(token)

    @pytest.mark.parametrize("hash_kind", ["bcrypt", "outdated_argon2id"])
    def test_wrong_password_never_upgrades_hash_or_creates_session(self, db_factory, legacy_hash, outdated_hash, hash_kind):
        from backend.database.models import AuthSession, User
        from backend.services.auth_service import AuthService, InvalidCredentialsError

        original_hash = legacy_hash if hash_kind == "bcrypt" else outdated_hash
        user_id = _seed_user(db_factory, original_hash)
        with AuthService(session_factory=db_factory) as auth:
            with pytest.raises(InvalidCredentialsError):
                auth.login("migration.validation@example.test", "Incorrect-migration-password!")
        with db_factory() as session:
            assert session.get(User, user_id).password_hash == original_hash
            assert session.query(AuthSession).count() == 0

    def test_successful_current_argon2id_login_keeps_hash_unchanged(self, db_factory, current_hash):
        from backend.database.models import User
        from backend.services.auth_service import AuthService

        user_id = _seed_user(db_factory, current_hash)
        with AuthService(session_factory=db_factory) as auth:
            auth.login("migration.validation@example.test", PASSWORD)
        with db_factory() as session:
            assert session.get(User, user_id).password_hash == current_hash

    def test_successful_outdated_argon2id_login_rehashes_at_current_parameters(self, db_factory, outdated_hash):
        from backend.database.models import User
        from backend.services.auth_service import AuthService
        from backend.utils.security import password_needs_rehash, verify_password

        user_id = _seed_user(db_factory, outdated_hash)
        with AuthService(session_factory=db_factory) as auth:
            auth.login("migration.validation@example.test", PASSWORD)
        with db_factory() as session:
            upgraded = session.get(User, user_id).password_hash
            assert upgraded != outdated_hash
            assert verify_password(PASSWORD, upgraded)
            assert not password_needs_rehash(upgraded)

    def test_legacy_password_truncation_does_not_authenticate_or_upgrade(self, db_factory):
        import bcrypt
        from backend.database.models import AuthSession, User
        from backend.services.auth_service import AuthService, InvalidCredentialsError

        boundary_password = "a" * 72
        stored = bcrypt.hashpw(boundary_password.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("ascii")
        user_id = _seed_user(db_factory, stored)
        with AuthService(session_factory=db_factory) as auth:
            with pytest.raises(InvalidCredentialsError):
                auth.login("migration.validation@example.test", boundary_password + "suffix")
        with db_factory() as session:
            assert session.get(User, user_id).password_hash == stored
            assert session.query(AuthSession).count() == 0

    def test_failed_session_creation_rolls_back_password_upgrade(self, db_factory, legacy_hash, monkeypatch):
        from backend.database.models import AuthSession, User
        from backend.repositories.auth_session_repository import AuthSessionRepository
        from backend.services.auth_service import AuthService

        user_id = _seed_user(db_factory, legacy_hash)

        def fail_session_creation(*args, **kwargs):
            raise RuntimeError("Simulated session persistence failure")

        monkeypatch.setattr(AuthSessionRepository, "create", fail_session_creation)
        with AuthService(session_factory=db_factory) as auth:
            with pytest.raises(RuntimeError, match="Simulated session persistence failure"):
                auth.login("migration.validation@example.test", PASSWORD)
        with db_factory() as session:
            assert session.get(User, user_id).password_hash == legacy_hash
            assert session.query(AuthSession).count() == 0

    def test_valid_short_legacy_password_upgrades_without_new_account_lockout(self, db_factory):
        import bcrypt
        from backend.database.models import User
        from backend.services.auth_service import AuthService
        from backend.utils.security import password_needs_rehash, verify_password

        old_password = "Old-7!"
        stored = bcrypt.hashpw(old_password.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("ascii")
        user_id = _seed_user(db_factory, stored)
        with AuthService(session_factory=db_factory) as auth:
            user, token = auth.login("migration.validation@example.test", old_password)
            assert user.user_id == user_id
            assert auth.current_user(token).user_id == user_id
        with db_factory() as session:
            upgraded = session.get(User, user_id).password_hash
            assert upgraded.startswith("$argon2id$")
            assert verify_password(old_password, upgraded)
            assert not password_needs_rehash(upgraded)
        # The migrated account must remain usable on subsequent logins too.
        with AuthService(session_factory=db_factory) as auth:
            user, _ = auth.login("migration.validation@example.test", old_password)
            assert user.user_id == user_id

    def test_repository_cas_cannot_overwrite_a_concurrently_replaced_password(self, db_factory, legacy_hash, current_hash, outdated_hash):
        from backend.database.models import User
        from backend.repositories.user_repository import UserRepository

        user_id = _seed_user(db_factory, legacy_hash)
        with db_factory() as stale_session:
            repository = UserRepository(stale_session)
            previous_hash = repository.get_by_id(user_id).password_hash
            with db_factory() as concurrent_session:
                concurrent_session.get(User, user_id).password_hash = current_hash
                concurrent_session.commit()
            assert repository.upgrade_password_hash(user_id, previous_hash, outdated_hash) is False
            stale_session.commit()
        with db_factory() as session:
            assert session.get(User, user_id).password_hash == current_hash

    def test_login_rejects_concurrent_password_reset_without_creating_session(self, db_factory, legacy_hash, monkeypatch):
        from backend.database.models import AuthSession, User
        import backend.services.auth_service as auth_module
        from backend.utils.security import hash_password

        user_id = _seed_user(db_factory, legacy_hash)
        replacement_hash = hash_password("Concurrent-reset-password-2026!")
        original_verify = auth_module.verify_password

        def verify_then_reset(password, stored_hash):
            valid = original_verify(password, stored_hash)
            if valid:
                with db_factory() as concurrent_session:
                    concurrent_session.get(User, user_id).password_hash = replacement_hash
                    concurrent_session.commit()
            return valid

        monkeypatch.setattr(auth_module, "verify_password", verify_then_reset)
        with auth_module.AuthService(session_factory=db_factory) as auth:
            with pytest.raises(auth_module.InvalidCredentialsError):
                auth.login("migration.validation@example.test", PASSWORD)
        with db_factory() as session:
            assert session.get(User, user_id).password_hash == replacement_hash
            assert session.query(AuthSession).count() == 0
