"""Stock points, forecast change and the policy suite, on hand-built plants.

Plant: SUB is laser-cut (work centre 2, 1 h per lot of 10) then painted (work centre 3, 10 h per lot of 10, plus a
5 minute reload); the finished good needs one SUB per unit and is assembled (work centre 1). Two lots of 10 are
demanded together, so the second lot's pieces wait after laser cutting while the first is being painted.
Clock: lot release at hour 168, first shift 174; the painting of lot A runs 175 -> 201.0833 (Monday, then Tuesday)."""
from __future__ import annotations

import pytest

from app.analytics.flow.analysis import (DEFAULT_WIP_POINTS, ForecastChange, WipPoint, change_impact, forecast_revisions,
                                         point_history, policy_suite, stage_table, waiting_passes, weekly_stock)
from app.analytics.flow.model import COATING_PROCESSES, Policy
from app.analytics.flow.sim import FlowSimulation
from tests.flow_helpers import FG, STEEL, SUB, op, params, plant, wc


def chain(demand=None):
    return plant([wc(1, "ASSEMBLY"), wc(2, "LASER_CUTTING", cost=60.0), wc(3, "POWDER_COATING", cost=60.0)],
                 {FG: [op(1, run=6)], SUB: [op(2, "Laser", run=6), op(3, "Paint", run=60)]},
                 bom={FG: [(SUB, 1.0, 0.0)], SUB: [(STEEL, 1.0, 0.0)]}, demand=demand or [(0, FG, 20.0)])


def simulate(p, policy=None, **kw):
    return FlowSimulation(p, params(max_lot_qty=10.0, **kw), policy).run()


PAINT_END_A = 198.0 + 3.0 + 5 / 60                                  # 7 h on Monday (175-182), 3 h 5 min from Tuesday 198
LASER_END_B = 176.0


def test_waiting_after_laser_cutting_is_what_happened_while_the_painting_line_was_busy():
    result = simulate(chain())
    history = point_history(result, DEFAULT_WIP_POINTS[0])
    assert history["passes"] == 2 and history["units_through"] == 20.0
    wait_b = (PAINT_END_A - LASER_END_B) / 24.0
    assert history["max_wait_days"] == pytest.approx(wait_b, abs=1e-3)
    assert history["mean_wait_days"] == pytest.approx(wait_b / 2.0, abs=1e-3)           # lot A waited no time at all
    assert history["peak_units"] == 10.0


def test_before_painting_is_the_same_waiting_seen_from_the_painting_line():
    result = simulate(chain())
    after = point_history(result, DEFAULT_WIP_POINTS[0])
    before = point_history(result, DEFAULT_WIP_POINTS[1])
    assert before["units_through"] == after["units_through"] and before["max_wait_days"] == pytest.approx(after["max_wait_days"])


def test_value_in_stock_is_the_laser_hours_plus_the_material_issued():
    history = point_history(simulate(chain()), DEFAULT_WIP_POINTS[0])
    assert history["peak_value"] == pytest.approx(1.0 * 60.0 + 10 * 10.0)               # 1 laser hour at 60 + 10 pieces of steel at 10


def test_the_stage_table_audits_every_stage_at_one_moment():
    result = simulate(chain())
    rows = {row["stage"]: row for row in stage_table(result, 192.0)}                    # Tuesday 00:00 of the lead-in week
    assert rows["Waiting before POWDER_COATING"]["units"] == 10.0 and rows["Waiting before POWDER_COATING"]["jobs"] == 1
    assert rows["Waiting before POWDER_COATING"]["value"] == pytest.approx(1.0 * 60.0 + 10 * 10.0)       # laser hour + steel
    assert rows["In process at POWDER_COATING"]["units"] == 10.0
    partial = 10.0833 * (192.0 - 175.0) / (PAINT_END_A - 175.0)
    assert rows["In process at POWDER_COATING"]["value"] == pytest.approx(160.0 + partial * 60.0, rel=1e-3)


