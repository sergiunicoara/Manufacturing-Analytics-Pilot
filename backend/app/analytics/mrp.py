"""Cascading multi-level MRP explosion [CP3.1 req. A], replacing CP3's
single-level-per-period netting limitation.

Standard MRP "low-level coding": every item gets a low-level code (LLC) equal
to the *deepest* level at which it appears anywhere in the BOM graph. Items
are then netted in ascending LLC order (shallowest/FG first). A parent's
NET requirement (not its gross) is what gets exploded one level down, and an
item that appears under multiple parents (e.g. a shared raw material) has
all of its parents' contributions accumulated into one gross-requirement
bucket before it is netted exactly once — never before all of its parents
have already been netted and exploded.

Worked example (the one in the CP3.1 request): demand for CAB=100, FRAME
qty/CAB=1, usable FRAME inventory=80. FG CAB nets to net=100 (assume no CAB
inventory) and explodes 100 units of FRAME demand. FRAME (LLC=1) then nets
100 gross against its own 80 on-hand -> net=20. Only 20 FRAME's worth of
demand explodes further into FRAME's own components — not 100.

Pure functions only — no DB access, no period-to-period state (the caller,
period_engine.py, owns inventory_state and calls this once per period).
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

import pandas as pd

from app.analytics.bom import active_bom_headers
from app.analytics.models import LineageStep


def compute_low_level_codes(
    items_df: pd.DataFrame, bom_headers_df: pd.DataFrame, bom_components_df: pd.DataFrame
) -> dict[int, int]:
    """LLC(item) = 0 if nothing ever consumes it as a component (a true
    top-level item); otherwise 1 + max(LLC(parent) for every parent that
    consumes it anywhere in the structural BOM graph, ignoring effective
    dates — see period_engine.py's module docstring for why LLC is computed
    once from the structural graph rather than per as_of_date).

    A structural cycle (see app.analytics.bom.find_bom_cycles — the DQ
    engine's organic bom_cycle_detected rule) would make this recursion
    infinite; a cycle guard breaks it by treating the back-edge as if that
    item had no parents there, so LLC computation always terminates. The
    cycle itself is still reported by the DQ engine and still blocks
    explosion through that branch in explode_one_level.
    """
    valid_ids = set(items_df["item_id"])
    bom_id_to_parent = bom_headers_df.set_index("bom_id")["parent_item_id"].to_dict()

    parents_of: dict[int, set[int]] = {}
    for _, comp in bom_components_df.iterrows():
        comp_id = comp["component_item_id"]
        if comp_id not in valid_ids:
            continue  # orphan — handled as its own DQ finding, not here
        parent_id = bom_id_to_parent.get(comp["bom_id"])
        if parent_id is None or parent_id not in valid_ids:
            continue
        parents_of.setdefault(comp_id, set()).add(int(parent_id))

    memo: dict[int, int] = {}

    def llc(item_id: int, visiting: frozenset[int]) -> int:
        if item_id in memo:
            return memo[item_id]
        if item_id in visiting:
            return 0  # cycle guard
        parents = parents_of.get(item_id)
        if not parents:
            memo[item_id] = 0
            return 0
        result = 1 + max(llc(p, visiting | {item_id}) for p in parents)
        memo[item_id] = result
        return result

    return {int(item_id): llc(int(item_id), frozenset()) for item_id in valid_ids}


@dataclass
class ChildDemand:
    component_item_id: int
    gross_qty: float
    step: LineageStep | None
    incomplete: bool = False
    blocked_reason: str | None = None


@dataclass
class OneLevelResult:
    children: list[ChildDemand] = field(default_factory=list)
    is_leaf: bool = False
    incomplete: bool = False
    blocked_reason: str | None = None


def explode_one_level(
    item_id: int,
    qty: float,
    as_of_date: dt.date,
    items_df: pd.DataFrame,
    bom_headers_df: pd.DataFrame,
    bom_components_df: pd.DataFrame,
) -> OneLevelResult:
    """Explodes exactly one BOM level for `item_id` at `qty` — the driving
    quantity should be the item's NET requirement, not its gross. Reuses
    bom.py's active_bom_headers so effective-dated revision selection is
    identical to the CP2 explode() path (single source of truth for "which
    revision is active on this date")."""
    items_by_id = items_df.set_index("item_id")
    valid_ids = set(items_df["item_id"])

    headers = active_bom_headers(item_id, as_of_date, bom_headers_df)
    if headers.empty:
        return OneLevelResult(is_leaf=True)
    if len(headers) > 1:
        return OneLevelResult(incomplete=True, blocked_reason="ambiguous_bom_revision")

    bom_row = headers.iloc[0]
    components = bom_components_df.loc[bom_components_df["bom_id"] == bom_row["bom_id"]]
    parent_code = str(items_by_id.loc[item_id, "item_code"]) if item_id in items_by_id.index else "UNKNOWN"

    children: list[ChildDemand] = []
    for _, comp in components.iterrows():
        component_item_id = int(comp["component_item_id"])
        scrap_pct = float(comp["scrap_pct"])
        quantity_per = float(comp["quantity_per"])

        if component_item_id not in valid_ids:
            children.append(ChildDemand(component_item_id, 0.0, None, incomplete=True, blocked_reason="orphan_component"))
            continue
        if scrap_pct >= 1.0 or scrap_pct < 0:
            children.append(ChildDemand(component_item_id, 0.0, None, incomplete=True, blocked_reason="invalid_scrap_pct"))
            continue
        if quantity_per <= 0:
            children.append(ChildDemand(component_item_id, 0.0, None, incomplete=True, blocked_reason="non_positive_bom_quantity"))
            continue

        gross = qty * quantity_per / (1.0 - scrap_pct)
        step = LineageStep(
            parent_item_id=item_id, parent_item_code=parent_code, bom_id=int(bom_row["bom_id"]),
            bom_component_id=int(comp["bom_component_id"]), revision=str(bom_row["revision"]),
            effective_from=bom_row["effective_from"], effective_to=bom_row["effective_to"],
            quantity_per=quantity_per, scrap_pct=scrap_pct,
        )
        children.append(ChildDemand(component_item_id, gross, step))

    return OneLevelResult(children=children)
