"""Builds period-engine inputs from a full generated/loaded dataset and runs
the CP3/CP3.1 named scenarios: CAB-100 +40% demand, a capacity intervention
sized to actually recover (not just slow backlog growth), and the four
intervention cases (BASELINE/BUFFER_ONLY/CAPACITY_ONLY/COMBINED). Shared by
the pytest golden-scenario tests (in-memory tables) and the CP3.1 report demo
script (DB-loaded tables) so there is exactly one implementation of "how to
wire the period engine up against a real dataset."

Nothing here hardcodes a specific work-centre id/code: "welding" is always
resolved generically via work_centres.process_type == 'WELDING' [CP3.1 req.
C], and which work centre(s) actually become constrained is always read back
from the engine's own classification output, never assumed in advance.
"""
from __future__ import annotations

import datetime as dt

import pandas as pd

from app.analytics.blocking import BlockingIndex, build_blocking_index
from app.analytics.forecast import consume_forecast, derive_planning_demand, to_top_level_demand
from app.analytics.period_engine import PeriodEngineOutput, run_period_engine
from app.dq.engine import findings_to_dataframe, run_all


def build_engine_inputs(tables: dict[str, pd.DataFrame], horizon: list[dt.date]) -> dict:
    items = tables["items"]

    findings = run_all(tables)
    blocking_index = build_blocking_index(findings_to_dataframe(findings))

    latest_inventory = tables["inventory"].sort_values("snapshot_date").groupby(
        ["item_id", "warehouse_id"], as_index=False
    ).last()
    inv_by_item = latest_inventory.groupby("item_id")["on_hand_qty"].sum()
    initial_inventory = {}
    for item_id, qty in inv_by_item.items():
        if blocking_index.is_entity_blocked("item", item_id):
            continue
        initial_inventory[int(item_id)] = max(0.0, float(qty))

    consumption = consume_forecast(
        tables["customer_forecasts"], tables["forecast_versions"],
        tables["sales_order_lines"], tables["sales_orders"],
    )
    planning_demand = derive_planning_demand(consumption, tables["sales_order_lines"], tables["sales_orders"])
    fg_item_ids = set(items.loc[items["item_type"] == "FG", "item_id"])
    planning_demand = planning_demand.loc[planning_demand["item_id"].isin(fg_item_ids)]
    horizon_dates = set(horizon)
    planning_demand = planning_demand.loc[
        planning_demand["delivery_period_start"].apply(lambda d: d.date() if hasattr(d, "date") else d).isin(horizon_dates)
    ]
    top_level_demand = to_top_level_demand(planning_demand)

    return dict(
        horizon=horizon,
        top_level_demand=top_level_demand,
        items_df=items,
        bom_headers_df=tables["bom_headers"],
        bom_components_df=tables["bom_components"],
        routing_headers_df=tables["routing_headers"],
        routing_operations_df=tables["routing_operations"],
        work_centres_df=tables["work_centres"],
        capacity_calendar_df=tables["capacity_calendar"],
        purchase_order_lines_df=tables["purchase_order_lines"],
        initial_inventory=initial_inventory,
        blocking_index=blocking_index,
    )


def cab100_item_ids(items_df: pd.DataFrame) -> list[int]:
    return items_df.loc[
        (items_df["item_type"] == "FG") & (items_df["product_family"] == "CAB-100"), "item_id"
    ].astype(int).tolist()


def work_centre_ids_for_process(work_centres_df: pd.DataFrame, process_type: str) -> list[int]:
    """Generic lookup by process_type -- never a hardcoded work_centre_id/code
    [CP3.1 req. C]."""
    return work_centres_df.loc[work_centres_df["process_type"] == process_type, "work_centre_id"].astype(int).tolist()


def welding_work_centre_ids(work_centres_df: pd.DataFrame) -> list[int]:
    return work_centre_ids_for_process(work_centres_df, "WELDING")


def items_loading_work_centres(
    routing_headers_df: pd.DataFrame, routing_operations_df: pd.DataFrame, work_centre_ids: list[int]
) -> list[int]:
    """Every item whose routing includes at least one operation on any of the
    given work centres -- used to target a buffer intervention generically,
    without hardcoding which specific subassembly items are involved."""
    routing_ids = routing_operations_df.loc[
        routing_operations_df["work_centre_id"].isin(work_centre_ids), "routing_id"
    ].unique()
    return routing_headers_df.loc[routing_headers_df["routing_id"].isin(routing_ids), "item_id"].astype(int).tolist()


def first_week_reaching(output: PeriodEngineOutput, work_centre_id: int, classification: str) -> dt.date | None:
    for r in output.wc_series(work_centre_id):
        if r.constraint_classification == classification:
            return r.period_start_date
    return None


