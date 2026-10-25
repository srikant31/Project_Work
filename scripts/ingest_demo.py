"""Run the sample corpus CLI.

    python -m scripts.ingest_demo                  # extraction/chunk preview only
    python -m scripts.ingest_demo --write          # ingest into configured stores

Failure injection belongs in the isolated integration tests, never a default
database demonstration. This command does not reset stores.
"""

import argparse
from pathlib import Path

from scripts.ingest_folder import main as ingest_main


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="Ingest using the configured database and vector collection")
    parser.add_argument("--folder", type=Path, default=Path("data/documents"))
    parser.add_argument("--category-name", default="IT Support")
    args = parser.parse_args(argv)
    forwarded = ["--folder", str(args.folder), "--category-name", args.category_name]
    if not args.write:
        forwarded.append("--dry-run")
    return ingest_main(forwarded)


if __name__ == "__main__":
    raise SystemExit(main())
