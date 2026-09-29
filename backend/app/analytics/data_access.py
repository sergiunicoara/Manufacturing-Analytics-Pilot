"""Thin DB I/O layer: loads tables from SQL Server into pandas DataFrames for
the pure analytics/DQ functions to consume, and writes their results back.
Nothing in here computes anything — see analytics/bom.py, analytics/netting.py,
dq/rules.py for the actual logic.
"""
from __future__ import annotations

import pandas as pd
import sqlalchemy as sa

from app.synthetic.run_generator import TABLE_ORDER


def load_all_tables(engine: sa.engine.Engine) -> dict[str, pd.DataFrame]:
    tables = {}
    with engine.connect() as conn:
        for table_name in TABLE_ORDER:
            tables[table_name] = pd.read_sql_table(table_name, conn)
    return tables


FINDING_COLUMNS = ("rule_id", "entity", "record_id", "severity", "classification", "origin",
                   "manifest_key", "impact_scope", "affected_entity_type", "affected_entity_id",
                   "description", "detected_value", "expected_constraint", "recommended_action")


def finding_record(finding) -> dict:
    return {column: getattr(finding, column) for column in FINDING_COLUMNS}


def write_findings(engine: sa.engine.Engine, findings: list, metadata: sa.MetaData) -> int:
    from app.dq.engine import Finding

    if not findings:
        return 0
    table = metadata.tables["dq_findings"]
    records = []
    for f in findings:
        assert isinstance(f, Finding)
        records.append(finding_record(f))
    with engine.begin() as conn:
        conn.execute(sa.text("DELETE FROM dq_findings"))
        conn.execute(table.insert(), records)
    return len(records)


def write_consumption_audit(engine: sa.engine.Engine, events: list[dict]) -> int:
    """Replace the derived forecast_consumption audit trail with the current run's events."""
    records = [{"forecast_id": int(e["forecast_id"]), "sales_order_line_id": int(e["sales_order_line_id"]),
                "consumed_qty": float(e["consumed_qty"]), "consumption_date": e["consumption_date"]}
               for e in events]
    with engine.begin() as conn:
        conn.execute(sa.text("DELETE FROM forecast_consumption"))
        if records:
            conn.execute(sa.text("INSERT INTO forecast_consumption (forecast_id, sales_order_line_id, consumed_qty, "
                                 "consumption_date) VALUES (:forecast_id, :sales_order_line_id, :consumed_qty, "
                                 ":consumption_date)"), records)
    return len(records)