def find_emergent_constraints(baseline: PeriodEngineOutput, scenario: PeriodEngineOutput) -> list[int]:
    """Work centres that are NOT persistently constrained in the baseline
    but DO reach CANDIDATE_CONSTRAINT or PRIMARY_CONSTRAINT in the scenario
    -- i.e. constraints the demand shock itself created, found generically
    by diffing the engine's own output, never assumed in advance."""
    persistent = {"CANDIDATE_CONSTRAINT", "PRIMARY_CONSTRAINT"}
    wc_ids = sorted({r.work_centre_id for r in scenario.work_centre_results})
    emergent = []
    for wc_id in wc_ids:
        base_classes = {r.constraint_classification for r in baseline.wc_series(wc_id)}
        scen_classes = {r.constraint_classification for r in scenario.wc_series(wc_id)}
        if not (base_classes & persistent) and (scen_classes & persistent):
            emergent.append(wc_id)
    return emergent


def run_cab100_demand_shock(
    tables: dict[str, pd.DataFrame], horizon: list[dt.date], demand_multiplier: float = 1.4
) -> dict:
    base_inputs = build_engine_inputs(tables, horizon)
    cab100_ids = cab100_item_ids(base_inputs["items_df"])
    weld_ids = welding_work_centre_ids(base_inputs["work_centres_df"])

    baseline = run_period_engine(**base_inputs)

    scenario_inputs = dict(base_inputs)
    scenario_inputs["demand_multiplier_by_item"] = {item_id: demand_multiplier for item_id in cab100_ids}
    scenario = run_period_engine(**scenario_inputs)

    return {
        "base_inputs": base_inputs, "scenario_inputs": scenario_inputs,
        "baseline": baseline, "scenario": scenario,
        "cab100_item_ids": cab100_ids, "welding_work_centre_ids": weld_ids,
        "emergent_constraints": find_emergent_constraints(baseline, scenario),
    }


def find_smallest_recovering_capacity_intervention(
    scenario_inputs: dict,
    work_centre_ids: list[int],
    intervention_start_week: dt.date,
    candidate_multipliers: list[float] = (1.1, 1.15, 1.2, 1.3, 1.4, 1.5, 1.75, 2.0),
) -> dict:
    """[CP3.1 req. D, superseded in scope by CP3.2 req. 5's per-work-centre
    version below] Tries increasing capacity multipliers (smallest first)
    applied to ALL of `work_centre_ids` simultaneously, and returns the
    smallest one where their COMBINED backlog peaks then declines. Kept for
    backward compatibility with the CP3 test suite; prefer
    find_smallest_recovering_capacity_intervention_per_work_centre for
    anything reported as "the" recovery multiplier, since a single shared
    multiplier does not guarantee every individual work centre recovers
    (see tasks/lessons.md)."""
    for multiplier in candidate_multipliers:
        capacity_lever = {wc_id: (multiplier, intervention_start_week) for wc_id in work_centre_ids}
        trial_inputs = dict(scenario_inputs)
        trial_inputs["capacity_multiplier_by_work_centre"] = capacity_lever
        output = run_period_engine(**trial_inputs)

        combined_by_period = {}
        for wc_id in work_centre_ids:
            for r in output.wc_series(wc_id):
                combined_by_period[r.period_start_date] = combined_by_period.get(r.period_start_date, 0.0) + r.backlog_hours_end
        series = [combined_by_period[p] for p in sorted(combined_by_period)]

        peak = max(series)
        peak_idx = series.index(peak)
        recovered = peak_idx < len(series) - 1 and series[-1] < peak
        if recovered:
            return {"multiplier": multiplier, "output": output, "combined_backlog_series": series, "peak": peak, "peak_index": peak_idx}

    # Nothing in the candidate list recovered -- return the largest tried so
    # the caller can still report what was attempted.
    return {"multiplier": None, "output": output, "combined_backlog_series": series, "peak": peak, "peak_index": peak_idx}


def _recovers(series: list[float]) -> tuple[bool, float, int]:
    peak = max(series)
    peak_idx = series.index(peak)
    recovered = peak_idx < len(series) - 1 and series[-1] < peak
    return recovered, peak, peak_idx


def find_smallest_recovering_capacity_intervention_per_work_centre(
    scenario_inputs: dict,
    work_centre_ids: list[int],
    intervention_start_week: dt.date,
    candidate_multipliers: list[float] = (1.1, 1.15, 1.2, 1.3, 1.4, 1.5, 1.75, 2.0, 2.5, 3.0),
) -> dict[int, dict]:
    """[CP3.2 req. 5] Searches each work centre INDEPENDENTLY (all others
    held at their scenario capacity) for the smallest multiplier that makes
    ITS OWN backlog peak then decline -- replacing the combined-backlog
    search, which sized one shared multiplier off the aggregate and could
    leave individual work centres under-recovered (observed in CP3.1: a
    1.3x multiplier sized for four work centres combined left one of them,
    POWDER_COATING, still growing). Returns {work_centre_id: {multiplier,
    peak, peak_index, backlog_series}}, using None for a work centre where
    nothing in the candidate list recovered it.
    """
    results: dict[int, dict] = {}
    for wc_id in work_centre_ids:
        found = None
        for multiplier in candidate_multipliers:
            trial_inputs = dict(scenario_inputs)
            trial_inputs["capacity_multiplier_by_work_centre"] = {wc_id: (multiplier, intervention_start_week)}
            output = run_period_engine(**trial_inputs)
            series = [r.backlog_hours_end for r in output.wc_series(wc_id)]
            recovered, peak, peak_idx = _recovers(series)
            if recovered:
                found = {"multiplier": multiplier, "peak": peak, "peak_index": peak_idx, "backlog_series": series}
                break
        if found is None:
            # Report what the largest candidate achieved, for transparency.
            trial_inputs = dict(scenario_inputs)
            trial_inputs["capacity_multiplier_by_work_centre"] = {wc_id: (candidate_multipliers[-1], intervention_start_week)}
            output = run_period_engine(**trial_inputs)
            series = [r.backlog_hours_end for r in output.wc_series(wc_id)]
            _, peak, peak_idx = _recovers(series)
            found = {"multiplier": None, "peak": peak, "peak_index": peak_idx, "backlog_series": series}
        results[wc_id] = found
    return results


