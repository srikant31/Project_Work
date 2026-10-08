"""Health check: are Postgres and Chroma actually in sync?

    python -m scripts.verify_stores

Run this after every ingest until you trust the pipeline. It catches the exact
failure classes the old code could produce silently.
"""

from sqlalchemy import text

from backend.database.connection import SessionLocal
from backend.services.retrieval_service import RetrievalService

CHECKS = []


def check(name):
    def deco(fn):
        CHECKS.append((name, fn))
        return fn

    return deco


@check("No chunks with NULL embedding_vector_id")
def c1(session, retriever):
    n = session.execute(
        text("SELECT count(*) FROM documentchunks WHERE embedding_vector_id IS NULL")
    ).scalar()
    return n == 0, f"{n} chunks never got a vector id"


@check("No chunks with NULL page_number")
def c2(session, retriever):
    n = session.execute(
        text("SELECT count(*) FROM documentchunks WHERE page_number IS NULL")
    ).scalar()
    return n == 0, f"{n} chunks unusable for page-level eval grading"


@check("Every active Postgres chunk has a Chroma vector")
def c3(session, retriever):
    pg = {
        str(r[0])
        for r in session.execute(
            text("SELECT chunk_id FROM documentchunks WHERE is_active = true")
        )
    }
    ch = set(retriever.collection.get().get("ids") or [])
    missing = pg - ch
    return not missing, f"{len(missing)} orphaned PG rows (unretrievable)"


@check("Every Chroma vector has a Postgres chunk")
def c4(session, retriever):
    pg = {
        str(r[0]) for r in session.execute(text("SELECT chunk_id FROM documentchunks"))
    }
    ch = set(retriever.collection.get().get("ids") or [])
    ghosts = ch - pg
    return not ghosts, f"{len(ghosts)} ghost vectors (retrieve to nothing)"


@check("No duplicate chunk_index within a document")
def c5(session, retriever):
    n = session.execute(
        text(
            """SELECT count(*) FROM (
                 SELECT document_id, chunk_index FROM documentchunks
                 WHERE is_active = true
                 GROUP BY document_id, chunk_index HAVING count(*) > 1
               ) d"""
        )
    ).scalar()
    return n == 0, f"{n} duplicated indices — sign of a double ingest"


@check("No document has two active versions")
def c6(session, retriever):
    n = session.execute(
        text(
            """SELECT count(*) FROM (
                 SELECT doc_name FROM documents WHERE status = 'active'
                 GROUP BY doc_name HAVING count(*) > 1
               ) d"""
        )
    ).scalar()
    return n == 0, f"{n} docs with multiple active versions — stale text in results"


@check("No ingestion jobs stuck in 'processing'")
def c7(session, retriever):
    n = session.execute(
        text("SELECT count(*) FROM ingestionjobs WHERE status = 'processing'")
    ).scalar()
    return n == 0, f"{n} jobs never reached completed/failed"


def main():
    session = SessionLocal()
    retriever = RetrievalService()
    failed = 0
    try:
        print(f"Chroma collection : {retriever.collection_name}")
        print(f"Vectors           : {retriever.count()}\n")
        for name, fn in CHECKS:
            try:
                ok, detail = fn(session, retriever)
            except Exception as e:
                ok, detail = False, f"check errored: {e}"
            print(f"[{'PASS' if ok else 'FAIL'}] {name}")
            if not ok:
                print(f"       -> {detail}")
                failed += 1
        print(f"\n{len(CHECKS) - failed}/{len(CHECKS)} passed")
    finally:
        session.close()
    raise SystemExit(1 if failed else 0)


if __name__ == "__main__":
    main()
