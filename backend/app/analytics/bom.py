"""Recursive multi-level BOM explosion with full lineage, effective-dated
revision selection, and cycle/orphan detection.

Pure functions only — no DB access. Callers pass in the three DataFrames
(items, bom_headers, bom_components) already loaded from wherever (DB,
in-memory generator context, or a hand-built test fixture).

Design decisions worth stating explicitly (see PLAN.md / CP2 report):
- A branch is never silently skipped. Orphan components, cycles, ambiguous
  (overlapping) active revisions, and invalid scrap percentages all produce
  an ExplosionLine with `incomplete=True` and a `blocked_reason`, and that
  quantity is excluded from `aggregated_requirements` rather than guessed.
- Overlapping active revisions are NOT arbitrarily tie-broken by this
  function — per the CP2 requirement not to silently pick one, an ambiguous
  BOM is treated as a blocked branch, not resolved by a hidden default rule.
"""
from __future__ import annotations

import datetime as dt

import pandas as pd

from app.analytics.models import ExplosionLine, ExplosionResult, LineageStep

MAX_EXPLOSION_DEPTH = 30  # defensive backstop, well above the spec's 3-9 level range


def active_bom_headers(
    item_id: int, as_of_date: dt.date, bom_headers_df: pd.DataFrame
) -> pd.DataFrame:
    """BOM header rows for `item_id` whose effective window covers
    `as_of_date`. Zero rows means "no active BOM" (a leaf/purchased item, or
    a manufactured item with a genuine data gap — the caller decides which).
    More than one row means an ambiguous/overlapping revision."""
    candidates = bom_headers_df.loc[bom_headers_df["parent_item_id"] == item_id]
    if candidates.empty:
        return candidates
    mask = candidates["effective_from"].apply(_as_date) <= as_of_date
    mask &= candidates["effective_to"].apply(
        lambda v: True if pd.isna(v) else _as_date(v) >= as_of_date
    )
    return candidates.loc[mask]


def _as_date(value) -> dt.date:
    if isinstance(value, dt.date) and not isinstance(value, dt.datetime):
        return value
    return pd.to_datetime(value).date()


def explode(
    item_id: int,
    qty: float,
    as_of_date: dt.date,
    items_df: pd.DataFrame,
    bom_headers_df: pd.DataFrame,
    bom_components_df: pd.DataFrame,
) -> ExplosionResult:
    items_by_id = items_df.set_index("item_id")
    top_code = str(items_by_id.loc[item_id, "item_code"]) if item_id in items_by_id.index else "UNKNOWN"

    result = ExplosionResult(top_item_id=item_id, top_item_code=top_code, top_qty=qty, as_of_date=as_of_date)

    _explode_node(
        item_id=item_id,
        cumulative_qty_per_unit=1.0,
        path=[],
        ancestors=frozenset({item_id}),
        items_by_id=items_by_id,
        bom_headers_df=bom_headers_df,
        bom_components_df=bom_components_df,
        as_of_date=as_of_date,
        top_qty=qty,
        lines_out=result.lines,
        depth=0,
    )

    for line in result.lines:
        if line.incomplete:
            continue
        result.aggregated_requirements[line.component_item_id] = (
            result.aggregated_requirements.get(line.component_item_id, 0.0) + line.gross_requirement
        )

    return result