def test_a_third_stock_point_can_be_defined_on_any_process():
    point = WipPoint("After painting", after=COATING_PROCESSES)
    history = point_history(simulate(chain()), point)
    assert history["passes"] == 2 and history["units_through"] == 20.0


# ---- change of forecast ----------------------------------------------------------------------------------------
def test_cancelling_strands_everything_already_built_when_nothing_else_needs_it():
    out = change_impact(chain(), params(max_lot_qty=10.0), Policy(), ForecastChange(day=1.0, from_week=0, factor=0.0))
    assert out["lots_changed"] == 2
    laser_value = 1.0 * 60.0 + 100.0
    partial = 10.0833 * (192.0 - 175.0) / (PAINT_END_A - 175.0) * 60.0
    assert out["sunk_value"] == pytest.approx(laser_value + laser_value + partial, rel=1e-3)   # lot B in stock, lot A in process
    assert out["reusable_value"] == 0.0 and out["stranded_value"] == pytest.approx(out["sunk_value"])
    assert out["metrics_after"]["lots"] == 0
    after_laser = next(p for p in out["points"] if p["name"] == "After laser cutting")
    assert after_laser["stranded_value"] == pytest.approx(laser_value) and after_laser["units_at_change"] == 10.0


def test_work_that_other_open_demand_still_needs_is_reusable():
    p = chain(demand=[(0, FG, 20.0), (3, FG, 10.0)])
    out = change_impact(p, params(max_lot_qty=10.0, horizon_weeks=4), Policy(),
                        ForecastChange(day=1.0, from_week=0, to_week=0, factor=0.0))
    assert out["lots_changed"] == 2
    stored = 1.0 * 60.0 + 10 * 10.0                                                     # lot B's pieces waiting after laser cutting
    assert out["reusable_value"] == pytest.approx(stored)                                # the week 3 lot needs exactly these 10
    assert out["stranded_value"] == pytest.approx(out["sunk_value"] - stored)
    assert out["metrics_after"]["lots"] == 1                                            # the week 3 lot is unchanged


def test_a_smaller_forecast_strands_only_the_unwanted_share():
    full = change_impact(chain(), params(max_lot_qty=10.0), Policy(), ForecastChange(day=1.0, from_week=0, factor=0.0))
    half = change_impact(chain(), params(max_lot_qty=10.0), Policy(), ForecastChange(day=1.0, from_week=0, factor=0.5))
    assert half["sunk_value"] == pytest.approx(full["sunk_value"] * 0.5, rel=1e-6)


def test_a_larger_forecast_strands_nothing_and_adds_load():
    out = change_impact(chain(), params(max_lot_qty=10.0), Policy(), ForecastChange(day=1.0, from_week=0, factor=1.5))
    assert out["sunk_value"] == 0.0
    coating = next(r for r in out["resources"] if r["process"] == "POWDER_COATING")
    assert coating["change"] > 0 and out["metrics_after"]["lots"] > out["metrics_before"]["lots"]


def test_change_before_any_work_started_costs_nothing():
    out = change_impact(chain(), params(max_lot_qty=10.0), Policy(), ForecastChange(day=-5.0, from_week=0, factor=0.0))
    assert out["sunk_value"] == 0.0


def test_colour_committed_work_is_reusable_only_for_the_same_colour():
    # lot A (cancelled) is coloured; the surviving lot is another colour, so the painted pieces cannot be reused
    p = chain(demand=[(0, FG, 10.0), (3, FG, 10.0)])
    prm = params(max_lot_qty=10.0, horizon_weeks=4, colours=(("RED", 0.5), ("BLUE", 0.5)), seed=3)
    base = FlowSimulation(p, prm, Policy()).run()
    reds = {lot["colour"] for lot in base.fg_lots}
    out = change_impact(p, prm, Policy(), ForecastChange(day=3.0, from_week=0, to_week=0, factor=0.0))
    painted = [s for s in out["stages"] if s["process"] == "POWDER_COATING"]
    if len(reds) == 2 and painted:
        assert all(s["reusable_units"] == 0.0 for s in painted)
    assert out["stranded_value"] <= out["sunk_value"] + 1e-9


