"""Real-store tests are opt-in and confined to throwaway PostgreSQL schemas."""

import uuid

import pytest


def pytest_addoption(parser):
    parser.addoption("--run-integration", action="store_true", default=False)


def pytest_configure(config):
    config.addinivalue_line("markers", "integration: isolated PostgreSQL/Chroma/model test")


def pytest_collection_modifyitems(config, items):
    if not config.getoption("--run-integration"):
        skip = pytest.mark.skip(reason="Pass --run-integration to use isolated real stores")
        for item in items:
            if "integration" in item.keywords:
                item.add_marker(skip)


@pytest.fixture(scope="session")
def database_url():
    from backend.database.connection import build_database_url
    return build_database_url()


@pytest.fixture
def db_factory(database_url):
    from sqlalchemy import text
    from sqlalchemy.orm import sessionmaker
    from backend.database.connection import make_engine
    from backend.database.models import Base, DocumentCategory

    schema = "codex_ingest_test_" + uuid.uuid4().hex
    admin = make_engine(database_url)
    test_engine = None
    try:
        with admin.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        test_engine = make_engine(database_url, schema=schema)
        Base.metadata.create_all(test_engine)
        factory = sessionmaker(bind=test_engine, expire_on_commit=False)
        with factory() as session:
            session.add(DocumentCategory(category_name="IT Support"))
            session.add(DocumentCategory(category_name="HR / Leave"))
            session.commit()
        yield factory
    finally:
        if test_engine is not None:
            test_engine.dispose()
        with admin.begin() as connection:
            # This name is generated above and never derived from user input.
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        admin.dispose()


@pytest.fixture(scope="session")
def real_embedder():
    from backend.services.embedding_service import EmbeddingService
    return EmbeddingService(local_files_only=True)


@pytest.fixture
def real_retriever(tmp_path):
    from backend.services.retrieval_service import RetrievalService
    return RetrievalService(persist_path=tmp_path / "chroma", collection_name="test_ingestion_v1")