def _explode_node(
    item_id: int,
    cumulative_qty_per_unit: float,
    path: list[LineageStep],
    ancestors: frozenset[int],
    items_by_id: pd.DataFrame,
    bom_headers_df: pd.DataFrame,
    bom_components_df: pd.DataFrame,
    as_of_date: dt.date,
    top_qty: float,
    lines_out: list[ExplosionLine],
    depth: int,
) -> None:
    if depth > MAX_EXPLOSION_DEPTH:
        lines_out.append(_blocked_line(item_id, items_by_id, path, cumulative_qty_per_unit, top_qty, "max_depth_exceeded"))
        return

    headers = active_bom_headers(item_id, as_of_date, bom_headers_df)
    if headers.empty:
        # No BOM at this effective date: either a genuine leaf (raw/packaging,
        # or a manufactured item outside its BOM's effective window — the
        # latter is a real data concern, but explode() doesn't have enough
        # context to distinguish "never had a BOM" from "BOM expired"; the
        # DQ engine's own rules cover that separately. explode() just stops.
        return
    if len(headers) > 1:
        lines_out.append(
            _blocked_line(item_id, items_by_id, path, cumulative_qty_per_unit, top_qty, "ambiguous_bom_revision")
        )
        return

    bom_row = headers.iloc[0]
    components = bom_components_df.loc[bom_components_df["bom_id"] == bom_row["bom_id"]]

    for _, comp in components.iterrows():
        component_item_id = int(comp["component_item_id"])
        scrap_pct = float(comp["scrap_pct"])
        quantity_per = float(comp["quantity_per"])

        parent_code = str(items_by_id.loc[item_id, "item_code"]) if item_id in items_by_id.index else "UNKNOWN"
        step = LineageStep(
            parent_item_id=item_id,
            parent_item_code=parent_code,
            bom_id=int(bom_row["bom_id"]),
            bom_component_id=int(comp["bom_component_id"]),
            revision=str(bom_row["revision"]),
            effective_from=_as_date(bom_row["effective_from"]),
            effective_to=None if pd.isna(bom_row["effective_to"]) else _as_date(bom_row["effective_to"]),
            quantity_per=quantity_per,
            scrap_pct=scrap_pct,
        )
        new_path = path + [step]

        if component_item_id not in items_by_id.index:
            lines_out.append(
                ExplosionLine(
                    component_item_id=component_item_id,
                    component_item_code="UNKNOWN",
                    component_item_type=None,
                    path=new_path,
                    cumulative_quantity_per_unit=0.0,
                    gross_requirement=0.0,
                    incomplete=True,
                    blocked_reason="orphan_component",
                )
            )
            continue

        if scrap_pct >= 1.0 or scrap_pct < 0:
            lines_out.append(
                ExplosionLine(
                    component_item_id=component_item_id,
                    component_item_code=str(items_by_id.loc[component_item_id, "item_code"]),
                    component_item_type=str(items_by_id.loc[component_item_id, "item_type"]),
                    path=new_path,
                    cumulative_quantity_per_unit=0.0,
                    gross_requirement=0.0,
                    incomplete=True,
                    blocked_reason="invalid_scrap_pct",
                )
            )
            continue

        effective_multiplier = quantity_per / (1.0 - scrap_pct)
        new_cumulative = cumulative_qty_per_unit * effective_multiplier

        if component_item_id in ancestors:
            lines_out.append(
                ExplosionLine(
                    component_item_id=component_item_id,
                    component_item_code=str(items_by_id.loc[component_item_id, "item_code"]),
                    component_item_type=str(items_by_id.loc[component_item_id, "item_type"]),
                    path=new_path,
                    cumulative_quantity_per_unit=new_cumulative,
                    gross_requirement=new_cumulative * top_qty,
                    incomplete=True,
                    blocked_reason="cycle_detected",
                )
            )
            continue

        lines_out.append(
            ExplosionLine(
                component_item_id=component_item_id,
                component_item_code=str(items_by_id.loc[component_item_id, "item_code"]),
                component_item_type=str(items_by_id.loc[component_item_id, "item_type"]),
                path=new_path,
                cumulative_quantity_per_unit=new_cumulative,
                gross_requirement=new_cumulative * top_qty,
                incomplete=False,
                blocked_reason=None,
            )
        )

        _explode_node(
            item_id=component_item_id,
            cumulative_qty_per_unit=new_cumulative,
            path=new_path,
            ancestors=ancestors | {component_item_id},
            items_by_id=items_by_id,
            bom_headers_df=bom_headers_df,
            bom_components_df=bom_components_df,
            as_of_date=as_of_date,
            top_qty=top_qty,
            lines_out=lines_out,
            depth=depth + 1,
        )


