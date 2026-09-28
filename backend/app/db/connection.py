"""SQL Server connectivity.

Design choice: the DDL in db/ddl/001_schema.sql is the single source of
truth for the schema. We reflect it via SQLAlchemy MetaData rather than
hand-maintaining a parallel set of declarative ORM models — with ~25 tables,
duplicating the schema in two places is a real drift risk for no benefit at
this stage (the loader and analytics layer both just need Table objects to
build INSERT/SELECT against). If a future phase needs ORM relationship
conveniences for a specific table, add a declarative model for that table
only, next to its usage.
"""
from __future__ import annotations

import time

import sqlalchemy as sa
from sqlalchemy.engine import Engine

from app.config import settings


def get_engine(url: str | None = None) -> Engine:
    return sa.create_engine(url or settings.mssql_odbc_url, fast_executemany=True)


def wait_for_sql_server(max_attempts: int = 30, delay_seconds: float = 2.0) -> None:
    """Poll the `master` database until SQL Server accepts connections.

    Used by the loader entrypoint so `docker compose up` doesn't require a
    separate wait-for-it script.
    """
    last_error: Exception | None = None
    engine = get_engine(settings.mssql_odbc_url_master)
    for _ in range(max_attempts):
        try:
            with engine.connect() as conn:
                conn.execute(sa.text("SELECT 1"))
            return
        except Exception as exc:  # noqa: BLE001 - retry loop, re-raised below
            last_error = exc
            time.sleep(delay_seconds)
    raise RuntimeError(f"SQL Server did not become ready in time: {last_error}")


def run_ddl_file(path: str) -> None:
    """Execute a .sql file against `master` (the file itself CREATEs and
    USEs the target database, and contains GO batch separators handled here
    since pyodbc/SQLAlchemy don't understand `GO`)."""
    with open(path, "r", encoding="utf-8") as f:
        script = f.read()

    batches = [b.strip() for b in script.split("\nGO\n") if b.strip()]
    engine = get_engine(settings.mssql_odbc_url_master)
    with engine.connect() as conn:
        conn = conn.execution_options(isolation_level="AUTOCOMMIT")
        for batch in batches:
            conn.execute(sa.text(batch))


def reflect_metadata(engine: Engine | None = None) -> sa.MetaData:
    engine = engine or get_engine()
    metadata = sa.MetaData()
    metadata.reflect(bind=engine)
    return metadata
