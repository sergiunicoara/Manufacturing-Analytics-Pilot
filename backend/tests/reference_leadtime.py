"""FROZEN reference: the pandas-based lead-time implementation as it was before LeadTimeCalculator.
Kept only so tests can prove the indexed implementation returns identical numbers. Do not edit or use in app code."""
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
class ReferenceLeadTimeResult:
    item_id: int
    period_start_date: dt.date
    processing_days: float
    queue_days: float
    transfer_days: float
    total_days: float
    route_item_ids: tuple[int, ...] = ()


def _as_date(value) -> dt.date:
    return value.date() if isinstance(value, pd.Timestamp) else value


def reference_compute_lead_time_for_item(
    item_id: int,
    period_start_date: dt.date,
    routing_headers_df: pd.DataFrame,
    routing_operations_df: pd.DataFrame,
    wc_results_by_period: dict[tuple[int, dt.date], WorkCentrePeriodResult],
    items_df: pd.DataFrame | None = None,
    bom_headers_df: pd.DataFrame | None = None,
    bom_components_df: pd.DataFrame | None = None,
) -> ReferenceLeadTimeResult | None:
    items_by_id = items_df.set_index("item_id") if items_df is not None else None
    route_headers = routing_headers_df.copy()

    def route_metrics(route_item: int, qty_per_fg: float) -> tuple[float, float, float] | None:
        candidates = route_headers.loc[route_headers["item_id"] == route_item]
        if "effective_from" in candidates:
            candidates = candidates.loc[candidates["effective_from"].apply(_as_date) <= period_start_date]
            candidates = candidates.loc[candidates["effective_to"].apply(lambda v: pd.isna(v) or _as_date(v) >= period_start_date)]
        if candidates.empty:
            return (0.0, 0.0, 0.0)
        if len(candidates) > 1:
            return None
        routing_id = candidates.iloc[0]["routing_id"]
        ops = routing_operations_df.loc[routing_operations_df["routing_id"] == routing_id].sort_values("seq_no")
        if ops.empty:
            return None
        processing = queue = transfer = 0.0
        for _, op in ops.iterrows():
            if pd.isna(op["setup_time_minutes"]) or pd.isna(op["run_time_minutes_per_unit"]):
                return None
            batch = max(1, int(op["batch_size"]))
            qty = max(0.0, float(qty_per_fg))
            # Allocate setup across the routing batch, then scale standard
            # per-unit processing by this subassembly's BOM quantity.
            minutes = (float(op["setup_time_minutes"]) / batch
                       + float(op["run_time_minutes_per_unit"])) * qty
            processing += minutes / 1440.0
            transfer_minutes = 0.0 if pd.isna(op["transfer_time_minutes"]) else float(op["transfer_time_minutes"])
            transfer += transfer_minutes * max(qty, 1.0) / 1440.0
            # Routing standard wait is operation-specific; congestion is a
            # separate addition derived from the centre's entry backlog.
            queue_minutes = 0.0 if pd.isna(op["queue_time_minutes"]) else float(op["queue_time_minutes"])
            queue += queue_minutes / 1440.0
            wc_id = op["work_centre_id"]
            if pd.notna(wc_id):
                wc = wc_results_by_period.get((int(wc_id), period_start_date))
                if wc is not None and not math.isnan(wc.effective_hours_per_workday):
                    if wc.effective_hours_per_workday > 0:
                        operating_days = max(1, float(wc.effective_days_per_week))
                        wait_workdays = wc.backlog_hours_at_entry / wc.effective_hours_per_workday
                        queue += wait_workdays * 7.0 / operating_days
        return processing, queue, transfer

    def longest_path(node: int, qty: float, seen: frozenset[int]) -> tuple[float, float, float, tuple[int, ...]] | None:
        if node in seen:
            return None
        own = route_metrics(node, qty)
        if own is None:
            return None
        best_child = (0.0, 0.0, 0.0, ())
        if items_by_id is not None and bom_headers_df is not None and bom_components_df is not None:
            headers = active_bom_headers(node, period_start_date, bom_headers_df)
            if len(headers) > 1:
                return None
            if len(headers) == 1:
                bom_id = headers.iloc[0]["bom_id"]
                components = bom_components_df.loc[bom_components_df["bom_id"] == bom_id]
                for _, comp in components.iterrows():
                    child_id = int(comp["component_item_id"])
                    if child_id not in items_by_id.index or str(items_by_id.loc[child_id, "item_type"]) not in ("FG", "SUBASSY"):
                        continue
                    if pd.isna(comp["quantity_per"]) or pd.isna(comp["scrap_pct"]) or float(comp["scrap_pct"]) >= 1.0:
                        return None
                    child_qty = qty * float(comp["quantity_per"]) / max(1e-9, (1.0 - float(comp["scrap_pct"])))
                    candidate = longest_path(child_id, child_qty, seen | {node})
                    if candidate is None:
                        return None
                    if sum(candidate[:3]) > sum(best_child[:3]):
                        best_child = candidate
        return own[0] + best_child[0], own[1] + best_child[1], own[2] + best_child[2], (node,) + best_child[3]

    result = longest_path(int(item_id), 1.0, frozenset())
    if result is None:
        return None
    processing, queue, transfer, path = result
    return ReferenceLeadTimeResult(item_id, period_start_date, processing, queue, transfer,
                          processing + queue + transfer, path)
