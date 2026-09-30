"""Flow-conservation reconciliation and system-level intervention KPIs
[CP3.2 req. 3/4].

Two identities are proven here, both true *by construction* of the
formulas in period_engine.py and netting.py — this module computes and
reports them explicitly (and tests assert them) rather than leaving them
implicit, so a future change to either formula that breaks conservation is
caught immediately:

1. **Work-centre hours**: for every period,
   `required_hours + backlog_hours_start = completed_hours + backlog_hours_end`.
   Summed over a horizon (backlog_hours_start of period 0 is always 0):
   `sum(required_hours) = sum(completed_hours) + final_backlog_hours_end`.
   A capacity intervention can only change this by raising `effective_hours`
   (which raises `completed_hours`, i.e. the constraint itself relieves) —
   it cannot make `completed_hours` exceed `effective_hours` in any single
   period, which is exactly the "buffer/capacity cannot exceed the physical
   constraint" invariant CP3.2 req. 3 asks to prove.

2. **Material flow**: for every (item, period),
   `gross_requirement = satisfied_from_inventory + satisfied_from_receipts + net_requirement`.
   A buffer (opening-inventory boost) can only ever increase
   `satisfied_from_inventory` — it changes nothing about `gross_requirement`
   (demand is not destroyed) and nothing about work-centre `effective_hours`
   (capacity is not created). Where a buffer's backlog reduction "goes" is
   exactly this term: more demand was satisfied from stock that was already
   built before the horizon began, so less *new* production (and hence less
   work-centre load) was required — not because capacity secretly grew.
"""
from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from app.analytics.leadtime import LeadTimeCalculator
from app.analytics.period_engine import PeriodEngineOutput

TOLERANCE = 1e-6


@dataclass
class WorkCentreReconciliation:
    work_centre_id: int
    total_required_hours: float
    total_completed_hours: float
    final_backlog_hours: float
    discrepancy: float
    holds: bool


def reconcile_work_centre_hours(output: PeriodEngineOutput, work_centre_id: int) -> WorkCentreReconciliation:
    series = output.wc_series(work_centre_id)
    total_required = sum(r.required_hours for r in series)
    total_completed = sum(r.completed_hours for r in series if not math.isnan(r.completed_hours))
    final_backlog = series[-1].backlog_hours_end if series else 0.0
    discrepancy = total_required - (total_completed + final_backlog)
    return WorkCentreReconciliation(
        work_centre_id=work_centre_id, total_required_hours=total_required,
        total_completed_hours=total_completed, final_backlog_hours=final_backlog,
        discrepancy=discrepancy, holds=abs(discrepancy) < max(TOLERANCE, total_required * 1e-9),
    )


@dataclass
class MaterialReconciliation:
    item_id: int
    total_gross_requirement: float
    total_satisfied_from_inventory: float
    total_satisfied_from_receipts: float
    total_net_requirement: float
    discrepancy: float
    holds: bool


def reconcile_material_flow(output: PeriodEngineOutput, item_id: int) -> MaterialReconciliation:
    series = output.material_series(item_id)
    total_gross = sum(r.gross_requirement for r in series)
    total_net = sum(r.net_requirement for r in series)
    # satisfied_from_inventory + satisfied_from_receipts = gross - net, by
    # construction (netting.py: net = max(0, gross - usable - receipts)).
    # The per-period split between the two sources is recoverable from the
    # stored usable_inventory/scheduled_receipts fields (capped at what was
    # actually needed, since netting never "uses" more than gross demands).
    satisfied_from_inventory = sum(min(r.usable_inventory, r.gross_requirement) for r in series)
    satisfied_from_receipts = sum(
        min(r.scheduled_receipts, max(0.0, r.gross_requirement - r.usable_inventory)) for r in series
    )
    discrepancy = total_gross - (satisfied_from_inventory + satisfied_from_receipts + total_net)
    return MaterialReconciliation(
        item_id=item_id, total_gross_requirement=total_gross,
        total_satisfied_from_inventory=satisfied_from_inventory,
        total_satisfied_from_receipts=satisfied_from_receipts, total_net_requirement=total_net,
        discrepancy=discrepancy, holds=abs(discrepancy) < max(TOLERANCE, total_gross * 1e-9),
    )


@dataclass
class SystemKpis:
    label: str
    total_demand_units: float
    completed_units_estimate: float
    ending_system_wip: float
    ending_total_backlog_hours: float
    overdue_unmet_units_estimate: float  # proxy: ending backlog converted to units-equivalent
    avg_lead_time_days: float
    p90_lead_time_days: float
    p95_lead_time_days: float
    service_risk: str  # LOW / MEDIUM / HIGH -- "Analytical Service-Risk Indicator", not a certified SLA metric
    constrained_wc_avg_utilization: dict[int, float] = field(default_factory=dict)
    constrained_wc_ending_backlog_hours: dict[int, float] = field(default_factory=dict)
    satisfied_from_inventory_units: float = 0.0
    plant_avg_lead_time_days: float = float("nan")


