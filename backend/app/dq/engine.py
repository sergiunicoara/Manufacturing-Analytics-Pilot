"""Finding model and rule-engine orchestration. Pure — `run_all` just calls
every registered rule function against the supplied tables and concatenates
results; no DB access here (see dq/run_dq_engine.py for that).
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass
class Finding:
    rule_id: str
    entity: str
    record_id: str
    severity: str  # LOW / MEDIUM / HIGH / CRITICAL
    classification: str  # TOLERABLE / ASSUMPTION_BASED / BLOCKING
    origin: str  # INJECTED / ORGANIC
    description: str
    manifest_key: str | None = None
    detected_value: str | None = None
    expected_constraint: str | None = None
    recommended_action: str | None = None


Tables = dict[str, pd.DataFrame]


def run_all(tables: Tables) -> list[Finding]:
    from app.dq.rules import ALL_RULES  # local import avoids a circular import at module load

    findings: list[Finding] = []
    for rule_fn in ALL_RULES:
        findings.extend(rule_fn(tables))
    return findings


def findings_to_dataframe(findings: list[Finding]) -> pd.DataFrame:
    return pd.DataFrame([f.__dict__ for f in findings])
