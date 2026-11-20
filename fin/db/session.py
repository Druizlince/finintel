"""Engine and session helpers for the SQLite database."""

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from fin.config import get_settings
from fin.db.models import Base

_engine: Engine | None = None


def make_engine(url: str) -> Engine:
    in_memory = url.endswith(":memory:")
    if in_memory:
        # One shared connection, usable from any thread (web server worker threads in tests);
        # otherwise each thread would get its own empty in-memory database.
        engine = create_engine(
            url, poolclass=StaticPool, connect_args={"check_same_thread": False}
        )
    else:
        engine = create_engine(url)

    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_conn, _record):  # noqa: ANN001
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")  # SQLite ignores foreign keys unless told
        if not in_memory:
            cur.execute("PRAGMA journal_mode=WAL")  # safe concurrent reads during sync
        cur.close()

    return engine


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        settings = get_settings()
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        _engine = make_engine(f"sqlite:///{settings.db_path.as_posix()}")
    return _engine


def init_db(engine: Engine | None = None) -> list[str]:
    """Create any missing tables. Returns the table names."""
    engine = engine or get_engine()
    Base.metadata.create_all(engine)
    add_missing_columns(engine)
    return sorted(Base.metadata.tables)


def add_missing_columns(engine: Engine) -> list[str]:
    """Tiny additive migration: add new NULLABLE columns to existing tables.
    (create_all creates missing tables but never alters existing ones.)"""
    insp = inspect(engine)
    added: list[str] = []
    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            if not insp.has_table(table.name):
                continue
            existing = {c["name"] for c in insp.get_columns(table.name)}
            for col in table.columns:
                if col.name in existing:
                    continue
                if col.primary_key or not col.nullable:
                    raise RuntimeError(
                        f"Cannot auto-add required column {table.name}.{col.name}; needs a migration"
                    )
                coltype = col.type.compile(dialect=engine.dialect)
                conn.execute(text(f'ALTER TABLE "{table.name}" ADD COLUMN "{col.name}" {coltype}'))
                added.append(f"{table.name}.{col.name}")
    return added


@contextmanager
def session_scope(engine: Engine | None = None) -> Iterator[Session]:
    factory = sessionmaker(bind=engine or get_engine(), expire_on_commit=False)
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
