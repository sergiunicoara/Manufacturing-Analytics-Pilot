"""Create the least-privilege logins from environment secrets and map them to the migration's roles.

Run as an administrator, once per database:
  PILOT_READER_PASSWORD=... PILOT_APP_PASSWORD=... python -m app.db.security_setup
Passwords are passed as query parameters and quoted inside T-SQL with QUOTENAME; they are never
interpolated into SQL text, printed or logged. Existing logins are left as they are (not altered).
"""
from __future__ import annotations

import os

from app.db.connection import get_engine
from app.db.restore import validate_name
from app.config import settings

LOGINS = {"pilot_reader": ("PILOT_READER_PASSWORD", "pilot_reader_role"),
          "pilot_app": ("PILOT_APP_PASSWORD", "pilot_app_role")}

CREATE_LOGIN = """
DECLARE @login sysname = ?, @password nvarchar(128) = ?;
IF SUSER_ID(@login) IS NULL
BEGIN
    DECLARE @sql nvarchar(max) = N'CREATE LOGIN ' + QUOTENAME(@login) + N' WITH PASSWORD = '
        + QUOTENAME(@password, '''') + N', CHECK_POLICY = ON, DEFAULT_DATABASE = ' + QUOTENAME(?);
    EXEC sys.sp_executesql @sql;
END
"""

CREATE_USER = """
DECLARE @login sysname = ?, @role sysname = ?;
IF DATABASE_PRINCIPAL_ID(@login) IS NULL
BEGIN
    DECLARE @sql nvarchar(max) = N'CREATE USER ' + QUOTENAME(@login) + N' FOR LOGIN ' + QUOTENAME(@login);
    EXEC sys.sp_executesql @sql;
END
IF IS_ROLEMEMBER(@role, @login) = 0 OR IS_ROLEMEMBER(@role, @login) IS NULL
BEGIN
    DECLARE @add nvarchar(max) = N'ALTER ROLE ' + QUOTENAME(@role) + N' ADD MEMBER ' + QUOTENAME(@login);
    EXEC sys.sp_executesql @add;
END
"""


def _run(engine, sql: str, params: tuple) -> None:
    raw = engine.raw_connection()
    try:
        raw.driver_connection.autocommit = True
        cursor = raw.cursor()
        cursor.execute(sql, params)
        while cursor.nextset():
            pass
    finally:
        raw.close()


def setup(database: str | None = None) -> list[str]:
    database = validate_name(database or settings.mssql_database)
    missing = [env for env, _ in LOGINS.values() if not os.environ.get(env)]
    if missing:
        raise SystemExit(f"Set {', '.join(missing)} in the environment first.")
    master = get_engine(settings.mssql_odbc_url_master)
    target = get_engine()
    done = []
    for login, (env, role) in LOGINS.items():
        _run(master, CREATE_LOGIN, (login, os.environ[env], database))
        _run(target, CREATE_USER, (login, role))
        done.append(f"{login} -> {role}")
    return done


if __name__ == "__main__":
    print("Configured: " + ", ".join(setup()))