def compute_system_kpis(
    label: str,
    output: PeriodEngineOutput,
    constrained_work_centre_ids: list[int],
    total_demand_units: float,
    lead_time_item_id: int,
    routing_headers_df: pd.DataFrame,
    routing_operations_df: pd.DataFrame,
    horizon: list[dt.date],
    avg_minutes_per_unit_by_wc: dict[int, float],
    items_df: pd.DataFrame | None = None,
    bom_headers_df: pd.DataFrame | None = None,
    bom_components_df: pd.DataFrame | None = None,
    lead_time_item_ids: list[int] | None = None,
) -> SystemKpis:
    ending_wip = sum(
        r.wip_qty for r in output.work_centre_results if r.period_start_date == horizon[-1]
    )
    ending_backlog_hours = sum(
        r.backlog_hours_end for r in output.work_centre_results if r.period_start_date == horizon[-1]
    )
    # Convert ending backlog to a units-equivalent "overdue/unmet demand"
    # proxy using each work centre's own average minutes-per-unit -- an
    # approximation (see module docstring), not order-level due-date tracking.
    overdue_units = 0.0
    for r in output.work_centre_results:
        if r.period_start_date == horizon[-1] and r.backlog_hours_end > 0:
            mpu = avg_minutes_per_unit_by_wc.get(r.work_centre_id)
            if mpu:
                overdue_units += r.backlog_hours_end * 60.0 / mpu

    completed_by_wc = {}
    for r in output.work_centre_results:
        if not math.isnan(r.completed_hours):
            mpu = avg_minutes_per_unit_by_wc.get(r.work_centre_id)
            if mpu:
                completed_by_wc[r.work_centre_id] = completed_by_wc.get(r.work_centre_id, 0.0) + r.completed_hours * 60.0 / mpu
    completed_units_estimate = sum(completed_by_wc.values())

    wc_results_by_period = {(r.work_centre_id, r.period_start_date): r for r in output.work_centre_results}
    if items_df is not None:
        plant_items = items_df.loc[items_df["item_type"] == "FG", "item_id"].astype(int).tolist()
    else:
        plant_items = [lead_time_item_id]
    cab_items = lead_time_item_ids or [lead_time_item_id]
    calculator = LeadTimeCalculator(routing_headers_df, routing_operations_df, items_df, bom_headers_df, bom_components_df)

    def demand_weighted_lead_time(item_ids: list[int]) -> tuple[float, list[tuple[float, float]]]:
        weighted_sum = total_weight = 0.0
        samples = []
        for item_id in item_ids:
            for period in horizon:
                lt = calculator.compute(item_id, period, wc_results_by_period)
                if lt is None:
                    continue
                rows = output.material_series(item_id)
                weight = next((r.gross_requirement for r in rows if r.period_start_date == period), 0.0)
                if weight > 0:
                    weighted_sum += lt.total_days * weight
                    total_weight += weight
                    samples.append((lt.total_days, weight))
        return (weighted_sum / total_weight if total_weight else float("nan")), samples

    def weighted_percentile(samples: list[tuple[float, float]], percentile: float) -> float:
        if not samples:
            return float("nan")
        ordered = sorted(samples)
        threshold = sum(weight for _, weight in ordered) * percentile / 100.0
        cumulative = 0.0
        for value, weight in ordered:
            cumulative += weight
            if cumulative >= threshold:
                return value
        return ordered[-1][0]

    avg_lt, lead_times = demand_weighted_lead_time(cab_items)
    plant_avg_lt, _ = demand_weighted_lead_time(plant_items)
    p90_lt = weighted_percentile(lead_times, 90)
    p95_lt = weighted_percentile(lead_times, 95)

    constrained_util = {}
    constrained_backlog = {}
    for wc_id in constrained_work_centre_ids:
        series = output.wc_series(wc_id)
        utils = [r.utilization_pct for r in series if r.utilization_pct == r.utilization_pct]
        constrained_util[wc_id] = float(np.mean(utils)) if utils else float("nan")
        constrained_backlog[wc_id] = series[-1].backlog_hours_end if series else 0.0

    # Analytical Service-Risk Indicator (not a certified SLA/OTIF metric):
    # HIGH if any constrained work centre ends the horizon still classified
    # as a persistent constraint with backlog not yet declining from its
    # peak; MEDIUM if a persistent constraint occurred but has since
    # recovered; LOW if none ever reached CANDIDATE/PRIMARY_CONSTRAINT.
    persistent_labels = {"CANDIDATE_CONSTRAINT", "PRIMARY_CONSTRAINT"}
    ever_persistent = any(
        r.constraint_classification in persistent_labels
        for wc_id in constrained_work_centre_ids for r in output.wc_series(wc_id)
    )
    if not ever_persistent:
        service_risk = "LOW"
    else:
        still_persistent_and_growing = False
        for wc_id in constrained_work_centre_ids:
            series = output.wc_series(wc_id)
            if not series:
                continue
            if series[-1].constraint_classification in persistent_labels and len(series) >= 2 and series[-1].backlog_hours_end >= series[-2].backlog_hours_end:
                still_persistent_and_growing = True
        service_risk = "HIGH" if still_persistent_and_growing else "MEDIUM"

    return SystemKpis(
        label=label, total_demand_units=total_demand_units, completed_units_estimate=completed_units_estimate,
        ending_system_wip=ending_wip, ending_total_backlog_hours=ending_backlog_hours,
        overdue_unmet_units_estimate=overdue_units, avg_lead_time_days=avg_lt,
        p90_lead_time_days=p90_lt, p95_lead_time_days=p95_lt, service_risk=service_risk,
        constrained_wc_avg_utilization=constrained_util, constrained_wc_ending_backlog_hours=constrained_backlog,
        plant_avg_lead_time_days=plant_avg_lt,
    )
