"""Back up the configured database, then apply additive helpdesk migrations.

Run from the project root: python -m scripts.apply_ingestion_migrations
Existing documents are preserved; legacy provenance remains unknown.
All numbered SQL files are discovered in order, including the admin-review
upgrade. Historical message links and feedback submission times stay unknown.
"""

import argparse
import os
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

from backend.database.connection import build_database_url, make_engine


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backup-dir", default="output/backups")
    args = parser.parse_args()
    executable = shutil.which("pg_dump")
    if executable is None:
        psql = shutil.which("psql")
        candidate = Path(psql).with_name("pg_dump.exe") if psql else None
        executable = str(candidate) if candidate and candidate.exists() else None
    if executable is None:
        parser.error("pg_dump is required for a backup before migration")
    url = build_database_url()
    backup_dir = Path(args.backup_dir)
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup = backup_dir / ("before_helpdesk_migrations_" + datetime.now().strftime("%Y%m%d_%H%M%S") + ".dump")
    environment = os.environ.copy()
    environment["PGPASSWORD"] = url.password or ""
    command = [executable, "--format=custom", "--host", url.host or "localhost",
               "--port", str(url.port or 5432), "--username", url.username,
               "--dbname", url.database, "--file", str(backup)]
    # Never log the subprocess environment or a rendered connection URL.
    completed = subprocess.run(command, env=environment, capture_output=True)
    if completed.returncode:
        raise RuntimeError("Database backup failed; no migration was attempted")
    print(f"Backup saved: {backup}")
    engine = make_engine(url)
    try:
        directory = Path(__file__).resolve().parents[1] / "backend/database/migrations"
        for name in sorted(path.name for path in directory.glob("*.sql")):
            with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
                connection.exec_driver_sql((directory / name).read_text(encoding="utf-8-sig"))
            print(f"Applied: {name}")
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
