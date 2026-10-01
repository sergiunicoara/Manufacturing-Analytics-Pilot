"""Create the least-privilege logins from environment secrets and map them to the migration's roles.

Run as an administrator, once per database, BEFORE the API is started with the secured profile (the
secured API logs in as `pilot_app`, which does not exist until this has run):
  MSSQL_ADMIN_PASSWORD=... PILOT_READER_PASSWORD=... PILOT_APP_PASSWORD=... python -m app.db.security_setup
The administrator login is `MSSQL_ADMIN_USER` (default `sa`) with `MSSQL_ADMIN_PASSWORD` (or `MSSQL_SA_PASSWORD`);
it is independent of the login the API itself uses.
Passwords are passed as query parameters and quoted inside T-SQL (REPLACE-doubled quotes); they are never
interpolated into SQL text in Python, printed or logged. An existing login gets its password rotated to the supplied value.
"""
from __future__ import annotations

import os
from urllib.parse import quote_plus

from app.db.connection import get_engine
from app.db.restore import validate_name
from app.config import settings

LOGINS = {"pilot_reader": ("PILOT_READER_PASSWORD", "pilot_reader_role"),
          "pilot_app": ("PILOT_APP_PASSWORD", "pilot_app_role")}

MAX_PASSWORD_LENGTH = 128   # SQL Server's limit; longer values must be rejected, not truncated

# nvarchar(max) so nothing is truncated on the way in; the length is validated in Python first.
# Password quoting uses REPLACE (QUOTENAME's input is limited to 128 characters and returns NULL beyond it).
CREATE_OR_ROTATE_LOGIN = """
DECLARE @login sysname = ?, @password nvarchar(max) = ?, @database sysname = ?;
DECLARE @quoted nvarchar(max) = N'''' + REPLACE(@password, N'''', N'''''') + N'''';
DECLARE @sql nvarchar(max);
IF SUSER_ID(@login) IS NULL
    SET @sql = N'CREATE LOGIN ' + QUOTENAME(@login) + N' WITH PASSWORD = ' + @quoted
             + N', CHECK_POLICY = ON, DEFAULT_DATABASE = ' + QUOTENAME(@database);
ELSE
    SET @sql = N'ALTER LOGIN ' + QUOTENAME(@login) + N' WITH PASSWORD = ' + @quoted + N', CHECK_POLICY = ON';
EXEC sys.sp_executesql @sql;
SELECT CASE WHEN @sql LIKE N'CREATE%' THEN 'created' ELSE 'password rotated' END AS action;
"""


def password_problems(passwords: dict[str, str]) -> list[str]:
    return [f"{env} is longer than {MAX_PASSWORD_LENGTH} characters" for env, value in passwords.items()
            if len(value) > MAX_PASSWORD_LENGTH]


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


def _run(engine, sql: str, params: tuple) -> str | None:
    """Execute one parameterised batch in autocommit; return the first column of the first result row."""
    raw = engine.raw_connection()
    try:
        raw.driver_connection.autocommit = True
        cursor = raw.cursor()
        cursor.execute(sql, params)
        first = None
        while True:
            if first is None and cursor.description:
                row = cursor.fetchone()
                first = row[0] if row else None
            if not cursor.nextset():
                break
        return first
    finally:
        raw.close()


def admin_urls(database: str) -> tuple[str, str]:
    """(master URL, database URL) for the administrator login, built from the environment, not the API's settings."""
    password = os.environ.get("MSSQL_ADMIN_PASSWORD") or os.environ.get("MSSQL_SA_PASSWORD")
    if not password:
        raise SystemExit("Set MSSQL_ADMIN_PASSWORD (the administrator login); the API's own login cannot create logins.")
    user = os.environ.get("MSSQL_ADMIN_USER", "sa")
    base = (f"mssql+pyodbc://{quote_plus(user)}:{quote_plus(password)}@{settings.mssql_host}:{settings.mssql_port}/{{db}}"
            "?driver=ODBC+Driver+18+for+SQL+Server&TrustServerCertificate=yes")
    return base.replace("{db}", "master"), base.replace("{db}", database)


def setup(database: str | None = None) -> list[str]:
    database = validate_name(database or settings.mssql_database)
    missing = [env for env, _ in LOGINS.values() if not os.environ.get(env)]
    if missing:
        raise SystemExit(f"Set {', '.join(missing)} in the environment first.")
    too_long = password_problems({env: os.environ[env] for env, _ in LOGINS.values()})
    if too_long:
        raise SystemExit("; ".join(too_long))
    master_url, target_url = admin_urls(database)
    master, target = get_engine(master_url), get_engine(target_url)
    done = []
    for login, (env, role) in LOGINS.items():
        action = _run(master, CREATE_OR_ROTATE_LOGIN, (login, os.environ[env], database))
        _run(target, CREATE_USER, (login, role))
        done.append(f"{login} {action} -> {role}")
    return done


if __name__ == "__main__":
    print("Configured: " + ", ".join(setup()))
