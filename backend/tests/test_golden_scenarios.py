"""CP3 req. 11/12: the named golden scenarios against the real generated
dataset — CAB-100 +40% demand, and a welding +30% capacity intervention on
top of it. These assert directional/qualitative properties (real generated
data has too much cross-product sharing of work centres for exact hardcoded
numbers to be meaningful or stable) — the precise mechanics are covered by
test_period_engine.py's synthetic mini-plant instead.
"""
from __future__ import annotations

import datetime as dt

from app.analytics.scenario_demo import run_golden_scenarios
from app.synthetic.timeline import REFERENCE_DATE

HORIZON = [REFERENCE_DATE + dt.timedelta(weeks=w) for w in range(12)]


def test_cab100_plus_40_percent_increases_welding_load_and_never_decreases_it(tables):
    results = run_golden_scenarios(tables, HORIZON)
    baseline = results["baseline"]
    scenario = results["scenario_demand_plus_40"]
    weld_ids = results["welding_work_centre_ids"]
    assert weld_ids, "expected at least one WELDING work centre in the generated dataset"

    for wc_id in weld_ids:
        base_series = {r.period_start_date: r for r in baseline.wc_series(wc_id)}
        scen_series = {r.period_start_date: r for r in scenario.wc_series(wc_id)}
        for period in HORIZON:
            b, s = base_series[period], scen_series[period]
            # Higher demand can never REDUCE required hours at a shared work
            # centre -- this is a structural invariant of the engine, not a
            # coincidence of this dataset.
            assert s.required_hours >= b.required_hours - 1e-6

    total_base_required = sum(r.required_hours for wc_id in weld_ids for r in baseline.wc_series(wc_id))
    total_scenario_required = sum(r.required_hours for wc_id in weld_ids for r in scenario.wc_series(wc_id))
    assert total_scenario_required > total_base_required

    print(f"\nWelding required hours over {len(HORIZON)} weeks: baseline={total_base_required:.1f}h, "
          f"CAB-100+40%={total_scenario_required:.1f}h "
          f"({(total_scenario_required / total_base_required - 1) * 100:+.1f}%)")


def test_cab100_plus_40_percent_material_requirements_increase(tables):
    results = run_golden_scenarios(tables, HORIZON)
    baseline, scenario = results["baseline"], results["scenario_demand_plus_40"]

    base_total = sum(r.gross_requirement for r in baseline.material_results)
    scenario_total = sum(r.gross_requirement for r in scenario.material_results)
    assert scenario_total > base_total
    print(f"Total material gross requirement over {len(HORIZON)} weeks: "
          f"baseline={base_total:.0f}, +40% scenario={scenario_total:.0f}")


def test_welding_capacity_plus_30_percent_reduces_backlog_versus_demand_only_scenario(tables):
    results = run_golden_scenarios(tables, HORIZON)
    scenario = results["scenario_demand_plus_40"]
    intervention = results["scenario_plus_capacity_plus_30"]
    weld_ids = results["welding_work_centre_ids"]

    scenario_total_backlog = sum(r.backlog_hours_end for wc_id in weld_ids for r in scenario.wc_series(wc_id))
    intervention_total_backlog = sum(r.backlog_hours_end for wc_id in weld_ids for r in intervention.wc_series(wc_id))

    print(f"Cumulative welding backlog-hours over {len(HORIZON)} weeks: "
          f"demand-only scenario={scenario_total_backlog:.1f}h, "
          f"+30% capacity intervention={intervention_total_backlog:.1f}h")

    assert intervention_total_backlog <= scenario_total_backlog

    for wc_id in weld_ids:
        scen_series = scenario.wc_series(wc_id)
        interv_series = intervention.wc_series(wc_id)
        for s, i in zip(scen_series, interv_series):
            assert i.effective_hours >= s.effective_hours - 1e-6  # capacity lever actually raised effective hours
