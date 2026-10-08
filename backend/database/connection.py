import os
from contextlib import contextmanager

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

load_dotenv()

DATABASE_URL = (
    f"postgresql+psycopg2://{os.getenv('DB_USER')}:{os.getenv('DB_PASSWORD')}"
    f"@{os.getenv('DB_HOST')}:{os.getenv('DB_PORT')}/{os.getenv('DB_NAME')}"
)

engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,   # drops dead connections instead of handing them to you
    pool_size=5,
    max_overflow=10,
    future=True,
)

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