# ---- policy suite ----------------------------------------------------------------------------------------------
def test_the_policy_suite_covers_every_case_and_marks_the_stochastic_ones():
    suite = policy_suite(chain(), params(max_lot_qty=10.0, defect_rate_default=0.1), replications=4, windows=(0.0, 5.0),
                         overtime_work_centres=(2,))
    keys = [entry["key"] for entry in suite]
    assert keys == ["today", "colour_0", "colour_5", "kit_priority", "scrap_final", "scrap_replace", "scrap_component",
                    "overtime_unconditional", "overtime_gated", "combined", "today_with_defects"]
    by_key = {entry["key"]: entry for entry in suite}
    assert by_key["today"]["reps"] == 1 and by_key["today"]["std"] is None
    assert by_key["scrap_final"]["reps"] == 4 and by_key["scrap_final"]["std"] is not None
    assert by_key["scrap_component"]["metrics"]["scrap_value"] <= by_key["scrap_final"]["metrics"]["scrap_value"]


def test_the_policy_suite_is_reproducible():
    a = policy_suite(chain(), params(max_lot_qty=10.0, defect_rate_default=0.1), replications=3, windows=(5.0,))
    b = policy_suite(chain(), params(max_lot_qty=10.0, defect_rate_default=0.1), replications=3, windows=(5.0,))
    assert [e["metrics"] for e in a] == [e["metrics"] for e in b]


# ---- third stock point, weekly history, weekly forecast updates -------------------------------------------------
def test_the_named_stock_points_are_laser_painting_and_welding_to_painting():
    assert [p.name for p in DEFAULT_WIP_POINTS] == ["After laser cutting", "Before painting", "Between welding and painting"]


def test_weekly_stock_averages_the_daily_history():
    result = simulate(chain())
    history = point_history(result, DEFAULT_WIP_POINTS[0])
    weeks = weekly_stock(history, 3)
    assert len(weeks) == 3 and weeks[0]["units"] > 0
    assert weeks[0]["units"] == pytest.approx(sum(s["units"] for s in history["series"] if s["day"] < 7) / 7.0)


def test_a_forecast_update_changes_only_later_weeks_and_strands_nothing_not_yet_started():
    p = chain(demand=[(0, FG, 10.0), (1, FG, 10.0), (3, FG, 10.0)])
    out = forecast_revisions(p, params(max_lot_qty=10.0, horizon_weeks=4), Policy(), [{(3, FG): 0.5, (1, FG): 0.1}])
    assert len(out) == 1 and out[0]["units_removed"] == pytest.approx(5.0)            # week 1 is frozen at the update on week 1
    assert out[0]["stranded_value"] == 0.0 and out[0]["metrics_after"]["lots"] == 3   # week 3 work had not started


def test_a_forecast_update_that_removes_started_work_strands_it():
    """Parts for week-2 lots are released two weeks ahead (offset 14 days), so they are already made, and waiting for
    their assembly, when the Sunday update cancels week 2."""
    p = chain(demand=[(2, FG, 10.0)])
    out = forecast_revisions(p, params(max_lot_qty=10.0, horizon_weeks=4, lead_in_weeks=1, level_offset_days=14.0), Policy(),
                             [{(2, FG): 0.0}])
    assert out[0]["units_removed"] == pytest.approx(10.0)
    painted_and_cut = 1.0 * 60.0 + 10 * 10.0 + 10.0833 * 60.0                        # laser hour + steel + painting hours
    assert out[0]["sunk_value"] == pytest.approx(painted_and_cut, rel=1e-3) and out[0]["stranded_value"] == pytest.approx(out[0]["sunk_value"])
    assert out[0]["metrics_after"]["lots"] == 0