def run_four_intervention_comparison(
    tables: dict[str, pd.DataFrame],
    horizon: list[dt.date],
    demand_multiplier: float = 1.4,
    buffer_boost_per_item: float = 40.0,
) -> dict:
    """[CP3.1 req. E, CP3.2 req. 5] BASELINE / BUFFER_ONLY / CAPACITY_ONLY /
    COMBINED, all driven off the same demand shock. Buffer-only adds
    standing safety-stock inventory to the items whose routing loads the
    emergent-constraint work centre(s) -- it changes nothing about capacity
    (proven in test_reconciliation.py). Capacity-only and combined each use
    a PER-WORK-CENTRE recovery multiplier (CP3.2 req. 5) rather than one
    multiplier shared across all emergent work centres, so each one's own
    recovery is what's actually being sized -- not just their combined sum.
    """
    shock = run_cab100_demand_shock(tables, horizon, demand_multiplier)
    scenario_inputs = shock["scenario_inputs"]
    weld_ids = shock["welding_work_centre_ids"]
    emergent = shock["emergent_constraints"] or weld_ids

    # Intervene right after the shock scenario first shows overload on an
    # emergent-constraint work centre -- found generically, not hardcoded.
    overload_weeks = [
        w for wc_id in emergent
        for w in [first_week_reaching(shock["scenario"], wc_id, "OVERLOADED")] if w is not None
    ]
    intervention_start = min(overload_weeks) if overload_weeks else horizon[len(horizon) // 2]

    per_wc_search = find_smallest_recovering_capacity_intervention_per_work_centre(scenario_inputs, emergent, intervention_start)
    capacity_lever = {
        wc_id: (result["multiplier"] or 3.0, intervention_start) for wc_id, result in per_wc_search.items()
    }

    buffer_targets = items_loading_work_centres(
        scenario_inputs["routing_headers_df"], scenario_inputs["routing_operations_df"], emergent
    )
    buffer_boost = {item_id: buffer_boost_per_item for item_id in buffer_targets}

    baseline = shock["baseline"]

    buffer_only_inputs = dict(scenario_inputs)
    buffer_only_inputs["buffer_boost_by_item"] = buffer_boost
    buffer_only = run_period_engine(**buffer_only_inputs)

    capacity_only_inputs = dict(scenario_inputs)
    capacity_only_inputs["capacity_multiplier_by_work_centre"] = capacity_lever
    capacity_only = run_period_engine(**capacity_only_inputs)

    combined_inputs = dict(scenario_inputs)
    combined_inputs["buffer_boost_by_item"] = buffer_boost
    combined_inputs["capacity_multiplier_by_work_centre"] = capacity_lever
    combined = run_period_engine(**combined_inputs)

    return {
        "emergent_constraints": emergent,
        "intervention_start_week": intervention_start,
        "per_work_centre_multipliers": {wc_id: r["multiplier"] for wc_id, r in per_wc_search.items()},
        "per_work_centre_search": per_wc_search,
        "buffer_boost_by_item": buffer_boost,
        "capacity_lever": capacity_lever,
        "BASELINE": baseline,
        "DEMAND_SHOCK_ONLY": shock["scenario"],
        "BUFFER_ONLY": buffer_only,
        "CAPACITY_ONLY": capacity_only,
        "COMBINED": combined,
    }


# --- Backward-compatible convenience wrapper used by the CP3 tests --------

def run_golden_scenarios(tables: dict[str, pd.DataFrame], horizon: list[dt.date]) -> dict:
    shock = run_cab100_demand_shock(tables, horizon, demand_multiplier=1.4)
    weld_ids = shock["welding_work_centre_ids"]

    capacity_plus_30 = {wc_id: 1.3 for wc_id in weld_ids}
    intervention_inputs = dict(shock["scenario_inputs"])
    intervention_inputs["capacity_multiplier_by_work_centre"] = capacity_plus_30
    intervention = run_period_engine(**intervention_inputs)

    return {
        "baseline": shock["baseline"], "scenario_demand_plus_40": shock["scenario"],
        "scenario_plus_capacity_plus_30": intervention,
        "cab100_item_ids": shock["cab100_item_ids"], "welding_work_centre_ids": weld_ids,
    }
