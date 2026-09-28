"""The period-stepped simulation core [CORR-2/7]. Steps through weekly
periods, carrying state (backlog-hours per work centre, on-hand inventory
per item, cumulative-backlog/overload-streak per work centre for constraint
classification) from period t to period t+1. This is what CP2's static,
single-point-in-time netting demo is upgraded into for CP3: every run here
produces a full weekly time series, not a snapshot.

Scope decisions, documented rather than silently assumed:

1. **Rough-Cut Capacity Planning (RCCP), not finite scheduling.** An item's
   full routing load lands in the *same* week its net requirement is due —
   operations are not offset backward by their own lead time. This is a
   standard simplification in capacity-planning tools; a truly finite,
   operation-by-operation schedule is out of scope for this pilot.
2. **Single-level netting per item per period, not full low-level-coded
   MRP.** Each item (FG/SUBASSY/RAW/PACKAGING) is netted against its own
   carried-forward inventory and scheduled receipts every period. A
   subassembly's on-hand inventory does *not* cascade to reduce its own
   components' gross requirement — that would require full recursive
   low-level-coded MRP (processing every item in strict level order,
   accumulating multi-parent demand before netting once). Gross requirement
   for every node is still computed by CP2's full BOM explosion, which
   already correctly aggregates a shared component's demand across every
   path that uses it; only the *netting offset* (inventory/receipts) is now
   period-stateful. This is a real simplification versus a production MRP
   system, and is called out again in ASSUMPTIONS.md.
3. **Unified queue/backlog formula, replacing the two-regime split sketched
   in PLAN.md's original CORR-1.** The original design used a bounded delay
   curve below a utilization threshold and switched to backlog accumulation
   above it. Working through CP3 req. 8 (verify continuity at the
   threshold) showed those two formulas measure genuinely different things
   near the seam — a steady-state expected wait (which diverges as
   utilization approaches 1) versus a freshly-accumulating backlog (which
   starts at ~0 right when utilization crosses 1) — and cannot be made
   continuous by construction; there will always be a seam-side value near
   ~0 meeting a seam-side value near infinity. The fix is to drop the
   piecewise split entirely: backlog accumulates every period, at every
   utilization level, via the same formula:

       backlog_hours_end(t) = max(0, backlog_hours_start(t) + required(t) - effective(t))
       queue_time_days(t) = base_queue_time_days + backlog_hours_end(t) / (effective(t) / 7)

   This is continuous everywhere (there is no seam), never diverges, never
   goes negative, and is still fully stateful: below saturation, backlog
   drains back toward 0 over successive periods; at/above saturation, it
   accumulates without bound — the same qualitative behavior CORR-1 wanted,
   without an artificial threshold. `base_queue_time_days` (a per-work-centre
   constant derived from that work centre's routing_operations.queue_time_minutes)
   represents the inherent minimum handling/transfer queueing that exists
   even at low utilization.
"""
from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass, field

import pandas as pd

from app.analytics.blocking import BlockingIndex
from app.analytics.bom import explode
from app.analytics.capacity import compute_capacity, compute_required_hours_by_work_centre
from app.analytics.constraint import ConstraintState, select_primary_constraint, step_work_centre
from app.analytics.netting import net_requirement
from app.config import settings


@dataclass
class WorkCentrePeriodResult:
    work_centre_id: int
    period_start_date: dt.date
    calendar_hours: float
    available_hours: float
    effective_hours: float
    required_hours: float
    utilization_pct: float
    backlog_hours_start: float
    backlog_hours_end: float
    wip_qty: float
    queue_time_days: float
    constraint_classification: str
    excluded_routing_operation_ids: list[int] = field(default_factory=list)


@dataclass
class MaterialRequirementPeriodResult:
    item_id: int
    item_code: str
    period_start_date: dt.date
    gross_requirement: float
    usable_inventory: float
    scheduled_receipts: float
    net_requirement: float
    shortage_flag: bool
    provenance: str = "DERIVED"