def _blocked_line(
    item_id: int,
    items_by_id: pd.DataFrame,
    path: list[LineageStep],
    cumulative_qty_per_unit: float,
    top_qty: float,
    reason: str,
) -> ExplosionLine:
    code = str(items_by_id.loc[item_id, "item_code"]) if item_id in items_by_id.index else "UNKNOWN"
    item_type = str(items_by_id.loc[item_id, "item_type"]) if item_id in items_by_id.index else None
    return ExplosionLine(
        component_item_id=item_id,
        component_item_code=code,
        component_item_type=item_type,
        path=path,
        cumulative_quantity_per_unit=cumulative_qty_per_unit,
        gross_requirement=cumulative_qty_per_unit * top_qty,
        incomplete=True,
        blocked_reason=reason,
    )


# --- Full-graph structural checks, shared by the DQ rule engine ------------


def find_orphan_bom_components(items_df: pd.DataFrame, bom_components_df: pd.DataFrame) -> pd.DataFrame:
    valid_ids = set(items_df["item_id"])
    return bom_components_df.loc[~bom_components_df["component_item_id"].isin(valid_ids)]


def find_overlapping_bom_revisions(bom_headers_df: pd.DataFrame) -> list[tuple[int, int, int]]:
    """Returns (parent_item_id, bom_id_a, bom_id_b) for every pair of BOM
    headers on the same parent item whose effective windows overlap."""
    overlaps: list[tuple[int, int, int]] = []
    for parent_item_id, group in bom_headers_df.groupby("parent_item_id"):
        if len(group) < 2:
            continue
        rows = group.to_dict("records")
        for i in range(len(rows)):
            for j in range(i + 1, len(rows)):
                a, b = rows[i], rows[j]
                a_from, a_to = _as_date(a["effective_from"]), _open_ended_max(a["effective_to"])
                b_from, b_to = _as_date(b["effective_from"]), _open_ended_max(b["effective_to"])
                if a_from <= b_to and b_from <= a_to:
                    overlaps.append((int(parent_item_id), int(a["bom_id"]), int(b["bom_id"])))
    return overlaps


def _open_ended_max(value) -> dt.date:
    if pd.isna(value):
        return dt.date.max
    return _as_date(value)


def find_bom_cycles(items_df: pd.DataFrame, bom_headers_df: pd.DataFrame, bom_components_df: pd.DataFrame) -> list[list[int]]:
    """Structural cycle detection over the whole BOM graph (ignores
    effective-dating — a cycle in the structure is a defect regardless of
    which revision is active on any given date). Returns one path per
    distinct cycle found, as a list of item_ids."""
    valid_ids = set(items_df["item_id"])
    bom_id_to_parent = bom_headers_df.set_index("bom_id")["parent_item_id"].to_dict()

    children: dict[int, list[int]] = {}
    for _, comp in bom_components_df.iterrows():
        comp_item_id = int(comp["component_item_id"])
        if comp_item_id not in valid_ids:
            continue  # orphan, handled by its own rule
        parent_item_id = bom_id_to_parent.get(comp["bom_id"])
        if parent_item_id is None:
            continue
        children.setdefault(int(parent_item_id), []).append(comp_item_id)

    cycles: list[list[int]] = []
    visited_global: set[int] = set()

    def dfs(node: int, stack: list[int], on_stack: set[int]) -> None:
        if node in on_stack:
            cycle_start = stack.index(node)
            cycles.append(stack[cycle_start:] + [node])
            return
        if node in visited_global:
            return
        visited_global.add(node)
        stack.append(node)
        on_stack.add(node)
        for child in children.get(node, []):
            dfs(child, stack, on_stack)
        stack.pop()
        on_stack.discard(node)

    for item_id in valid_ids:
        if item_id not in visited_global:
            dfs(item_id, [], set())

    return cycles
