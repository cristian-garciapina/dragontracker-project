"""
Database connection layer.

SQLite file lives at /opt/dashboard/data/eternal_vanguard.db. The data
directory is sibling to the app/ package, not inside it — keeps code
and data clearly separated and makes backups (rsync the data dir)
trivial.
"""
from pathlib import Path
from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker

DB_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "eternal_vanguard.db"
DB_URL = f"sqlite:///{DB_PATH}"

engine = create_engine(
    DB_URL,
    connect_args={"check_same_thread": False},
    echo=False,
)

@event.listens_for(engine, "connect")
def _sqlite_pragma_on_connect(dbapi_connection, connection_record):
    """Enable FK enforcement on every new SQLite connection.

    SQLite disables foreign key checks by default per-connection, so this
    hook must fire on every new pooled connection, not just at startup.
    """
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    """SQLAlchemy 2.0 declarative base for all ORM models."""
    pass


def get_session():
    """FastAPI dependency: yields a DB session and guarantees it closes."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
