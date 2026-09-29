import datetime as dt

import pandas as pd
import pytest

from app.analytics import cost
from app.analytics.period_engine import MaterialRequirementPeriodResult, PeriodEngineOutput, WorkCentrePeriodResult

W1, W2, W3 = dt.date(2026, 6, 1), dt.date(2026, 6, 8), dt.date(2026, 6, 15)

ITEMS = pd.DataFrame({"item_id": [1, 2, 3, 4, 5], "item_type": ["RAW", "SUBASSY", "FG", "FG", "FG"]})
BOM_HEADERS = pd.DataFrame({"bom_id": [10, 11], "parent_item_id": [2, 3]})
BOM_COMPONENTS = pd.DataFrame({"bom_id": [10, 11], "component_item_id": [1, 2]})   # 2 ← raw 1; 3 ← subassy 2
STANDARD_COSTS = pd.DataFrame([
    # item, from, to, material, labour, overhead, source
    (100, 1, "2026-01-01", None, 5.0, 0.0, 0.9, "MEASURED"),
    (101, 2, "2026-01-01", "2026-05-31", 99.0, 99.0, 99.0, "MEASURED"),   # expired revision
    (102, 2, "2026-06-01", None, 10.0, 4.0, 2.52, "MEASURED"),            # effective revision
    (103, 3, "2026-01-01", None, 20.0, 6.0, 4.68, "MEASURED"),
    (104, 4, "2026-01-01", None, 1.0, 1.0, 1.0, "MEASURED"),
    (105, 4, "2026-03-01", None, 2.0, 2.0, 2.0, "MEASURED"),              # overlaps 104
], columns=["standard_cost_id", "item_id", "effective_from", "effective_to", "material_cost", "labour_cost",
            "overhead_cost", "cost_source"])


@pytest.fixture
def costs():
    return cost.unit_costs(STANDARD_COSTS, ITEMS, BOM_HEADERS, BOM_COMPONENTS, W1)


def test_unit_cost_is_the_effective_record_not_a_bom_resum(costs):
    assert costs[2].standard_cost_id == 102 and costs[2].total == pytest.approx(16.52)
    # FG 3 uses its own record (20 + 6 + 4.68); its component's 16.52 is not added again
    assert costs[3].total == pytest.approx(30.68)
    assert costs[1].provenance == "MEASURED"


def test_missing_and_overlapping_costs_are_unknown_not_zero(costs):
    assert costs[4].status == cost.OVERLAPPING and costs[4].total is None
    assert costs[5].status == cost.MISSING and costs[5].total is None


def test_manufactured_component_material_is_marked_assumed(costs):
    assert costs[2].provenance == "MEASURED"        # only a RAW component
    assert costs[3].provenance == "ASSUMED"         # has a SUBASSY component
    assert "approximated" in costs[3].reason


def _material(item, period, qty):
    return MaterialRequirementPeriodResult(item_id=item, item_code=str(item), period_start_date=period,
                                           gross_requirement=0, usable_inventory=qty, scheduled_receipts=0,
                                           net_requirement=0, shortage_flag=False)


def _wc(period, backlog):
    return WorkCentrePeriodResult(work_centre_id=9, period_start_date=period, calendar_hours=40, available_hours=40,
                                  effective_hours=35, required_hours=35, utilization_pct=1.0, backlog_hours_start=0,
                                  backlog_hours_end=backlog, completed_hours=35, wip_qty=0, queue_time_days=0,
                                  backlog_hours_at_entry=0, effective_hours_per_workday=7, effective_days_per_week=5,
                                  base_queue_time_days=0, constraint_classification="NONE")


def test_inventory_value_and_carrying_cost_exposure_timing(costs):
    output = PeriodEngineOutput(
        work_centre_results=[_wc(W1, 0), _wc(W2, 0), _wc(W3, 12.0)],
        material_results=[_material(2, W1, 10)],                     # rows exist only for weeks with demand
        inventory_end_by_period={W1: {2: 10, 5: 7}, W2: {2: 5}, W3: {}})   # item 5 has no cost → unvalued
    assumptions = cost.CostAssumptions(annual_carrying_rate=0.26, weeks_per_year=52)
    econ = cost.case_economics(output, costs, [W1, W2, W3], assumptions)
    series = econ["inventory_series"].set_index("period_start_date")
    assert series.loc[W1, "inventory_value"] == pytest.approx(165.2)
    assert series.loc[W1, "unvalued_qty"] == 7
    # carrying = Σ weekly value × 0.26/52 = (165.2 + 82.6 + 0) × 0.005
    assert econ["inventory_carrying_cost"] == pytest.approx(1.239)
    assert econ["ending_backlog_hours"] == 12.0
    assert econ["wip_carrying_cost"] is None and "Unavailable" in econ["wip_carrying_cost_reason"]


def test_buffer_capital_excludes_unvalued_items(costs):
    detail = cost.buffer_capital({2: 40.0, 4: 40.0}, costs)
    assert detail["capital"] == pytest.approx(40 * 16.52)
    assert detail["unvalued_items"] == 1 and detail["unvalued_qty"] == 40


def test_intervention_cost_counts_only_active_weeks_of_added_scheduled_hours():
    wcs = pd.DataFrame({"work_centre_id": [9], "shifts_per_day": [1], "hours_per_shift": [8.0],
                        "days_per_week": [5], "cost_per_hour": [60.0]})
    result = cost.intervention_cost({9: (1.1, W2)}, wcs, [W1, W2, W3],
                                    cost.CostAssumptions(added_hour_rate_multiplier=1.25))
    line = result["lines"][0]
    assert line["added_scheduled_hours_per_week"] == pytest.approx(4.0)   # 40 h × 0.10
    assert line["active_weeks"] == 2                                      # W1 is before the start
    assert result["cost"] == pytest.approx(4.0 * 2 * 60.0 * 1.25)


def test_five_case_deltas_are_relative_to_the_comparison_case():
    base = {"average_inventory_capital": 100.0, "inventory_carrying_cost": 5.0, "buffer_capital": 0.0,
            "intervention_cost": 0.0, "ending_backlog_hours": 200.0}
    econ = {"DEMAND_SHOCK_ONLY": base,
            "COMBINED": {**base, "buffer_capital": 50.0, "intervention_cost": 30.0, "ending_backlog_hours": 80.0}}
    rows = {r["case"]: r for r in cost.five_case_summary(econ)}
    assert rows["DEMAND_SHOCK_ONLY"]["delta_intervention_cost"] == 0
    assert rows["COMBINED"]["delta_buffer_capital"] == 50.0
    assert rows["COMBINED"]["delta_ending_backlog_hours"] == -120.0
