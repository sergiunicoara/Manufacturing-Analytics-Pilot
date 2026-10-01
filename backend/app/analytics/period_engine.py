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
2. **Cascading multi-level MRP netting [CP3.1 req. A]**, superseding CP3's
   original single-level-per-period limitation. Items are netted in
   ascending low-level-code order (app.analytics.mrp.compute_low_level_codes)
   so a parent's NET requirement — not its gross — is what explodes one
   level down (app.analytics.mrp.explode_one_level), and a shared component
   accumulates demand from every parent that uses it, at any depth, before
   being netted exactly once. A subassembly's on-hand inventory therefore
   *does* reduce its own components' requirement: 100 units of a parent
   needing 1 FRAME each, with 80 FRAME already on hand, nets to a FRAME
   requirement of 20 — and only 20 FRAME's worth of demand cascades further
   down into FRAME's own components. This replaces CP3's full-gross-then-net
   approach (which computed component demand as if every level were always
   built from scratch) and is the direct fix for CP3's "known calibration
   gap" note about overstated plant-wide load.
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
from app.analytics.capacity import CapacityCalendar, RoutingLoad
from app.analytics.constraint import ConstraintState, select_primary_constraint, step_work_centre
from app.analytics.mrp import BomExploder, compute_low_level_codes
from app.analytics.netting import net_single_position
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
    completed_hours: float  # [CP3.2] hours of work actually processed this period -- see reconciliation.py
    wip_qty: float
    queue_time_days: float
    backlog_hours_at_entry: float
    effective_hours_per_workday: float
    effective_days_per_week: float
    base_queue_time_days: float
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
    # Carried inventory state at the end of each period, including items with no requirement that week.
    inventory_end_by_period: dict[dt.date, dict[int, float]] = field(default_factory=dict)

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


PERIOD_DAYS = 7   # the engine steps in weekly periods


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
        # is <= the receipt date (a receipt is available from that week on);
        # past-due receipts go to the first week, receipts after the horizon are dropped.
        period = None
        for p in horizon_sorted:
            if p <= receipt_date:
                period = p
            else:
                break
        if period is None:
            # Past due: an open line whose expected date is already behind us is still expected, so it is
            # available from the first modelled week (standard MRP treatment of past-due scheduled receipts).
            period = horizon_sorted[0]
        if receipt_date >= horizon_sorted[-1] + dt.timedelta(days=PERIOD_DAYS):
            continue  # arrives after the last modelled week: it cannot cover demand inside the horizon
        key = (int(row["item_id"]), period)
        bucketed[key] = bucketed.get(key, 0.0) + float(row["remaining_qty"])
    return bucketed


