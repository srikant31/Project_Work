"""Replaying setup must not restore labels removed by category management."""

from pathlib import Path

import pytest

pytestmark = pytest.mark.integration
SQL = (Path(__file__).resolve().parents[1] / "backend/database/migrations/002_mandatory_document_category.sql").read_text(encoding="utf-8")


def apply_seed(factory):
    with factory.kw["bind"].connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        connection.exec_driver_sql(SQL)


def test_rerun_preserves_renamed_and_removed_starter_categories(db_factory):
    from sqlalchemy import text
    from backend.database.models import DocumentCategory

    with db_factory() as session:
        assert session.scalar(text("SELECT current_schema()")).startswith("codex_ingest_test_")
        session.query(DocumentCategory).filter_by(category_name="IT Support").delete()
        row = session.query(DocumentCategory).filter_by(category_name="HR / Leave").one()
        row.category_name = "People policies"
        retained_id = row.category_id
        session.commit()
    apply_seed(db_factory)
    apply_seed(db_factory)
    with db_factory() as session:
        rows = session.query(DocumentCategory).all()
        assert len(rows) == 1 and rows[0].category_name == "People policies"
        assert rows[0].category_id == retained_id


def test_empty_initial_taxonomy_is_seeded_once(db_factory):
    from sqlalchemy import text
    from backend.database.models import DocumentCategory

    with db_factory() as session:
        assert session.scalar(text("SELECT current_schema()")).startswith("codex_ingest_test_")
        session.query(DocumentCategory).delete()
        session.commit()
    apply_seed(db_factory)
    with db_factory() as session:
        original = {row.category_name: row.category_id for row in session.query(DocumentCategory)}
        assert len(original) == 5 and "Uncategorized" in original
    apply_seed(db_factory)
    with db_factory() as session:
        assert {row.category_name: row.category_id for row in session.query(DocumentCategory)} == original


def test_legacy_null_documents_get_fallback_without_recreating_starters(db_factory):
    from sqlalchemy import text
    from backend.database.models import Document, DocumentCategory

    engine = db_factory.kw["bind"]
    with engine.begin() as connection:
        connection.exec_driver_sql("ALTER TABLE documents ALTER COLUMN category_id DROP NOT NULL")
    with db_factory() as session:
        row = session.query(DocumentCategory).filter_by(category_name="IT Support").one()
        row.category_name = "Corporate support"
        session.query(DocumentCategory).filter_by(category_name="HR / Leave").delete()
        document = Document(doc_name="Legacy fixture", doc_type="txt", doc_path="fixture.txt",
                            category_id=None, status="failed")
        session.add(document)
        session.commit()
        document_id = document.doc_id
    apply_seed(db_factory)
    with db_factory() as session:
        names = {row.category_name: row.category_id for row in session.query(DocumentCategory)}
        assert set(names) == {"Corporate support", "Uncategorized"}
        assert session.get(Document, document_id).category_id == names["Uncategorized"]


def test_admin_cleared_taxonomy_is_not_mistaken_for_first_install(db_factory):
    from backend.database.models import DocumentCategory, User
    from backend.services.category_service import CategoryService

    with db_factory() as session:
        actor = User(email="taxonomy-admin@example.test", role="admin")
        session.add(actor)
        session.commit()
        ids = [row.category_id for row in session.query(DocumentCategory)]
    for category_id in ids:
        CategoryService(db_factory).delete(category_id, actor)
    apply_seed(db_factory)
    apply_seed(db_factory)
    with db_factory() as session:
        assert session.query(DocumentCategory).count() == 0


def test_legacy_fallback_reuses_case_insensitive_existing_category(db_factory):
    from backend.database.models import Document, DocumentCategory

    with db_factory.kw["bind"].begin() as connection:
        connection.exec_driver_sql("ALTER TABLE documents ALTER COLUMN category_id DROP NOT NULL")
    with db_factory() as session:
        fallback = DocumentCategory(category_name="uncategorized")
        session.add(fallback)
        session.flush()
        fallback_id = fallback.category_id
        document = Document(doc_name="Legacy fixture", doc_type="txt", doc_path="fixture.txt",
                            category_id=None, status="failed")
        session.add(document)
        session.commit()
        document_id = document.doc_id
    apply_seed(db_factory)
    with db_factory() as session:
        assert session.get(Document, document_id).category_id == fallback_id
        assert session.query(DocumentCategory).filter(
            DocumentCategory.category_name.in_(["Uncategorized", "uncategorized"])).count() == 1
