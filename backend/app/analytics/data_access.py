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


def write_findings(engine: sa.engine.Engine, findings: list, metadata: sa.MetaData) -> int:
    from app.dq.engine import Finding

    if not findings:
        return 0
    table = metadata.tables["dq_findings"]
    records = []
    for f in findings:
        assert isinstance(f, Finding)
        records.append({
            "rule_id": f.rule_id,
            "entity": f.entity,
            "record_id": f.record_id,
            "severity": f.severity,
            "classification": f.classification,
            "origin": f.origin,
            "manifest_key": f.manifest_key,
            "description": f.description,
            "detected_value": f.detected_value,
            "expected_constraint": f.expected_constraint,
            "recommended_action": f.recommended_action,
        })
    with engine.begin() as conn:
        conn.execute(sa.text("DELETE FROM dq_findings"))
        conn.execute(table.insert(), records)
    return len(records)
