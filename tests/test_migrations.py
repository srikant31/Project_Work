"""Exercise migration 003 against legacy tables in an isolated real schema.

The shared --run-integration opt-in gate applies. No public table is altered.
"""

from pathlib import Path
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError

from backend.database.connection import make_engine


pytestmark = pytest.mark.integration
MIGRATION = (
    Path(__file__).resolve().parents[1]
    / "backend" / "database" / "migrations" / "003_safe_ingestion.sql"
)


@pytest.fixture
def legacy_engine(database_url):
    schema = "codex_ingest_migration_" + uuid.uuid4().hex
    admin_engine = make_engine(database_url)
    isolated_engine = None
    created = False
    try:
        with admin_engine.begin() as connection:
            connection.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
        created = True
        isolated_engine = make_engine(database_url, schema=schema)
        with isolated_engine.begin() as connection:
            assert connection.scalar(text("SELECT current_schema()")) == schema
            assert connection.scalar(text("SHOW timezone")) == "UTC"
            connection.exec_driver_sql(
                """
                CREATE TABLE documentcategories (
                    category_id UUID PRIMARY KEY,
                    category_name VARCHAR(100) NOT NULL
                );
                CREATE TABLE documents (
                    doc_id UUID PRIMARY KEY,
                    doc_name VARCHAR(255) NOT NULL,
                    category_id UUID REFERENCES documentcategories(category_id),
                    version INT DEFAULT 1,
                    status VARCHAR(20) DEFAULT 'active'
                        CHECK (status IN ('active','deactivated')),
                    doc_path TEXT NOT NULL
                );
                CREATE TABLE documentchunks (
                    chunk_id UUID PRIMARY KEY,
                    document_id UUID NOT NULL REFERENCES documents(doc_id),
                    chunk_text TEXT NOT NULL,
                    chunk_index INT NOT NULL,
                    is_active BOOLEAN DEFAULT true
                );
                """
            )
        yield isolated_engine
    finally:
        if isolated_engine is not None:
            isolated_engine.dispose()
        # The name is generated here, never derived from user input or a URL.
        assert schema.startswith("codex_ingest_migration_")
        if created:
            with admin_engine.begin() as connection:
                connection.exec_driver_sql(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        admin_engine.dispose()


def _apply_migration(engine):
    # The SQL file owns BEGIN/COMMIT. AUTOCOMMIT avoids an enclosing transaction
    # that would obscure whether its own preflight really rolled back changes.
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        try:
            connection.exec_driver_sql(MIGRATION.read_text(encoding="utf-8"))
        except Exception:
            connection.exec_driver_sql("ROLLBACK")
            raise


def _seed_legacy(engine):
    category_id, document_id, historical_id, chunk_id = (uuid.uuid4() for _ in range(4))
    with engine.begin() as connection:
        connection.execute(
            text("INSERT INTO documentcategories VALUES (:id, 'IT Support')"),
            {"id": category_id},
        )
        connection.execute(
            text(
                "INSERT INTO documents (doc_id,doc_name,category_id,version,status,doc_path) "
                "VALUES (:id,'policy.txt',:category,1,:status,'legacy/policy.txt')"
            ),
            [
                {"id": document_id, "category": category_id, "status": "active"},
                {"id": historical_id, "category": category_id, "status": "deactivated"},
            ],
        )
        connection.execute(
            text(
                "INSERT INTO documentchunks (chunk_id,document_id,chunk_text,chunk_index) "
                "VALUES (:id,:document,'Original legacy evidence',0)"
            ),
            {"id": chunk_id, "document": document_id},
        )
    return category_id, document_id, historical_id, chunk_id


def test_safe_ingestion_migration_is_repeatable_and_preserves_legacy(legacy_engine):
    _, document_id, historical_id, chunk_id = _seed_legacy(legacy_engine)
    _apply_migration(legacy_engine)
    _apply_migration(legacy_engine)

    with legacy_engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT doc_id,source_key,source_hash,text_hash,pipeline_fingerprint,"
                "pipeline_config,extracted_text,extraction_warnings,cleanup_pending,status "
                "FROM documents ORDER BY status"
            )
        ).mappings().all()
        assert len(rows) == 2
        assert {row["doc_id"] for row in rows} == {document_id, historical_id}
        assert {row["status"] for row in rows} == {"active", "deactivated"}
        for row in rows:
            for field in (
                "source_key", "source_hash", "text_hash", "pipeline_fingerprint",
                "pipeline_config", "extracted_text", "extraction_warnings",
            ):
                assert row[field] is None
            assert row["cleanup_pending"] is False
        chunk = connection.execute(text("SELECT * FROM documentchunks")).mappings().one()
        assert chunk["chunk_id"] == chunk_id
        assert chunk["chunk_text"] == "Original legacy evidence"
        assert chunk["start_char"] is None and chunk["end_char"] is None

    new_id = uuid.uuid4()
    with legacy_engine.begin() as connection:
        status = connection.scalar(
            text(
                "INSERT INTO documents (doc_id,doc_name,doc_path) "
                "VALUES (:id,'new.txt','new.txt') RETURNING status"
            ),
            {"id": new_id},
        )
        assert status == "staging"
        connection.execute(text("UPDATE documents SET status='failed' WHERE doc_id=:id"), {"id": new_id})


def test_migration_enforces_active_source_version_and_chunk_uniqueness(legacy_engine):
    category_id, document_id, _, _ = _seed_legacy(legacy_engine)
    _apply_migration(legacy_engine)
    with legacy_engine.begin() as connection:
        connection.execute(
            text("UPDATE documents SET source_key='corpus/policy.txt' WHERE doc_id=:id"),
            {"id": document_id},
        )

    def insert_version(version, status):
        with legacy_engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO documents (doc_id,doc_name,doc_path,category_id,"
                    "source_key,version,status) VALUES (:id,'policy.txt','policy.txt',"
                    ":category,'corpus/policy.txt',:version,:status)"
                ),
                {"id": uuid.uuid4(), "category": category_id, "version": version, "status": status},
            )

    with pytest.raises(IntegrityError, match="uq_documents_active_source"):
        insert_version(2, "active")
    insert_version(2, "staging")
    with pytest.raises(IntegrityError, match="uq_documents_source_version"):
        insert_version(2, "failed")
    with pytest.raises(IntegrityError, match="uq_documentchunks_document_index"):
        with legacy_engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO documentchunks (chunk_id,document_id,chunk_text,chunk_index) "
                    "VALUES (:id,:document,'Duplicate index',0)"
                ),
                {"id": uuid.uuid4(), "document": document_id},
            )
    with pytest.raises(IntegrityError, match="documents_status_check"):
        insert_version(3, "invalid")


def test_migration_preflight_rolls_back_without_deleting_duplicates(legacy_engine):
    _, document_id, _, _ = _seed_legacy(legacy_engine)
    with legacy_engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO documentchunks (chunk_id,document_id,chunk_text,chunk_index) "
                "VALUES (:id,:document,'Existing duplicate requiring human review',0)"
            ),
            {"id": uuid.uuid4(), "document": document_id},
        )
    with pytest.raises(DBAPIError, match="duplicate document/chunk_index"):
        _apply_migration(legacy_engine)
    with legacy_engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM documents")) == 2
        assert connection.scalar(text("SELECT count(*) FROM documentchunks")) == 2
        assert connection.scalar(
            text(
                "SELECT count(*) FROM information_schema.columns "
                "WHERE table_schema=current_schema() AND table_name='documents' "
                "AND column_name='source_key'"
            )
        ) == 0
