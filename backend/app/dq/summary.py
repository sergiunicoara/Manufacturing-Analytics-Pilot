"""Explain DQ findings by rule, origin, classification and impact scope.

The share of findings that are BLOCKING says nothing about how much data is
unusable: one rule can raise hundreds of findings on a small table, and a
KPI_BLOCKING finding withholds one KPI, not a record. So each group reports
unique affected records and their share of the source table's rows.
"""
from __future__ import annotations

import csv
import io
from collections import defaultdict

import pandas as pd

from app.dq.engine import Finding

EXPORT_COLUMNS = ("rule_id", "entity", "record_id", "severity", "classification", "origin", "manifest_key",
                  "impact_scope", "affected_entity_type", "affected_entity_id", "description",
                  "detected_value", "expected_constraint", "recommended_action")


def explain(findings: list[Finding], tables: dict[str, pd.DataFrame]) -> list[dict]:
    groups: dict[tuple, list[Finding]] = defaultdict(list)
    for f in findings:
        groups[(f.rule_id, f.entity, f.origin, f.classification, f.impact_scope)].append(f)
    rows = []
    for (rule_id, entity, origin, classification, scope), items in sorted(
            groups.items(), key=lambda kv: (-len(kv[1]), kv[0][0])):
        unique_records = len({f.record_id for f in items})
        table_rows = len(tables[entity]) if entity in tables else None
        rows.append({
            "rule_id": rule_id, "entity": entity, "origin": origin, "classification": classification,
            "impact_scope": scope, "findings": len(items), "unique_records": unique_records,
            "table_rows": table_rows,
            "records_affected_pct": round(100.0 * unique_records / table_rows, 2) if table_rows else None,
            "severity": sorted({f.severity for f in items}),
            "description": items[0].description,
        })
    return rows


def entity_coverage(findings: list[Finding], tables: dict[str, pd.DataFrame]) -> list[dict]:
    """Per source table: rows with any finding and rows with a BLOCKING one."""
    any_hit: dict[str, set] = defaultdict(set)
    blocking_hit: dict[str, set] = defaultdict(set)
    for f in findings:
        any_hit[f.entity].add(f.record_id)
        if f.classification == "BLOCKING":
            blocking_hit[f.entity].add(f.record_id)
    rows = []
    for entity in sorted(any_hit):
        total = len(tables[entity]) if entity in tables else None
        rows.append({"entity": entity, "table_rows": total,
                     "records_with_findings": len(any_hit[entity]),
                     "records_with_blocking_findings": len(blocking_hit[entity]),
                     "blocking_records_pct": round(100.0 * len(blocking_hit[entity]) / total, 2) if total else None})
    return rows


def filter_findings(findings: list[Finding], rule_id: str | None = None,
                    classification: str | None = None, origin: str | None = None,
                    entity: str | None = None) -> list[Finding]:
    return [f for f in findings
            if (rule_id is None or f.rule_id == rule_id)
            and (classification is None or f.classification == classification)
            and (origin is None or f.origin == origin)
            and (entity is None or f.entity == entity)]


def finding_dict(f: Finding) -> dict:
    return {column: getattr(f, column) for column in EXPORT_COLUMNS}


def to_csv(findings: list[Finding]) -> str:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=EXPORT_COLUMNS, lineterminator="\n")
    writer.writeheader()
    for f in findings:
        writer.writerow(finding_dict(f))
    return buffer.getvalue()
