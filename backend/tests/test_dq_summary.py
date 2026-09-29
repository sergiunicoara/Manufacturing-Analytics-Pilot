import csv
import io

import pandas as pd

from app.dq import summary
from app.dq.engine import Finding


def _f(rule, entity, record, cls="BLOCKING", origin="INJECTED", scope="ENTITY_BLOCKING"):
    return Finding(rule_id=rule, entity=entity, record_id=record, severity="HIGH", classification=cls,
                   origin=origin, description=f"{rule} on {record}", impact_scope=scope if cls == "BLOCKING" else None)


TABLES = {"routing_operations": pd.DataFrame({"x": range(200)}), "items": pd.DataFrame({"x": range(50)})}
FINDINGS = [_f("missing_routing_times", "routing_operations", "7"),
            _f("missing_routing_times", "routing_operations", "7"),     # same record, second finding
            _f("missing_routing_times", "routing_operations", "10"),
            _f("invalid_uom", "items", "3", cls="TOLERABLE", origin="ORGANIC")]


def test_groups_report_unique_records_and_table_share():
    groups = {g["rule_id"]: g for g in summary.explain(FINDINGS, TABLES)}
    routing = groups["missing_routing_times"]
    assert routing["findings"] == 3
    assert routing["unique_records"] == 2
    assert routing["records_affected_pct"] == 1.0          # 2 of 200 rows
    assert groups["invalid_uom"]["impact_scope"] is None


def test_blocking_share_of_findings_differs_from_share_of_records():
    coverage = {row["entity"]: row for row in summary.entity_coverage(FINDINGS, TABLES)}
    assert coverage["routing_operations"]["records_with_blocking_findings"] == 2
    assert coverage["routing_operations"]["blocking_records_pct"] == 1.0
    assert coverage["items"]["records_with_blocking_findings"] == 0
    blocking_findings_share = sum(f.classification == "BLOCKING" for f in FINDINGS) / len(FINDINGS)
    assert blocking_findings_share == 0.75


def test_filter_and_full_csv_export_are_not_truncated():
    many = [_f("missing_routing_times", "routing_operations", str(i)) for i in range(450)]
    rows = list(csv.DictReader(io.StringIO(summary.to_csv(many))))
    assert len(rows) == 450
    assert rows[0]["impact_scope"] == "ENTITY_BLOCKING"
    assert len(summary.filter_findings(many + FINDINGS, classification="TOLERABLE")) == 1