@dataclass
class PeriodEngineOutput:
    work_centre_results: list[WorkCentrePeriodResult] = field(default_factory=list)
    material_results: list[MaterialRequirementPeriodResult] = field(default_factory=list)

    def wc_series(self, work_centre_id: int) -> list[WorkCentrePeriodResult]:
        return sorted(
            (r for r in self.work_centre_results if r.work_centre_id == work_centre_id),
            key=lambda r: r.period_start_date,
        )

    def material_series(self, item_id: int) -> list[MaterialRequirementPeriodResult]:
        return sorted(
            (r for r in self.material_results if r.item_id == item_id),
            key=lambda r: r.period_start_date,
        )


def _base_queue_time_days_by_work_centre(routing_operations_df: pd.DataFrame) -> dict[int, float]:
    grp = routing_operations_df.dropna(subset=["work_centre_id"]).groupby("work_centre_id")["queue_time_minutes"].mean()
    return {int(wc): float(minutes) / 1440.0 for wc, minutes in grp.items()}


def _avg_minutes_per_unit_by_work_centre(routing_operations_df: pd.DataFrame) -> dict[int, float]:
    grp = routing_operations_df.dropna(subset=["work_centre_id", "run_time_minutes_per_unit"]).groupby("work_centre_id")[
        "run_time_minutes_per_unit"
    ].mean()
    return {int(wc): float(minutes) for wc, minutes in grp.items() if minutes > 0}


def _bucket_receipts_by_item_period(
    purchase_order_lines_df: pd.DataFrame, horizon: list[dt.date]
) -> dict[tuple[int, dt.date], float]:
    if purchase_order_lines_df.empty:
        return {}
    df = purchase_order_lines_df.loc[purchase_order_lines_df["status"].isin(["OPEN", "PARTIAL"])].copy()
    df["remaining_qty"] = (df["qty_ordered"] - df["qty_received"]).clip(lower=0)
    df["expected_receipt_date"] = pd.to_datetime(df["expected_receipt_date"]).dt.date

    bucketed: dict[tuple[int, dt.date], float] = {}
    horizon_sorted = sorted(horizon)
    for _, row in df.iterrows():
        receipt_date = row["expected_receipt_date"]
        # Assign to exactly one period bucket: the last period whose start
        # is <= the receipt date (a receipt is available from that week on).
        period = None
        for p in horizon_sorted:
            if p <= receipt_date:
                period = p
            else:
                break
        if period is None:
            continue
        key = (int(row["item_id"]), period)
        bucketed[key] = bucketed.get(key, 0.0) + float(row["remaining_qty"])
    return bucketed


