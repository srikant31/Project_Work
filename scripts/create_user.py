"""Create one local account without a default password or reset behaviour.

Interactive:
    python -m scripts.create_user --email admin@example.test --role admin
Generated credentials (never printed):
    python -m scripts.create_user --email admin@example.test --role admin \
        --generate-password --credentials-file output/local_admin_credentials.json
"""

import argparse
from getpass import getpass
import json
import os
from pathlib import Path
import secrets
import subprocess

from backend.database.connection import SessionLocal
from backend.repositories.user_repository import UserRepository
from backend.utils.security import hash_password, normalize_email


def _private_output_path(value) -> Path:
    workspace = Path(__file__).resolve().parents[1]
    path = Path(value).expanduser()
    path = (path if path.is_absolute() else workspace / path).resolve()
    if not path.is_relative_to(workspace):
        raise ValueError("Credentials file must be inside this workspace's ignored output directory")
    relative = path.relative_to(workspace)
    ignored = path.is_relative_to(workspace / "output")
    if not ignored:
        try:
            ignored = subprocess.run(
                ["git", "check-ignore", "--quiet", "--", str(relative)],
                cwd=workspace, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                check=False,
            ).returncode == 0
        except OSError:
            ignored = False
    if not ignored:
        raise ValueError("Credentials file must be git-ignored or beneath output/")
    if path.exists():
        raise ValueError("Credentials file already exists; it will not be overwritten")
    return path


def _write_credentials(path, email, role, password):
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as target:
        json.dump({"email": email, "role": role, "password": password}, target, indent=2)
        target.write("\n")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--email", required=True)
    parser.add_argument("--role", choices=("admin", "employee"), required=True)
    parser.add_argument("--department")
    parser.add_argument("--generate-password", action="store_true")
    parser.add_argument("--credentials-file", type=Path)
    args = parser.parse_args(argv)
    if args.generate_password != (args.credentials_file is not None):
        parser.error("--generate-password and --credentials-file must be supplied together")
    try:
        email = normalize_email(args.email)
        credentials = _private_output_path(args.credentials_file) if args.generate_password else None
        if args.generate_password:
            password = secrets.token_urlsafe(24)
        else:
            password = getpass("New password (at least 10 characters): ")
            if password != getpass("Confirm password: "):
                raise ValueError("Passwords do not match")
        password_hash = hash_password(password)
        with SessionLocal() as session:
            UserRepository(session).create(email, password_hash, args.role, args.department)
            # Write before commit so a successful account always has its generated
            # credentials available. An ambiguous commit retains the private file.
            if credentials is not None:
                _write_credentials(credentials, email, args.role, password)
            session.commit()
        print(f"Created {args.role} account: {email}")
        if credentials is not None:
            print(f"Private credentials saved to: {credentials}")
        return 0
    except ValueError as exc:
        print(str(exc))
        return 1
    except Exception:
        # Driver tracebacks/connection representations can disclose credentials.
        print("Account creation failed. Verify database access and existing accounts. Any private credentials file was retained.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
