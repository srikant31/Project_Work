"""Wipe ingested data. Run from project root.

    python -m scripts.reset_stores --dry-run      # show counts, change nothing
    python -m scripts.reset_stores --doc-id <uuid>  # remove ONE document
    python -m scripts.reset_stores --all          # wipe everything (asks first)
    python -m scripts.reset_stores --all --yes    # wipe, no prompt (CI only)

Deletes in FK-safe order and keeps Postgres and Chroma in sync — deleting from
only one store is the single easiest way to corrupt a RAG system, because
retrieval keeps returning IDs that no longer resolve to any row.
"""

import argparse
import sys

from sqlalchemy import text

from backend.database.connection import SessionLocal
from backend.services.retrieval_service import RetrievalService

# Child-to-parent order. Anything referencing a row must be cleared before it.
DELETE_ORDER = [
    "retrievedcontext",
    "feedback",
    "evaluationresults",
    "messages",
    "conversations",
    "evaluationquestions",
    "evaluationtestruns",
    "auditlogs",
    "ingestionjobs",
    "documentchunks",
    "documents",
]

# Never auto-wiped: losing these means re-seeding accounts and taxonomy.
PROTECTED = ["users", "documentcategories"]


def counts(session) -> dict:
    out = {}
    for t in DELETE_ORDER + PROTECTED:
        try:
            out[t] = session.execute(text(f"SELECT count(*) FROM {t}")).scalar()
        except Exception:
            session.rollback()
            out[t] = "—(table missing)"
    return out


def show(session, retriever, label):
    print(f"\n=== {label} ===")
    for t, c in counts(session).items():
        flag = "  [protected]" if t in PROTECTED else ""
        print(f"  {t:<24} {c}{flag}")
    print(f"  {'chroma vectors':<24} {retriever.count()}")


def wipe_all(session, retriever):
    for t in DELETE_ORDER:
        try:
            # SAVEPOINT, not the whole transaction. A failed DELETE (e.g. a
            # table that doesn't exist) now only undoes itself — earlier
            # successful deletes in this loop are NOT rolled back with it.
            with session.begin_nested():
                n = session.execute(text(f"DELETE FROM {t}")).rowcount
            print(f"  deleted {n:>6} from {t}")
        except Exception as e:
            print(f"  skipped {t}: {str(e).splitlines()[0][:70]}")
    session.commit()

    # Goes through RetrievalService.recreate_collection() rather than
    # rebuilding the collection inline — that method is the one place that
    # knows the correct config (cosine space). Duplicating the
    # get_or_create_collection() call here is what dropped that config
    # silently last time.
    retriever.recreate_collection()
    print(f"  recreated chroma collection '{retriever.collection_name}'")


def wipe_one(session, retriever, doc_id):
    session.execute(
        text(
            """DELETE FROM retrievedcontext WHERE chunk_id IN
               (SELECT chunk_id FROM documentchunks WHERE document_id = :d)"""
        ),
        {"d": doc_id},
    )
    for stmt in (
        "DELETE FROM documentchunks WHERE document_id = :d",
        "DELETE FROM ingestionjobs WHERE document_id = :d",
        "DELETE FROM documents WHERE doc_id = :d",
    ):
        n = session.execute(text(stmt), {"d": doc_id}).rowcount
        print(f"  {stmt.split()[2]:<20} {n} rows")
    session.commit()
    retriever.delete_by_doc(str(doc_id))
    print(f"  cleared chroma vectors for doc {doc_id}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--doc-id")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--yes", action="store_true")
    args = ap.parse_args()

    session = SessionLocal()
    retriever = RetrievalService()
    try:
        show(session, retriever, "BEFORE")

        if args.dry_run or not (args.all or args.doc_id):
            print("\nNothing changed. Pass --all or --doc-id <uuid> to delete.")
            return

        if args.all and not args.yes:
            if input("\nType DELETE to wipe all ingested data: ").strip() != "DELETE":
                print("Aborted.")
                sys.exit(1)

        print()
        if args.all:
            wipe_all(session, retriever)
        else:
            wipe_one(session, retriever, args.doc_id)

        show(session, retriever, "AFTER")
    finally:
        session.close()


if __name__ == "__main__":
    main()