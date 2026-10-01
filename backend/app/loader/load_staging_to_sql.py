"""Loads the ERP-extract CSV files in staging/ into SQL Server, against the
schema created by db/ddl/001_schema.sql.

Every table uses an explicit, generator-assigned surrogate integer ID (see
synthetic/context.py), so every insert runs under
`SET IDENTITY_INSERT <table> ON` — the loaded IDENTITY values end up exactly
matching what the generator assigned, which is what keeps the in-memory FK
wiring from generation valid after loading.

Usage: python -m app.loader.load_staging_to_sql [--staging-dir staging] [--ddl db/ddl/001_schema.sql]
"""
from __future__ import annotations

import argparse
import datetime as dt
import math
import os

import pandas as pd
import sqlalchemy as sa

from app.db.connection import get_engine, reflect_metadata, run_ddl_file, wait_for_sql_server

# Dependency order — parents before children (matches synthetic/run_generator.py).
TABLE_ORDER = [
    "sites", "warehouses", "customers", "suppliers", "work_centres", "items",
    "bom_headers", "bom_components", "routing_headers", "routing_operations",
    "forecast_versions", "customer_forecasts", "sales_orders", "sales_order_lines",
    "production_orders", "production_order_operations",
    "purchase_orders", "purchase_order_lines",
    "capacity_calendar", "standard_costs",
    "inventory", "wip",
]


def _coerce_value(value, col_type: sa.types.TypeEngine):
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    if pd.isna(value):
        return None

    if isinstance(col_type, (sa.Date,)):
        if isinstance(value, dt.date):
            return value
        return pd.to_datetime(value).date()
    if isinstance(col_type, (sa.DateTime,)):
        if isinstance(value, dt.datetime):
            return value
        return pd.to_datetime(value).to_pydatetime()
    if isinstance(col_type, sa.Boolean):
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in ("true", "1", "1.0")
    if isinstance(col_type, sa.Integer):
        return int(value)
    if isinstance(col_type, (sa.Numeric, sa.Float)):
        return float(value)
    return value


def _load_table(engine: sa.engine.Engine, metadata: sa.MetaData, table_name: str, staging_dir: str) -> int:
    csv_path = os.path.join(staging_dir, f"{table_name}.csv")
    df = pd.read_csv(csv_path)
    if df.empty:
        return 0

    table = metadata.tables[table_name]
    columns = {c.name: c.type for c in table.columns}

    records = []
    for row in df.to_dict(orient="records"):
        record = {}
        for col_name, raw_value in row.items():
            if col_name not in columns:
                continue
            record[col_name] = _coerce_value(raw_value, columns[col_name])
        records.append(record)

    pk_col = list(table.primary_key.columns)[0].name

    with engine.begin() as conn:
        conn.execute(sa.text(f"SET IDENTITY_INSERT {table_name} ON"))
        conn.execute(table.insert(), records)
        conn.execute(sa.text(f"SET IDENTITY_INSERT {table_name} OFF"))

    return len(records)


def completeness_gate(staging_dir: str) -> None:
    """Refuse to start the destructive reload when the staged data fails the data-request contract (BLOCK).
    WARN results are printed and allowed. Runs before anything is dropped."""
    from app.integration.completeness import BLOCK, PASS, load_contract, run_checks, tables_from_csv
    contract = load_contract()
    report = run_checks(tables_from_csv(staging_dir, contract), contract)
    print(f"Completeness check: {report['overall']}  {report['counts']}")
    for r in report["results"]:
        if r["status"] != PASS:
            print(f"  {r['status']:5s} {r['dataset']:28s} {r['check']:52s} {r['detail']}")
    if report["overall"] == BLOCK:
        raise SystemExit("Staged data fails the completeness contract (BLOCK); nothing was dropped or loaded. "
                         "Fix the source files, or pass --skip-completeness to load anyway.")


def load_all(staging_dir: str = "staging", ddl_path: str = "db/ddl/001_schema.sql",
             migrations_dir: str | None = "db/migrations", check_completeness: bool = True) -> dict:
    """Destructive bootstrap: the DDL drops and recreates every table, then the versioned migrations
    (result tables, read models, roles, run data version) are re-applied so the schema is current
    before the API starts. Pass migrations_dir=None only to inspect the bare DDL. Staged data is checked against
    the completeness contract first; a BLOCK aborts before anything is dropped."""
    if check_completeness:
        completeness_gate(staging_dir)
    wait_for_sql_server()
    run_ddl_file(ddl_path)

    engine = get_engine()
    if migrations_dir:
        from app.db.migrate import apply_migrations
        print("Applied migrations:", ", ".join(apply_migrations(migrations_dir, engine)) or "none")
    metadata = reflect_metadata(engine)

    counts = {}
    for table_name in TABLE_ORDER:
        counts[table_name] = _load_table(engine, metadata, table_name, staging_dir)
        print(f"Loaded {table_name:35s} {counts[table_name]:>7d} rows")

    return counts


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--staging-dir", default="staging")
    parser.add_argument("--ddl", default="db/ddl/001_schema.sql")
    parser.add_argument("--migrations", default="db/migrations")
    parser.add_argument("--skip-completeness", action="store_true", help="load even if the completeness check reports BLOCK")
    args = parser.parse_args()
    load_all(args.staging_dir, args.ddl, args.migrations, check_completeness=not args.skip_completeness)
