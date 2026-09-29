"""Apply db/migrations/*.sql in name order, once each, recorded in schema_migrations.

Migrations must be rerunnable and non-destructive (create-if-missing,
CREATE OR ALTER). db/ddl/001_schema.sql is the destructive bootstrap and is
not handled here.

Usage: python -m app.db.migrate [--dir db/migrations]
"""
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

import sqlalchemy as sa

from app.db.connection import get_engine

LEDGER = """
IF OBJECT_ID('dbo.schema_migrations', 'U') IS NULL
    CREATE TABLE dbo.schema_migrations (
        name        VARCHAR(200) NOT NULL PRIMARY KEY,
        sha256      CHAR(64) NOT NULL,
        applied_at  DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME()
    );
"""


def split_batches(script: str) -> list[str]:
    batches, current = [], []
    for line in script.splitlines():
        if line.strip().upper() == "GO":
            batches.append("\n".join(current))
            current = []
        else:
            current.append(line)
    batches.append("\n".join(current))
    return [b.strip() for b in batches if b.strip()]


def apply_migrations(directory: str = "db/migrations", engine: sa.Engine | None = None) -> list[str]:
    engine = engine or get_engine()
    applied_now = []
    with engine.connect() as conn:
        conn = conn.execution_options(isolation_level="AUTOCOMMIT")
        conn.execute(sa.text(LEDGER))
        done = {row[0]: row[1] for row in conn.execute(sa.text("SELECT name, sha256 FROM dbo.schema_migrations"))}
        for path in sorted(Path(directory).glob("*.sql")):
            script = path.read_text(encoding="utf-8")
            digest = hashlib.sha256(script.encode("utf-8")).hexdigest()
            if path.name in done:
                if done[path.name] != digest:
                    # Views are CREATE OR ALTER: re-applying a changed file is safe and intended.
                    for batch in split_batches(script):
                        conn.execute(sa.text(batch))
                    conn.execute(sa.text("UPDATE dbo.schema_migrations SET sha256 = :h, applied_at = SYSUTCDATETIME() "
                                         "WHERE name = :n"), {"h": digest, "n": path.name})
                    applied_now.append(f"{path.name} (re-applied)")
                continue
            for batch in split_batches(script):
                conn.execute(sa.text(batch))
            conn.execute(sa.text("INSERT INTO dbo.schema_migrations (name, sha256) VALUES (:n, :h)"),
                         {"n": path.name, "h": digest})
            applied_now.append(path.name)
    return applied_now


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dir", default="db/migrations")
    args = parser.parse_args()
    applied = apply_migrations(args.dir)
    print("Applied: " + (", ".join(applied) if applied else "nothing (up to date)"))
