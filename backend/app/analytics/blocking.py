"""Query helper over dq_findings for scoped blocking checks [CP3 req. 2].

A BLOCKING finding does not, by default, invalidate the whole plant-wide
analysis (GLOBAL_BLOCKING) — most findings are ENTITY_BLOCKING (scoped to one
item/order/forecast record) or KPI_BLOCKING (scoped even narrower, to one
specific entity-period KPI, e.g. one work centre's one week). This module is
a pure, in-memory index built once per engine run from the current
dq_findings, so the period engine can cheaply ask "is X blocked" without
re-querying the database every period.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd


@dataclass
class BlockingIndex:
    global_blocking: bool = False
    global_reasons: list[str] = field(default_factory=list)
    entity_blocked: dict[tuple[str, str], list[str]] = field(default_factory=dict)
    kpi_blocked: dict[tuple[str, str], list[str]] = field(default_factory=dict)

    def is_entity_blocked(self, entity_type: str, entity_id) -> bool:
        if self.global_blocking:
            return True
        return (entity_type, str(entity_id)) in self.entity_blocked

    def is_kpi_blocked(self, entity_type: str, entity_id) -> bool:
        if self.global_blocking:
            return True
        return (entity_type, str(entity_id)) in self.kpi_blocked

    def reasons_for_entity(self, entity_type: str, entity_id) -> list[str]:
        return self.entity_blocked.get((entity_type, str(entity_id)), [])

    def reasons_for_kpi(self, entity_type: str, entity_id) -> list[str]:
        return self.kpi_blocked.get((entity_type, str(entity_id)), [])


def build_blocking_index(dq_findings: pd.DataFrame) -> BlockingIndex:
    index = BlockingIndex()
    if dq_findings.empty:
        return index

    blocking = dq_findings.loc[dq_findings["classification"] == "BLOCKING"]
    for _, row in blocking.iterrows():
        scope = row.get("impact_scope")
        if scope == "GLOBAL_BLOCKING":
            index.global_blocking = True
            index.global_reasons.append(str(row["description"]))
            continue

        entity_type = row.get("affected_entity_type")
        entity_id = row.get("affected_entity_id")
        if pd.isna(entity_type) or pd.isna(entity_id):
            continue
        key = (str(entity_type), str(entity_id))

        if scope == "ENTITY_BLOCKING":
            index.entity_blocked.setdefault(key, []).append(str(row["description"]))
        elif scope == "KPI_BLOCKING":
            index.kpi_blocked.setdefault(key, []).append(str(row["description"]))

    return index