def run_period_engine(
    horizon: list[dt.date],
    top_level_demand: dict[dt.date, dict[int, float]],
    items_df: pd.DataFrame,
    bom_headers_df: pd.DataFrame,
    bom_components_df: pd.DataFrame,
    routing_headers_df: pd.DataFrame,
    routing_operations_df: pd.DataFrame,
    work_centres_df: pd.DataFrame,
    capacity_calendar_df: pd.DataFrame,
    purchase_order_lines_df: pd.DataFrame,
    initial_inventory: dict[int, float],
    blocking_index: BlockingIndex,
    demand_multiplier_by_item: dict[int, float] | None = None,
    capacity_multiplier_by_work_centre: dict[int, float] | None = None,
    min_consecutive_periods: int | None = None,
) -> PeriodEngineOutput:
    demand_multiplier_by_item = demand_multiplier_by_item or {}
    capacity_multiplier_by_work_centre = capacity_multiplier_by_work_centre or {}
    min_consecutive = min_consecutive_periods or settings.candidate_constraint_min_consecutive_periods

    items_by_id = items_df.set_index("item_id")
    process_type_by_wc = work_centres_df.set_index("work_centre_id")["process_type"].to_dict()
    base_queue_days = _base_queue_time_days_by_work_centre(routing_operations_df)
    avg_minutes_per_unit = _avg_minutes_per_unit_by_work_centre(routing_operations_df)
    receipts_by_item_period = _bucket_receipts_by_item_period(purchase_order_lines_df, horizon)

    inventory_state: dict[int, float] = dict(initial_inventory)
    backlog_state: dict[int, float] = {int(wc): 0.0 for wc in work_centres_df["work_centre_id"]}
    constraint_state: dict[int, ConstraintState] = {int(wc): ConstraintState() for wc in work_centres_df["work_centre_id"]}

    output = PeriodEngineOutput()

    for period in sorted(horizon):
        period_demand = top_level_demand.get(period, {})

        # --- 1. BOM explosion: aggregate gross requirement for every node
        #        (FG items themselves + every component at every level). ---
        gross_by_item: dict[int, float] = {}
        for item_id, qty in period_demand.items():
            qty = qty * demand_multiplier_by_item.get(item_id, 1.0)
            if qty <= 0:
                continue
            gross_by_item[item_id] = gross_by_item.get(item_id, 0.0) + qty
            if blocking_index.is_entity_blocked("item", item_id):
                continue  # BOM branch blocked (orphan/cycle/ambiguous revision/etc.) — do not explode further
            result = explode(item_id, qty, period, items_df, bom_headers_df, bom_components_df)
            for comp_id, comp_qty in result.aggregated_requirements.items():
                gross_by_item[comp_id] = gross_by_item.get(comp_id, 0.0) + comp_qty

        # --- 2. Netting, period-stateful [CP3 req. 1]. ---
        net_by_item: dict[int, float] = {}
        for item_id, gross in gross_by_item.items():
            item_code = str(items_by_id.loc[item_id, "item_code"]) if item_id in items_by_id.index else "UNKNOWN"
            usable = 0.0 if blocking_index.is_entity_blocked("item", item_id) else inventory_state.get(item_id, 0.0)
            receipts = receipts_by_item_period.get((item_id, period), 0.0)

            inv_rows = pd.DataFrame([{"on_hand_qty": usable, "is_blocked": False}]) if usable != 0 else pd.DataFrame(
                columns=["on_hand_qty", "is_blocked"]
            )
            po_rows = pd.DataFrame([{
                "qty_ordered": receipts, "qty_received": 0.0, "expected_receipt_date": period, "status": "OPEN",
            }]) if receipts > 0 else pd.DataFrame(columns=["qty_ordered", "qty_received", "expected_receipt_date", "status"])

            result = net_requirement(item_id, item_code, gross, period, inv_rows, po_rows)
            net_by_item[item_id] = result.net_requirement

            available = usable + receipts
            consumed = min(available, gross)
            inventory_state[item_id] = max(0.0, available - consumed)

            output.material_results.append(MaterialRequirementPeriodResult(
                item_id=item_id, item_code=item_code, period_start_date=period,
                gross_requirement=result.gross_requirement, usable_inventory=result.usable_inventory,
                scheduled_receipts=result.scheduled_receipts, net_requirement=result.net_requirement,
                shortage_flag=result.shortage_quantity > 0,
            ))

        # --- 3. Routing load: only manufactured items' NET requirement
        #        drives work-centre hours (inventory already on hand does
        #        not need to be re-produced this period). ---
        production_qty_by_item = {
            item_id: qty for item_id, qty in net_by_item.items()
            if item_id in items_by_id.index and items_by_id.loc[item_id, "item_type"] in ("FG", "SUBASSY")
        }
        required_by_wc, excluded_ops = compute_required_hours_by_work_centre(
            production_qty_by_item, routing_headers_df, routing_operations_df, blocking_index
        )

        # --- 4-8. Per work centre: capacity tiers, backlog/queue, WIP, classification. ---
        classifications_this_period: dict[int, str] = {}
        for wc_id in work_centres_df["work_centre_id"]:
            wc_id = int(wc_id)
            required_hours = required_by_wc.get(wc_id, 0.0)
            multiplier = capacity_multiplier_by_work_centre.get(wc_id, 1.0)

            cap = compute_capacity(
                wc_id, period, required_hours, capacity_calendar_df, blocking_index, excluded_ops,
                effective_hours_multiplier=multiplier,
            )

            backlog_start = backlog_state[wc_id]
            # Gate on utilization_pct (NaN whenever compute_capacity judged
            # this period's capacity unreliable — no calendar row, OR a real
            # effective_hours of exactly 0 from a zero_capacity_weeks
            # KPI_BLOCKING finding), not on effective_hours directly: a
            # blocked-but-nonnegative-looking 0.0 is not NaN and would
            # otherwise divide by zero below.
            if math.isnan(cap.utilization_pct):
                backlog_end = backlog_start  # unreliable capacity data this period — carry state, don't corrupt it
                queue_time_days = float("nan")
            else:
                backlog_end = max(0.0, backlog_start + required_hours - cap.effective_hours)
                queue_time_days = base_queue_days.get(wc_id, 0.0) + backlog_end / (cap.effective_hours / 7.0)
            backlog_state[wc_id] = backlog_end

            minutes_per_unit = avg_minutes_per_unit.get(wc_id)
            wip_qty = (backlog_end * 60.0 / minutes_per_unit) if minutes_per_unit else 0.0

            classification, new_state = step_work_centre(cap.utilization_pct, backlog_end, constraint_state[wc_id], min_consecutive)
            constraint_state[wc_id] = new_state
            classifications_this_period[wc_id] = classification

            output.work_centre_results.append(WorkCentrePeriodResult(
                work_centre_id=wc_id, period_start_date=period,
                calendar_hours=cap.calendar_hours, available_hours=cap.available_hours,
                effective_hours=cap.effective_hours, required_hours=cap.required_hours,
                utilization_pct=cap.utilization_pct, backlog_hours_start=backlog_start,
                backlog_hours_end=backlog_end, wip_qty=wip_qty, queue_time_days=queue_time_days,
                constraint_classification=classification, excluded_routing_operation_ids=cap.excluded_routing_operation_ids,
            ))

        primary = select_primary_constraint(classifications_this_period, constraint_state, process_type_by_wc)
        if primary is not None:
            for r in output.work_centre_results:
                if r.period_start_date == period and r.work_centre_id == primary:
                    r.constraint_classification = "PRIMARY_CONSTRAINT"

    return output


