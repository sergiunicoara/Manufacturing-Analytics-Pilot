"""Three-tier capacity [CORR-5]: calendar -> available -> effective ->
required -> utilization -> overload. Pure functions — capacity_calendar rows
are passed in, not queried here.
"""
from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass

import pandas as pd

from app.analytics.blocking import BlockingIndex


@dataclass
class CapacityResult:
    work_centre_id: int
    period_start_date: dt.date
    calendar_hours: float
    available_hours: float
    effective_hours: float
    required_hours: float
    utilization_pct: float  # NaN if effective_hours <= 0 (unreliable capacity data for this period)
    overload_hours: float  # NaN under the same condition
    excluded_routing_operation_ids: list[int]


def compute_required_hours_by_work_centre(
    production_qty_by_item: dict[int, float],
    routing_headers_df: pd.DataFrame,
    routing_operations_df: pd.DataFrame,
    blocking_index: BlockingIndex,
) -> tuple[dict[int, float], list[int]]:
    """Rough-cut capacity load: every routing operation for every item being
    produced this period loads its work centre in this same period (no
    operation-level time-offsetting — see period_engine.py's module
    docstring for why this simplification is documented and acceptable).
    A routing operation with a KPI_BLOCKING finding (missing time, missing
    work centre, invalid batch/yield) is excluded from the total — it does
    not block the item's other operations or any other item/work centre
    [CP3 req. 2].
    """
    routing_id_by_item = routing_headers_df.set_index("item_id")["routing_id"].to_dict()
    required: dict[int, float] = {}
    excluded: list[int] = []

    for item_id, qty in production_qty_by_item.items():
        if qty <= 0:
            continue
        routing_id = routing_id_by_item.get(item_id)
        if routing_id is None:
            continue
        ops = routing_operations_df.loc[routing_operations_df["routing_id"] == routing_id]
        for _, op in ops.iterrows():
            op_id = int(op["routing_operation_id"])
            if blocking_index.is_kpi_blocked("routing_operation", op_id):
                excluded.append(op_id)
                continue
            wc_id = op["work_centre_id"]
            if pd.isna(wc_id):
                excluded.append(op_id)
                continue
            setup = op["setup_time_minutes"]
            run = op["run_time_minutes_per_unit"]
            if pd.isna(setup) or pd.isna(run):
                excluded.append(op_id)
                continue
            batch_size = int(op["batch_size"])
            if batch_size <= 0:
                excluded.append(op_id)
                continue
            yield_pct = float(op["yield_pct"])
            if yield_pct <= 0:
                excluded.append(op_id)
                continue

            batches = math.ceil(qty / batch_size)
            minutes = setup * batches + run * (qty / yield_pct)
            hours = minutes / 60.0
            wc_id = int(wc_id)
            required[wc_id] = required.get(wc_id, 0.0) + hours

    return required, excluded


def compute_capacity(
    work_centre_id: int,
    period_start_date: dt.date,
    required_hours: float,
    capacity_calendar_df: pd.DataFrame,
    blocking_index: BlockingIndex,
    excluded_routing_operation_ids: list[int] | None = None,
    effective_hours_multiplier: float = 1.0,
) -> CapacityResult:
    """`effective_hours_multiplier` is the scenario capacity lever (e.g. "+30%
    welding capacity") — applied directly to this one work centre's effective
    hours rather than requiring the caller to copy/mutate the whole calendar
    DataFrame per period."""
    # Normalize before comparing: week_start_date may be plain datetime.date
    # (in-memory generator context) or pandas Timestamp (DB-loaded via
    # pd.read_sql_table) depending on the caller — `date == Timestamp` is
    # silently always False, so both sides are coerced to plain dates first.
    week_start_as_date = pd.to_datetime(capacity_calendar_df["week_start_date"]).dt.date
    row = capacity_calendar_df.loc[
        (capacity_calendar_df["work_centre_id"] == work_centre_id) & (week_start_as_date == period_start_date)
    ]
    if row.empty:
        return CapacityResult(
            work_centre_id=work_centre_id, period_start_date=period_start_date,
            calendar_hours=float("nan"), available_hours=float("nan"), effective_hours=float("nan"),
            required_hours=required_hours, utilization_pct=float("nan"), overload_hours=float("nan"),
            excluded_routing_operation_ids=excluded_routing_operation_ids or [],
        )

    r = row.iloc[0]
    calendar_hours = float(r["calendar_hours"])
    available_hours = float(r["available_hours"])
    effective_hours = float(r["effective_hours"]) * effective_hours_multiplier

    kpi_blocked = blocking_index.is_kpi_blocked("work_centre_period", f"{work_centre_id}:{period_start_date}")
    if effective_hours <= 0 or kpi_blocked:
        utilization_pct = float("nan")
        overload_hours = float("nan")
    else:
        utilization_pct = required_hours / effective_hours
        overload_hours = max(0.0, required_hours - effective_hours)

    return CapacityResult(
        work_centre_id=work_centre_id, period_start_date=period_start_date,
        calendar_hours=calendar_hours, available_hours=available_hours, effective_hours=effective_hours,
        required_hours=required_hours, utilization_pct=utilization_pct, overload_hours=overload_hours,
        excluded_routing_operation_ids=excluded_routing_operation_ids or [],
    )
