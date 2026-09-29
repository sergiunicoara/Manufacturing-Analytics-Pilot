"""Refresh only production_orders / production_order_operations from regenerated staging CSVs.

Non-destructive alternative to the full DDL reload (which drops every table, including scenario and
cost history). Refuses unless the staging extract matches the database everywhere else:
- every other source table has the same row count in staging and in the database;
- every production order already in the database keeps its identity columns (order number, item,
  quantity, status, planned dates) — only actual dates may change.
Then, in one transaction: rewrite operations, update actual dates of existing orders, insert new
orders. Derived tables (dq_findings, forecast_consumption, scenario results) are not touched here;
re-run the DQ engine and restart the API afterwards.

Usage: python -m app.loader.refresh_execution [--staging-dir staging]
"""
from __future__ import annotations

import argparse
import os

import pandas as pd
import sqlalchemy as sa

from app.db.connection import get_engine, reflect_metadata
from app.loader.load_staging_to_sql import TABLE_ORDER, _coerce_value

REFRESHED = ("production_orders", "production_order_operations")
IDENTITY_COLUMNS = ("order_number", "item_id", "qty", "status", "planned_start", "planned_finish")


def _records(df: pd.DataFrame, table: sa.Table) -> list[dict]:
    types = {c.name: c.type for c in table.columns}
    return [{k: _coerce_value(v, types[k]) for k, v in row.items() if k in types} for row in df.to_dict("records")]


def check(engine: sa.Engine, staging_dir: str) -> list[str]:
    problems = []
    with engine.connect() as conn:
        for name in TABLE_ORDER:
            if name in REFRESHED:
                continue
            db_rows = conn.execute(sa.text(f"SELECT COUNT(*) FROM dbo.[{name}]")).scalar()
            csv_rows = len(pd.read_csv(os.path.join(staging_dir, f"{name}.csv")))
            if db_rows != csv_rows:
                problems.append(f"{name}: database has {db_rows} rows, staging has {csv_rows}")
    existing = pd.read_sql("SELECT production_order_id, " + ", ".join(IDENTITY_COLUMNS) + " FROM dbo.production_orders",
                           engine).set_index("production_order_id")
    staged = pd.read_csv(os.path.join(staging_dir, "production_orders.csv")).set_index("production_order_id")
    missing = existing.index.difference(staged.index)
    if len(missing):
        problems.append(f"{len(missing)} existing production orders are absent from staging")
    common = existing.index.intersection(staged.index)
    for column in IDENTITY_COLUMNS:
        a, b = existing.loc[common, column].astype(str), staged.loc[common, column].astype(str)
        if column in ("qty",):
            a, b = existing.loc[common, column].astype(float), staged.loc[common, column].astype(float)
        if column in ("planned_start", "planned_finish"):
            a = pd.to_datetime(existing.loc[common, column]).dt.date
            b = pd.to_datetime(staged.loc[common, column]).dt.date
        changed = int((a != b).sum())
        if changed:
            problems.append(f"{changed} existing production orders changed {column}")
    return problems


def refresh(staging_dir: str = "staging") -> dict:
    engine = get_engine()
    problems = check(engine, staging_dir)
    if problems:
        raise SystemExit("Refusing to refresh: " + "; ".join(problems))
    metadata = reflect_metadata(engine)
    orders_table, ops_table = metadata.tables["production_orders"], metadata.tables["production_order_operations"]
    orders = pd.read_csv(os.path.join(staging_dir, "production_orders.csv"))
    ops = pd.read_csv(os.path.join(staging_dir, "production_order_operations.csv"))
    existing_ids = set(pd.read_sql("SELECT production_order_id FROM dbo.production_orders", engine)["production_order_id"])
    old_orders = orders.loc[orders["production_order_id"].isin(existing_ids)]
    new_orders = orders.loc[~orders["production_order_id"].isin(existing_ids)]
    updates = [{"id": r["production_order_id"], "a_start": r["actual_start"], "a_finish": r["actual_finish"]}
               for r in _records(old_orders[["production_order_id", "actual_start", "actual_finish"]], orders_table)]
    with engine.begin() as conn:
        conn.execute(sa.text("DELETE FROM dbo.production_order_operations"))
        conn.execute(sa.text("UPDATE dbo.production_orders SET actual_start = :a_start, actual_finish = :a_finish, "
                             "updated_at = SYSUTCDATETIME() WHERE production_order_id = :id"), updates)
        if len(new_orders):
            conn.execute(sa.text("SET IDENTITY_INSERT dbo.production_orders ON"))
            conn.execute(orders_table.insert(), _records(new_orders, orders_table))
            conn.execute(sa.text("SET IDENTITY_INSERT dbo.production_orders OFF"))
        conn.execute(sa.text("SET IDENTITY_INSERT dbo.production_order_operations ON"))
        conn.execute(ops_table.insert(), _records(ops, ops_table))
        conn.execute(sa.text("SET IDENTITY_INSERT dbo.production_order_operations OFF"))
    return {"orders_updated": len(updates), "orders_inserted": len(new_orders), "operations_loaded": len(ops)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--staging-dir", default="staging")
    print(refresh(parser.parse_args().staging_dir))