@dataclass
class LeadTimeResult:
    item_id: int
    period_start_date: dt.date
    processing_days: float
    queue_days: float
    transfer_days: float
    total_days: float


def compute_lead_time_for_item(
    item_id: int,
    period_start_date: dt.date,
    routing_headers_df: pd.DataFrame,
    routing_operations_df: pd.DataFrame,
    wc_results_by_period: dict[tuple[int, dt.date], WorkCentrePeriodResult],
) -> LeadTimeResult | None:
    """Sums processing + queue + transfer time across an item's routing
    sequence for one period, using that period's actual queue_time_days per
    work centre from the engine output — not a separate static estimate."""
    routing_row = routing_headers_df.loc[routing_headers_df["item_id"] == item_id]
    if routing_row.empty:
        return None
    routing_id = routing_row.iloc[0]["routing_id"]
    ops = routing_operations_df.loc[routing_operations_df["routing_id"] == routing_id].sort_values("seq_no")

    processing_days = queue_days = transfer_days = 0.0
    for _, op in ops.iterrows():
        setup = op["setup_time_minutes"] or 0.0
        run = op["run_time_minutes_per_unit"] or 0.0
        batch = max(1, int(op["batch_size"]))
        processing_minutes = (setup / batch) + run
        processing_days += processing_minutes / 1440.0
        transfer_days += (op["transfer_time_minutes"] or 0.0) / 1440.0

        wc_id = op["work_centre_id"]
        if pd.notna(wc_id):
            wc_result = wc_results_by_period.get((int(wc_id), period_start_date))
            if wc_result is not None and not math.isnan(wc_result.queue_time_days):
                queue_days += wc_result.queue_time_days

    return LeadTimeResult(
        item_id=item_id, period_start_date=period_start_date,
        processing_days=processing_days, queue_days=queue_days, transfer_days=transfer_days,
        total_days=processing_days + queue_days + transfer_days,
    )
