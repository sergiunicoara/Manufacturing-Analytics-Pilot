"""Shared dataclasses for the analytics core. Every analytics module in this
package is a pure function over plain data (pandas DataFrames / dataclasses)
with no I/O side effects — see PLAN.md's architecture section. A thin
orchestration layer (analytics/data_access.py, dq/run_dq_engine.py,
analytics/run_baseline_netting.py) does the actual DB reads/writes and calls
these pure functions.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field


@dataclass(frozen=True)
class LineageStep:
    """One BOM-component link traversed while exploding a parent item."""

    parent_item_id: int
    parent_item_code: str
    bom_id: int
    bom_component_id: int
    revision: str
    effective_from: dt.date
    effective_to: dt.date | None
    quantity_per: float
    scrap_pct: float


@dataclass
class ExplosionLine:
    """One node visited during BOM explosion — every intermediate
    subassembly and every raw/purchased leaf, not just the leaves."""

    component_item_id: int
    component_item_code: str
    component_item_type: str | None  # None only when orphan (item doesn't exist)
    path: list[LineageStep]
    cumulative_quantity_per_unit: float  # qty of this component per 1 unit of the top-level item
    gross_requirement: float  # cumulative_quantity_per_unit * top-level order qty
    provenance: str = "DERIVED"
    incomplete: bool = False
    blocked_reason: str | None = None


@dataclass
class ExplosionResult:
    top_item_id: int
    top_item_code: str
    top_qty: float
    as_of_date: dt.date
    lines: list[ExplosionLine] = field(default_factory=list)
    # component_item_id -> total gross requirement summed across every path
    # that reached it *without* hitting a blocking issue on that path.
    aggregated_requirements: dict[int, float] = field(default_factory=dict)

    @property
    def incomplete(self) -> bool:
        return any(line.incomplete for line in self.lines)

    @property
    def blocked_branches(self) -> list[ExplosionLine]:
        return [line for line in self.lines if line.incomplete]


@dataclass
class NettingResult:
    item_id: int
    item_code: str
    need_by_date: dt.date
    gross_requirement: float
    usable_inventory: float
    excluded_inventory: float
    excluded_inventory_reason: str | None
    scheduled_receipts: float
    net_requirement: float
    shortage_quantity: float
    provenance: str = "DERIVED"
