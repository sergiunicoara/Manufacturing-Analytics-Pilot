"""Safe SQL Server backup inspection and restore into a NEW, disposable database.

Safety rules, enforced in code:
- Database names must match NAME_PATTERN; backup paths must be absolute .bak files under an allowed
  root and contain only safe characters. Nothing else reaches the T-SQL text.
- Restore refuses when the target database already exists. WITH REPLACE is never used, and nothing
  is dropped or deleted.
- Backups are COPY_ONLY, so they do not disturb any existing backup chain.
- RESTORE VERIFYONLY alone is not proof of a usable restore; `roundtrip` also restores and compares
  schema objects and row counts against the source.

Usage (inside the api container):
  python -m app.db.restore backup    --database mfg_analytics_pilot --path /var/opt/mssql/data/pilot.bak
  python -m app.db.restore inspect   --path /var/opt/mssql/data/pilot.bak
  python -m app.db.restore restore   --path ... --target pilot_restore_check
  python -m app.db.restore roundtrip --database mfg_analytics_pilot --target pilot_restore_check_20260929
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import PurePosixPath

import sqlalchemy as sa

from app.config import settings
from app.db.connection import get_engine

NAME_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}$")
PATH_PATTERN = re.compile(r"^/[A-Za-z0-9_./-]+\.bak$")
ALLOWED_ROOTS = ("/var/opt/mssql/data", "/var/opt/mssql/backup")
DATA_DIRECTORY = "/var/opt/mssql/data"


class RestoreRefused(RuntimeError):
    pass


def validate_name(name: str) -> str:
    if not NAME_PATTERN.fullmatch(name or ""):
        raise RestoreRefused(f"Invalid database name {name!r}: letters, digits and underscores only, "
                             "starting with a letter, max 64 characters.")
    return name


def validate_backup_path(path: str) -> str:
    if not PATH_PATTERN.fullmatch(path or "") or ".." in PurePosixPath(path).parts:
        raise RestoreRefused(f"Invalid backup path {path!r}: absolute .bak path with safe characters only.")
    if not any(path.startswith(root + "/") for root in ALLOWED_ROOTS):
        raise RestoreRefused(f"Backup path must be under one of {ALLOWED_ROOTS}.")
    return path


def _master() -> sa.Engine:
    return get_engine(settings.mssql_odbc_url_master)


def _exec(sql: str, fetch: bool = False):
    """Run one statement in autocommit. BACKUP/RESTORE stream progress messages as extra result
    sets and only finish once they are drained, so every result set is consumed."""
    raw = _master().raw_connection()
    try:
        raw.driver_connection.autocommit = True
        cursor = raw.cursor()
        cursor.execute(sql)
        rows = None
        while True:
            if fetch and rows is None and cursor.description:
                columns = [c[0] for c in cursor.description]
                rows = [dict(zip(columns, values)) for values in cursor.fetchall()]
            if not cursor.nextset():
                break
        return rows if fetch else None
    finally:
        raw.close()


def database_exists(name: str) -> bool:
    validate_name(name)
    rows = _exec(f"SELECT DB_ID(N'{name}') AS id", fetch=True)
    return rows[0]["id"] is not None


def backup_file_exists(path: str) -> bool:
    validate_backup_path(path)
    rows = _exec(f"SELECT file_exists FROM sys.dm_os_file_exists(N'{path}')", fetch=True)
    return bool(rows and rows[0]["file_exists"])


def backup(database: str, path: str) -> None:
    """Copy-only backup to a NEW file. An existing file is never overwritten or appended to (it may be a
    client backup); NOINIT also guarantees that a file created between the check and the backup is kept."""
    validate_name(database)
    validate_backup_path(path)
    if not database_exists(database):
        raise RestoreRefused(f"Source database {database} does not exist.")
    if backup_file_exists(path):
        raise RestoreRefused(f"Backup file {path} already exists; refusing to overwrite it. Choose a new path.")
    _exec(f"BACKUP DATABASE [{database}] TO DISK = N'{path}' WITH COPY_ONLY, NOINIT, CHECKSUM, "
          f"NAME = N'{database} pilot copy-only backup'")


def inspect(path: str) -> dict:
    validate_backup_path(path)
    header = _exec(f"RESTORE HEADERONLY FROM DISK = N'{path}'", fetch=True)
    files = _exec(f"RESTORE FILELISTONLY FROM DISK = N'{path}'", fetch=True)
    _exec(f"RESTORE VERIFYONLY FROM DISK = N'{path}' WITH CHECKSUM")
    return {"database": header[0]["DatabaseName"], "backup_finish": str(header[0]["BackupFinishDate"]),
            "files": [{"logical_name": f["LogicalName"], "type": f["Type"], "physical_name": f["PhysicalName"],
                       "file_id": int(f["FileId"])} for f in files],
            "verifyonly": "passed (media readable and checksums valid; not proof of a usable restore)"}


def move_clauses(files: list[dict], target: str) -> list[str]:
    """One distinct physical file per backup file, named by FileId: the primary data file (FileId 1) is
    <target>.mdf, other data files <target>_<id>.ndf, log files <target>_log<id>.ldf."""
    moves = []
    for f in files:
        logical = f["logical_name"]
        if not NAME_PATTERN.fullmatch(logical):
            raise RestoreRefused(f"Unexpected logical file name {logical!r} in backup.")
        file_id = int(f["file_id"])
        if f["type"] == "L":
            name = f"{target}_log{file_id}.ldf"
        elif f["type"] == "D":
            name = f"{target}.mdf" if file_id == 1 else f"{target}_{file_id}.ndf"
        else:
            raise RestoreRefused(f"File {logical!r} has type {f['type']!r} (full-text or FILESTREAM); "
                                 "restore it manually with an explicit MOVE.")
        moves.append(f"MOVE N'{logical}' TO N'{DATA_DIRECTORY}/{name}'")
    return moves


def restore(path: str, target: str) -> dict:
    validate_backup_path(path)
    validate_name(target)
    if database_exists(target):
        raise RestoreRefused(f"Target database {target} already exists; refusing to overwrite. "
                             "Choose a new name. WITH REPLACE is never used.")
    info = inspect(path)
    moves = move_clauses(info["files"], target)
    _exec(f"RESTORE DATABASE [{target}] FROM DISK = N'{path}' WITH {', '.join(moves)}, CHECKSUM, RECOVERY")
    return {"restored": target, "from": path, "moves": moves}


def snapshot(database: str) -> dict:
    """Schema objects and exact row counts per user table."""
    validate_name(database)
    tables = _exec(f"SELECT t.name FROM [{database}].sys.tables AS t ORDER BY t.name", fetch=True)
    counts = {}
    for row in tables:
        table = row["name"]
        if not NAME_PATTERN.fullmatch(table):
            raise RestoreRefused(f"Unexpected table name {table!r}.")
        counts[table] = _exec(f"SELECT COUNT_BIG(*) AS n FROM [{database}].dbo.[{table}]", fetch=True)[0]["n"]
    views = _exec(f"SELECT COUNT(*) AS n FROM [{database}].sys.views", fetch=True)[0]["n"]
    fks = _exec(f"SELECT COUNT(*) AS n FROM [{database}].sys.foreign_keys", fetch=True)[0]["n"]
    return {"tables": counts, "views": views, "foreign_keys": fks}


def roundtrip(database: str, target: str, path: str | None = None) -> dict:
    validate_name(database)
    validate_name(target)
    path = path or f"{DATA_DIRECTORY}/{target}.bak"
    validate_backup_path(path)
    if database_exists(target):
        raise RestoreRefused(f"Target database {target} already exists; refusing to overwrite.")
    backup(database, path)
    restored = restore(path, target)
    source, copy = snapshot(database), snapshot(target)
    mismatches = [t for t in source["tables"] if source["tables"][t] != copy["tables"].get(t)]
    ok = (not mismatches and source["views"] == copy["views"]
          and source["foreign_keys"] == copy["foreign_keys"] and set(source["tables"]) == set(copy["tables"]))
    return {"ok": ok, "source": database, "target": target, "backup": path, "restore": restored,
            "tables_compared": len(source["tables"]), "rows_compared": sum(source["tables"].values()),
            "views": [source["views"], copy["views"]], "foreign_keys": [source["foreign_keys"], copy["foreign_keys"]],
            "mismatched_tables": mismatches}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["backup", "inspect", "restore", "roundtrip"])
    parser.add_argument("--database")
    parser.add_argument("--path")
    parser.add_argument("--target")
    args = parser.parse_args()
    if args.command == "backup":
        backup(args.database, args.path)
        result = {"backup": args.path}
    elif args.command == "inspect":
        result = inspect(args.path)
    elif args.command == "restore":
        result = restore(args.path, args.target)
    else:
        result = roundtrip(args.database, args.target, args.path)
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
