import os
from contextlib import contextmanager

from dotenv import load_dotenv
from sqlalchemy import URL, create_engine
from sqlalchemy.orm import sessionmaker

load_dotenv()

def build_database_url() -> URL:
    """Build a URL without interpreting reserved characters in credentials.

    Keep this object private in logs; render_as_string() hides passwords by
    default, unlike interpolating environment values into a URL string.
    """
    return URL.create(
        "postgresql+psycopg2",
        username=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
        host=os.getenv("DB_HOST"),
        port=int(os.getenv("DB_PORT") or 5432),
        database=os.getenv("DB_NAME"),
    )


def make_engine(url=None, *, schema: str | None = None):
    """Create an engine, optionally confined to an isolated PostgreSQL schema.

    Only simple schema identifiers are allowed so the connection option cannot
    smuggle SQL or additional server settings. The schema must already exist.
    Engine creation is lazy and does not contact the database.
    """
    settings = dict(
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=10,
        future=True,
        connect_args={"options": "-ctimezone=UTC"},
    )
    if schema is not None:
        import re

        if not re.fullmatch(r"[a-z_][a-z0-9_]*", schema):
            raise ValueError("schema must be a lowercase SQL identifier")
        settings["connect_args"]["options"] += f" -csearch_path={schema}"
    return create_engine(url if url is not None else build_database_url(), **settings)


DATABASE_URL = build_database_url()
engine = make_engine(DATABASE_URL)

SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, future=True)


def get_session():
    """Raw session. Caller MUST close it. Prefer session_scope() below."""
    return SessionLocal()


@contextmanager
def session_scope():
    """Transactional scope. Commits on clean exit, rolls back on exception,
    always closes. This is what application code should use."""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
