import datetime as dt
import re
from pathlib import Path

import pandas as pd
import pytest
import sqlalchemy as sa

from app.db.migrate import split_batches
from app.loader import refresh_execution as rx
from app.loader.load_staging_to_sql import _coerce_value

def _db_dir() -> Path:
    """Repo layout (backend/tests -> ../../db) or container layout (/app/tests -> /app/db)."""
    for parent in Path(__file__).resolve().parents[1:3]:
        if (parent / "db" / "ddl").exists():
            return parent / "db"
    raise FileNotFoundError("db/ddl not found next to the tests")


DDL = _db_dir() / "ddl" / "001_schema.sql"
MIGRATIONS = _db_dir() / "migrations"


def _orders(**changes):
    frame = pd.DataFrame({"production_order_id": [1, 2], "order_number": ["A", "B"], "item_id": [10, 11],
                          "qty": [5.0, 6.0], "status": ["COMPLETED", "PLANNED"],
                          "planned_start": ["2026-01-05", "2026-02-02"], "planned_finish": ["2026-01-09", "2026-02-06"]})
    for column, values in changes.items():
        frame[column] = values
    return frame.set_index("production_order_id")


def test_refresh_allows_new_orders_and_actual_date_changes_only():
    staged = pd.concat([_orders(), pd.DataFrame({"order_number": ["C"], "item_id": [12], "qty": [1.0], "status": ["COMPLETED"],
                                                 "planned_start": ["2026-03-02"], "planned_finish": ["2026-03-03"]},
                                                index=pd.Index([3], name="production_order_id"))])
    assert rx.identity_problems(_orders(), staged) == []


@pytest.mark.parametrize("column,values,fragment", [
    ("item_id", [10, 99], "changed item_id"), ("qty", [5.0, 7.0], "changed qty"), ("status", ["COMPLETED", "RELEASED"], "changed status"),
    ("planned_start", ["2026-01-05", "2026-02-09"], "changed planned_start"), ("order_number", ["A", "Z"], "changed order_number")])
def test_refresh_refuses_when_an_existing_order_changes_identity(column, values, fragment):
    problems = rx.identity_problems(_orders(), _orders(**{column: values}))
    assert len(problems) == 1 and fragment in problems[0]


def test_refresh_refuses_missing_orders_and_row_count_drift_in_other_tables():
    assert "1 existing production orders are absent" in rx.identity_problems(_orders(), _orders().iloc[:1])[0]
    problems = rx.count_problems({"items": 10, "wip": 5, "production_orders": 99}, {"items": 10, "wip": 6, "production_orders": 1})
    assert problems == ["wip: database has 5 rows, staging has 6"]      # the refreshed tables are exempt


def test_coerce_value_handles_nulls_dates_and_types():
    assert _coerce_value(float("nan"), sa.Integer()) is None and _coerce_value(None, sa.Date()) is None
    assert _coerce_value("2026-06-01", sa.Date()) == dt.date(2026, 6, 1)
    assert _coerce_value("2026-06-01 08:30:00", sa.DateTime()) == dt.datetime(2026, 6, 1, 8, 30)
    assert _coerce_value(3.0, sa.Integer()) == 3 and isinstance(_coerce_value(3, sa.Numeric()), float)
    assert _coerce_value("True", sa.Boolean()) is True and _coerce_value("0", sa.Boolean()) is False


def test_split_batches_handles_go_separators_case_insensitively():
    assert split_batches("SELECT 1\nGO\nselect 2\ngo\n\nSELECT 3") == ["SELECT 1", "select 2", "SELECT 3"]


def _fk_parents(create_sql: str) -> dict[str, set[str]]:
    parents = {}
    for name, body in re.findall(r"CREATE TABLE (?:dbo\.)?(\w+)\s*\((.*?)\n\);", create_sql, re.S):
        parents[name] = {p for p in re.findall(r"REFERENCES (?:dbo\.)?(\w+)", body) if p != name}
    return parents


def test_ddl_drops_children_before_parents_including_migration_tables():
    """A reload of an already-migrated database must not fail on a foreign key from a migration table."""
    ddl = DDL.read_text(encoding="utf-8")
    parents = _fk_parents(ddl)
    for path in sorted(MIGRATIONS.glob("*.sql")):
        parents.update(_fk_parents(path.read_text(encoding="utf-8").replace("CREATE TABLE dbo.", "CREATE TABLE ")
                                   .replace("    );\nEND", "\n);")))
    drops = re.findall(r"DROP TABLE (\w+);", ddl)
    order = {name: i for i, name in enumerate(drops)}
    assert "cost_results" in parents and "cost_results" in order and "schema_migrations" in order
    for child, its_parents in parents.items():
        for parent in its_parents & order.keys():
            if child in order:
                assert order[child] < order[parent], f"{child} must be dropped before {parent}"
    migrated_tables = {n for path in MIGRATIONS.glob("*.sql")
                       for n in re.findall(r"CREATE TABLE dbo\.(\w+)", path.read_text(encoding="utf-8"))}
    assert migrated_tables <= set(order), f"tables created by migrations but not dropped by the DDL: {migrated_tables - set(order)}"


def test_stale_schema_gives_an_actionable_error_not_a_sql_error(monkeypatch):
    from app.db import migrate
    monkeypatch.setattr(migrate, "schema_problems", lambda engine=None: ["scenario_runs.data_version"])
    with pytest.raises(RuntimeError, match=r"out of date.*scenario_runs\.data_version.*app\.db\.migrate"):
        migrate.require_current_schema()
    monkeypatch.setattr(migrate, "schema_problems", lambda engine=None: [])
    migrate.require_current_schema()