def _resolve_capacity_multiplier(value: float | tuple[float, dt.date], period: dt.date) -> float:
    """A capacity lever entry is either a flat float (applies the whole
    horizon) or (multiplier, effective_from_date) [CP3.1 req. D] — so an
    intervention can be modeled as "applied starting week N", not just
    "always on", to demonstrate peak -> recovery rather than a flat-shifted
    trajectory from day one."""
    if isinstance(value, tuple):
        multiplier, effective_from = value
        return multiplier if period >= effective_from else 1.0
    return value


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
    capacity_multiplier_by_work_centre: dict[int, float | tuple[float, dt.date]] | None = None,
    buffer_boost_by_item: dict[int, float] | None = None,
    min_consecutive_periods: int | None = None,
) -> PeriodEngineOutput:
    """`buffer_boost_by_item` [CP3.1 req. E]: a one-time addition to that
    item's opening inventory, representing a standing safety-stock decision
    made before the horizon starts. It changes nothing about capacity or
    routing — a BUFFER_ONLY scenario can smooth/delay when a shortage first
    bites, but cannot fix a sustained capacity deficit, which is exactly the
    numeric distinction CP3.1 asks the four intervention cases to prove."""
    demand_multiplier_by_item = demand_multiplier_by_item or {}
    capacity_multiplier_by_work_centre = capacity_multiplier_by_work_centre or {}
    buffer_boost_by_item = buffer_boost_by_item or {}
    min_consecutive = min_consecutive_periods or settings.candidate_constraint_min_consecutive_periods

    # Tables are indexed once per run; the inner loops below are dictionary lookups, not pandas filtering.
    item_code_by_id = {item_id: str(code) for item_id, code in zip(items_df["item_id"], items_df["item_code"])}
    item_type_by_id = dict(zip(items_df["item_id"], items_df["item_type"]))
    exploder = BomExploder(items_df, bom_headers_df, bom_components_df)
    routing_load = RoutingLoad(routing_headers_df, routing_operations_df)
    calendar = CapacityCalendar(capacity_calendar_df)
    process_type_by_wc = work_centres_df.set_index("work_centre_id")["process_type"].to_dict()
    workdays_by_wc = work_centres_df.set_index("work_centre_id")["days_per_week"].to_dict()
    base_queue_days = _base_queue_time_days_by_work_centre(routing_operations_df)
    avg_minutes_per_unit = _avg_minutes_per_unit_by_work_centre(routing_operations_df)
    receipts_by_item_period = _bucket_receipts_by_item_period(purchase_order_lines_df, horizon)
    low_level_codes = compute_low_level_codes(items_df, bom_headers_df, bom_components_df)
    max_llc = max(low_level_codes.values(), default=0)

    inventory_state: dict[int, float] = dict(initial_inventory)
    for item_id, boost in buffer_boost_by_item.items():
        inventory_state[item_id] = inventory_state.get(item_id, 0.0) + boost
    backlog_state: dict[int, float] = {int(wc): 0.0 for wc in work_centres_df["work_centre_id"]}
    constraint_state: dict[int, ConstraintState] = {int(wc): ConstraintState() for wc in work_centres_df["work_centre_id"]}

    output = PeriodEngineOutput()

    for period in sorted(horizon):
        period_demand = top_level_demand.get(period, {})

        # --- 1+2. Cascading multi-level MRP [CP3.1 req. A]: net each item
        #     against its own carried-forward inventory/receipts, then
        #     explode only its NET requirement one level down, in ascending
        #     low-level-code order so every parent (at any depth) has
        #     already contributed its demand before a shared component is
        #     netted. See app.analytics.mrp module docstring. ---
        demand_by_item: dict[int, float] = {}
        for item_id, qty in period_demand.items():
            qty = qty * demand_multiplier_by_item.get(item_id, 1.0)
            if qty > 0:
                demand_by_item[item_id] = demand_by_item.get(item_id, 0.0) + qty

        net_by_item: dict[int, float] = {}
        gross_by_item: dict[int, float] = {}

        for level in range(0, max_llc + 1):
            items_at_level = [
                item_id for item_id, qty in demand_by_item.items()
                if low_level_codes.get(item_id, 0) == level and qty > 0
            ]
            for item_id in items_at_level:
                gross = demand_by_item[item_id]
                gross_by_item[item_id] = gross_by_item.get(item_id, 0.0) + gross
                item_code = item_code_by_id.get(item_id, "UNKNOWN")

                blocked = blocking_index.is_entity_blocked("item", item_id)
                stored = inventory_state.get(item_id, 0.0)
                usable = 0.0 if blocked else stored
                receipts = receipts_by_item_period.get((item_id, period), 0.0)
                # Scalar form of netting.net_requirement for one position (same rules; see its tests).
                usable_inventory, scheduled_receipts, net = net_single_position(gross, usable, receipts)
                net_by_item[item_id] = net_by_item.get(item_id, 0.0) + net

                available = usable + receipts
                consumed = min(available, gross)
                # Blocked stock cannot be used, but it is still physically there: carry it forward untouched
                # instead of overwriting it with what is left of this week's receipts.
                held_back = stored if blocked else 0.0
                inventory_state[item_id] = held_back + max(0.0, available - consumed)

                output.material_results.append(MaterialRequirementPeriodResult(
                    item_id=item_id, item_code=item_code, period_start_date=period,
                    gross_requirement=gross, usable_inventory=usable_inventory,
                    scheduled_receipts=scheduled_receipts, net_requirement=net,
                    shortage_flag=net > 0,
                ))

                if net <= 0 or blocked:
                    continue  # nothing left to build, or branch blocked — do not cascade further

                one_level = exploder.explode_one_level(item_id, net, period)
                if one_level.incomplete:
                    continue  # ambiguous revision etc. — do not silently cascade a guess
                for child in one_level.children:
                    if child.incomplete:
                        continue  # orphan/invalid component — that branch's DQ finding already covers it
                    demand_by_item[child.component_item_id] = demand_by_item.get(child.component_item_id, 0.0) + child.gross_qty

        # A receipt for an item with no demand this week is not lost: it joins the carried inventory, so
        # later demand sees it. (Items netted above already folded their receipts into inventory_state.)
        for (receipt_item_id, receipt_period), receipt_qty in receipts_by_item_period.items():
            if receipt_period == period and receipt_item_id not in gross_by_item:
                inventory_state[receipt_item_id] = inventory_state.get(receipt_item_id, 0.0) + receipt_qty

        # --- 3. Routing load: only manufactured items' NET requirement
        #        drives work-centre hours (inventory already on hand does
        #        not need to be re-produced this period). ---
        production_qty_by_item = {
            item_id: qty for item_id, qty in net_by_item.items()
            if item_type_by_id.get(item_id) in ("FG", "SUBASSY")
        }
        required_by_wc, excluded_ops = routing_load.required_hours(production_qty_by_item, blocking_index, period)

        # --- 4-8. Per work centre: capacity tiers, backlog/queue, WIP, classification. ---
        classifications_this_period: dict[int, str] = {}
        for wc_id in work_centres_df["work_centre_id"]:
            wc_id = int(wc_id)
            required_hours = required_by_wc.get(wc_id, 0.0)
            multiplier = _resolve_capacity_multiplier(capacity_multiplier_by_work_centre.get(wc_id, 1.0), period)

            cap = calendar.compute(wc_id, period, required_hours, blocking_index, excluded_ops,
                                   effective_hours_multiplier=multiplier)

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
                effective_hours_per_workday = float("nan")
                operating_days = max(float(workdays_by_wc.get(wc_id, 5) or 5), 1.0)
                completed_hours = float("nan")
            else:
                backlog_end = max(0.0, backlog_start + required_hours - cap.effective_hours)
                queue_time_days = base_queue_days.get(wc_id, 0.0) + backlog_end / (cap.effective_hours / 7.0)
                operating_days = max(float(workdays_by_wc.get(wc_id, 5) or 5), 1.0)
                effective_hours_per_workday = cap.effective_hours / operating_days
                # [CP3.2] Flow-conservation identity (see analytics/reconciliation.py):
                # required_hours + backlog_start = completed_hours + backlog_end,
                # i.e. completed_hours = min(effective_hours, backlog_start + required_hours).
                # True by construction of the backlog formula above -- stored
                # explicitly so it can be reported and tested, not just implied.
                completed_hours = min(cap.effective_hours, backlog_start + required_hours)
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
                backlog_hours_end=backlog_end, completed_hours=completed_hours, wip_qty=wip_qty,
                queue_time_days=queue_time_days,
                backlog_hours_at_entry=backlog_start,
                effective_hours_per_workday=effective_hours_per_workday,
                effective_days_per_week=operating_days,
                base_queue_time_days=base_queue_days.get(wc_id, 0.0),
                constraint_classification=classification, excluded_routing_operation_ids=cap.excluded_routing_operation_ids,
            ))

        primary = select_primary_constraint(classifications_this_period, constraint_state, process_type_by_wc)
        if primary is not None:
            for r in output.work_centre_results:
                if r.period_start_date == period and r.work_centre_id == primary:
                    r.constraint_classification = "PRIMARY_CONSTRAINT"

        output.inventory_end_by_period[period] = {item: qty for item, qty in inventory_state.items() if qty > 0}

    return output


def compute_lead_time_for_item(
    item_id: int,
    period_start_date: dt.date,
    routing_headers_df: pd.DataFrame,
    routing_operations_df: pd.DataFrame,
    wc_results_by_period: dict[tuple[int, dt.date], WorkCentrePeriodResult],
    items_df: pd.DataFrame | None = None,
    bom_headers_df: pd.DataFrame | None = None,
    bom_components_df: pd.DataFrame | None = None,
):
    """Delegate to BOM-aware lead-time path model; queue workdays convert to calendar days."""
    from app.analytics.leadtime import compute_lead_time_for_item as compute
    return compute(item_id, period_start_date, routing_headers_df, routing_operations_df,
                   wc_results_by_period, items_df, bom_headers_df, bom_components_df)
