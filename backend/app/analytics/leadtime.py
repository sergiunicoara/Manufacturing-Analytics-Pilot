"""Routing and BOM-path lead-time estimates from period-engine state.

For a finished good, elapsed routing time is its own route plus the longest
manufactured-subassembly branch. Queue congestion is measured from backlog
already present when the order enters each centre. Backlog capacity is in
operating workdays and is converted to calendar days before aggregation.
"""
from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass

import pandas as pd

from app.analytics.bom import active_bom_headers
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.analytics.period_engine import WorkCentrePeriodResult


@dataclass
class LeadTimeResult:
    item_id: int
    period_start_date: dt.date
    processing_days: float
    queue_days: float
    transfer_days: float
    total_days: float
    route_item_ids: tuple[int, ...] = ()


def _as_date(value) -> dt.date:
    return value.date() if isinstance(value, pd.Timestamp) else value


def _bom_date(value) -> dt.date:
    """Same conversion as bom.active_bom_headers so effective-dating stays identical."""
    if isinstance(value, dt.date) and not isinstance(value, dt.datetime):
        return value
    return pd.to_datetime(value).date()


class LeadTimeCalculator:
    """Indexes routing and BOM tables once, so a lead time is dictionary lookups plus arithmetic instead of
    repeated pandas filtering. Build one per set of tables and reuse it across items, periods and scenarios:
    the route structure does not depend on the scenario, only the queue term (period-entry backlog) does."""

    def __init__(self, routing_headers_df: pd.DataFrame, routing_operations_df: pd.DataFrame,
                 items_df: pd.DataFrame | None = None, bom_headers_df: pd.DataFrame | None = None,
                 bom_components_df: pd.DataFrame | None = None):
        dated = "effective_from" in routing_headers_df.columns
        self._route_candidates: dict[int, list[tuple]] = {}
        for row in routing_headers_df.itertuples(index=False):
            start = _as_date(row.effective_from) if dated else None
            end = None if not dated or pd.isna(row.effective_to) else _as_date(row.effective_to)
            self._route_candidates.setdefault(row.item_id, []).append((row.routing_id, start, end))
        self._dated_routes = dated

        self._route_ops: dict = {}
        for routing_id, group in routing_operations_df.groupby("routing_id"):
            ops = []
            for _, op in group.sort_values("seq_no").iterrows():
                invalid = pd.isna(op["setup_time_minutes"]) or pd.isna(op["run_time_minutes_per_unit"])
                ops.append(None if invalid else (
                    max(1, int(op["batch_size"])), float(op["setup_time_minutes"]),
                    float(op["run_time_minutes_per_unit"]),
                    0.0 if pd.isna(op["transfer_time_minutes"]) else float(op["transfer_time_minutes"]),
                    0.0 if pd.isna(op["queue_time_minutes"]) else float(op["queue_time_minutes"]),
                    int(op["work_centre_id"]) if pd.notna(op["work_centre_id"]) else None))
            self._route_ops[routing_id] = ops

        self._use_bom = items_df is not None and bom_headers_df is not None and bom_components_df is not None
        self._bom_headers: dict[int, list[tuple]] = {}
        self._bom_children: dict = {}
        if self._use_bom:
            item_type = {int(i): str(t) for i, t in zip(items_df["item_id"], items_df["item_type"])}
            for row in bom_headers_df.itertuples(index=False):
                end = None if pd.isna(row.effective_to) else _bom_date(row.effective_to)
                self._bom_headers.setdefault(row.parent_item_id, []).append(
                    (row.bom_id, _bom_date(row.effective_from), end))
            for row in bom_components_df.itertuples(index=False):
                child = int(row.component_item_id)
                if item_type.get(child) not in ("FG", "SUBASSY"):
                    continue
                invalid = pd.isna(row.quantity_per) or pd.isna(row.scrap_pct) or float(row.scrap_pct) >= 1.0
                self._bom_children.setdefault(row.bom_id, []).append(
                    (child, None if invalid else (float(row.quantity_per), float(row.scrap_pct))))

    def _route_metrics(self, route_item: int, qty_per_fg: float, period: dt.date,
                       wc_results_by_period: dict) -> tuple[float, float, float] | None:
        candidates = self._route_candidates.get(route_item, [])
        if self._dated_routes:
            candidates = [c for c in candidates if c[1] <= period and (c[2] is None or c[2] >= period)]
        if not candidates:
            return (0.0, 0.0, 0.0)
        if len(candidates) > 1:
            return None
        ops = self._route_ops.get(candidates[0][0], [])
        if not ops:
            return None
        processing = queue = transfer = 0.0
        for op in ops:
            if op is None:
                return None
            batch, setup, run, transfer_minutes, queue_minutes, wc_id = op
            qty = max(0.0, float(qty_per_fg))
            # Allocate setup across the routing batch, then scale standard per-unit processing by this
            # subassembly's BOM quantity.
            minutes = (setup / batch + run) * qty
            processing += minutes / 1440.0
            transfer += transfer_minutes * max(qty, 1.0) / 1440.0
            # Routing standard wait is operation-specific; congestion is a separate addition derived from
            # the centre's entry backlog.
            queue += queue_minutes / 1440.0
            if wc_id is not None:
                wc = wc_results_by_period.get((wc_id, period))
                # No usable capacity figure for a centre on the route means the wait is unknown, not zero:
                # report the route as unavailable rather than an optimistic finite lead time.
                if (wc is None or math.isnan(wc.effective_hours_per_workday) or wc.effective_hours_per_workday <= 0
                        or math.isnan(wc.backlog_hours_at_entry)):
                    return None
                operating_days = max(1, float(wc.effective_days_per_week))
                wait_workdays = wc.backlog_hours_at_entry / wc.effective_hours_per_workday
                queue += wait_workdays * 7.0 / operating_days
        return processing, queue, transfer

    def _longest_path(self, node: int, qty: float, seen: frozenset, period: dt.date,
                      wc_results_by_period: dict) -> tuple[float, float, float, tuple[int, ...]] | None:
        if node in seen:
            return None
        own = self._route_metrics(node, qty, period, wc_results_by_period)
        if own is None:
            return None
        best_child = (0.0, 0.0, 0.0, ())
        if self._use_bom:
            headers = [h for h in self._bom_headers.get(node, [])
                       if h[1] <= period and (h[2] is None or h[2] >= period)]
            if len(headers) > 1:
                return None
            if len(headers) == 1:
                for child_id, factors in self._bom_children.get(headers[0][0], []):
                    if factors is None:
                        return None
                    quantity_per, scrap_pct = factors
                    child_qty = qty * quantity_per / max(1e-9, (1.0 - scrap_pct))
                    candidate = self._longest_path(child_id, child_qty, seen | {node}, period, wc_results_by_period)
                    if candidate is None:
                        return None
                    if sum(candidate[:3]) > sum(best_child[:3]):
                        best_child = candidate
        return own[0] + best_child[0], own[1] + best_child[1], own[2] + best_child[2], (node,) + best_child[3]

    def compute(self, item_id: int, period_start_date: dt.date,
                wc_results_by_period: dict[tuple[int, dt.date], WorkCentrePeriodResult]) -> LeadTimeResult | None:
        result = self._longest_path(int(item_id), 1.0, frozenset(), period_start_date, wc_results_by_period)
        if result is None:
            return None
        processing, queue, transfer, path = result
        return LeadTimeResult(item_id, period_start_date, processing, queue, transfer,
                              processing + queue + transfer, path)


def compute_lead_time_for_item(
    item_id: int,
    period_start_date: dt.date,
    routing_headers_df: pd.DataFrame,
    routing_operations_df: pd.DataFrame,
    wc_results_by_period: dict[tuple[int, dt.date], WorkCentrePeriodResult],
    items_df: pd.DataFrame | None = None,
    bom_headers_df: pd.DataFrame | None = None,
    bom_components_df: pd.DataFrame | None = None,
) -> LeadTimeResult | None:
    """Single-call convenience wrapper. It re-indexes the tables on every call; loops over many items or
    periods should build one LeadTimeCalculator and reuse it."""
    return LeadTimeCalculator(routing_headers_df, routing_operations_df, items_df, bom_headers_df,
                              bom_components_df).compute(item_id, period_start_date, wc_results_by_period)
