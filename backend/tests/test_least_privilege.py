"""Live least-privilege checks. Needs SQL Server plus PILOT_READER_PASSWORD / PILOT_APP_PASSWORD for
logins created by app.db.security_setup; skipped otherwise. Writes happen inside rolled-back transactions."""
import os
from urllib.parse import quote_plus

import pytest
import sqlalchemy as sa

from app.config import settings


def _engine(login: str, env: str):
    password = os.environ.get(env)
    if not password:
        pytest.skip(f"{env} not set")
    url = (f"mssql+pyodbc://{login}:{quote_plus(password)}@{settings.mssql_host}:{settings.mssql_port}/"
           f"{settings.mssql_database}?driver=ODBC+Driver+18+for+SQL+Server&TrustServerCertificate=yes")
    engine = sa.create_engine(url)
    try:
        with engine.connect() as conn:
            conn.execute(sa.text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"cannot connect as {login}: {exc}")
    return engine


def _denied(engine, sql: str) -> bool:
    with engine.connect() as conn:
        trans = conn.begin()
        try:
            conn.execute(sa.text(sql))
            return False
        except sa.exc.DBAPIError as exc:
            return "permission" in str(exc).lower() or "denied" in str(exc).lower()
        finally:
            trans.rollback()


def _allowed(engine, sql: str) -> bool:
    with engine.connect() as conn:
        trans = conn.begin()
        try:
            conn.execute(sa.text(sql))
            return True
        finally:
            trans.rollback()


def test_reader_sees_only_read_models():
    reader = _engine("pilot_reader", "PILOT_READER_PASSWORD")
    assert _allowed(reader, "SELECT TOP 1 * FROM dbo.vw_weekly_load")
    assert _allowed(reader, "SELECT TOP 1 * FROM dbo.vw_forecast_accuracy_by_horizon")
    assert _denied(reader, "SELECT TOP 1 * FROM dbo.items")
    assert _denied(reader, "SELECT TOP 1 * FROM dbo.customers")
    assert _denied(reader, "DELETE FROM dbo.cost_results")


def test_app_writes_only_its_own_result_tables():
    app = _engine("pilot_app", "PILOT_APP_PASSWORD")
    assert _allowed(app, "SELECT TOP 1 * FROM dbo.items")
    assert _allowed(app, "DELETE FROM dbo.forecast_consumption WHERE 1 = 0")
    assert _allowed(app, "UPDATE dbo.cost_results SET units = units WHERE 1 = 0")
    assert _denied(app, "DELETE FROM dbo.items WHERE 1 = 0")
    assert _denied(app, "UPDATE dbo.sales_order_lines SET qty = qty WHERE 1 = 0")
    assert _denied(app, "CREATE TABLE dbo.pilot_should_not_exist (x INT)")
    assert _denied(app, "DROP TABLE dbo.cost_results")
    assert _denied(app, "ALTER ROLE pilot_app_role ADD MEMBER pilot_reader")
