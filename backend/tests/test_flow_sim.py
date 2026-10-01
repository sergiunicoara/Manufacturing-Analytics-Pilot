"""Mechanics of the flow simulator, checked on hand-built plants where every number can be computed by hand.
Clock zero is a Monday 00:00; with a one-week lead-in the first lot is released at hour 168 and the first shift
starts at 174 (06:00)."""
from __future__ import annotations

import pytest

from app.analytics.flow.model import Policy
from app.analytics.flow.sim import FlowSimulation
from tests.flow_helpers import FG, STEEL, SUB, op, params, plant, wc

RELEASE, FIRST_SHIFT = 168.0, 174.0


def run(p, prm=None, policy=None, seed=None):
    return FlowSimulation(p, prm or params(), policy, seed).run()


def only_lot(result):
    assert len(result.fg_lots) == 1
    return result.fg_lots[0]


def test_a_work_centre_without_working_hours_is_rejected_instead_of_looping_forever():
    p = plant([wc(1, "LASER_CUTTING", hours=0.0)], {FG: [op(1, run=6)]})
    with pytest.raises(ValueError, match="no working hours"):
        FlowSimulation(p, params())


# ---- calendar and timing ---------------------------------------------------------------------------------------
def test_single_operation_finishes_after_setup_and_run():
    p = plant([wc(1, "LASER_CUTTING")], {FG: [op(1, setup=30, run=6, batch=1000)]})
    lot = only_lot(run(p))
    assert lot["finish_h"] == pytest.approx(FIRST_SHIFT + 0.5 + 1.0)     # 0.5 h setup + 10 units x 6 min
    assert lot["due_h"] == RELEASE + 168.0 and lot["late_h"] == 0.0


def test_setup_is_charged_per_batch():
    p = plant([wc(1, "LASER_CUTTING")], {FG: [op(1, setup=30, run=0, batch=4)]})          # 10 units = 3 batches
    assert only_lot(run(p))["finish_h"] == pytest.approx(FIRST_SHIFT + 1.5)


def test_an_operation_that_outlasts_the_shift_continues_next_morning():
    p = plant([wc(1, "LASER_CUTTING")], {FG: [op(1, run=72)]}, demand=[(0, FG, 10.0)])    # 12 h of work, 8 h shift
    assert only_lot(run(p))["finish_h"] == pytest.approx(FIRST_SHIFT + 24.0 + 4.0)       # 8 h Monday, 4 h Tuesday


def test_availability_stretches_the_clock_time():
    p = plant([wc(1, "LASER_CUTTING", availability=0.5)], {FG: [op(1, run=9)]})          # 1.5 h of work at 50 %
    assert only_lot(run(p))["finish_h"] == pytest.approx(FIRST_SHIFT + 3.0)


def test_work_never_runs_on_the_weekend():
    p = plant([wc(1, "LASER_CUTTING")], {FG: [op(1, run=60)]}, demand=[(0, FG, 50.0)])   # 50 h: Mon-Fri gives 40 h
    # the other 10 h: 8 h on the following Monday (hour 342-350), 2 h on Tuesday (366-368)
    assert only_lot(run(p))["finish_h"] == pytest.approx(2 * 168.0 + 6.0 + 24.0 + 2.0)


# ---- components: kit gating ------------------------------------------------------------------------------------
def two_level(stock=None, receipts=None):
    return plant([wc(1, "ASSEMBLY"), wc(2, "BENDING")], {FG: [op(1, run=6)], SUB: [op(2, run=12)]},
                 bom={FG: [(SUB, 1.0, 0.0)]}, stock=stock, receipts=receipts)


def test_finished_good_waits_for_its_component_job():
    result = run(two_level())
    sub = next(j for j in result.jobs.values() if j.item_id == SUB)
    assert sub.release_h == RELEASE - 24.0 and sub.done_h == pytest.approx(FIRST_SHIFT + 2.0)   # 10 x 12 min
    fg = only_lot(result)
    assert fg["finish_h"] == pytest.approx(FIRST_SHIFT + 2.0 + 1.0)
    assert result.metrics["component_wait_days"] == pytest.approx((FIRST_SHIFT + 2.0 - RELEASE) / 24.0)


def test_component_in_stock_removes_the_child_job():
    result = run(two_level(stock={SUB: 10.0}))
    assert [j.item_id for j in result.jobs.values()] == [FG]
    assert only_lot(result)["finish_h"] == pytest.approx(FIRST_SHIFT + 1.0)


def test_partial_stock_makes_only_the_shortfall():
    result = run(two_level(stock={SUB: 4.0}))
    sub = next(j for j in result.jobs.values() if j.item_id == SUB)
    assert sub.qty == pytest.approx(6.0)


def steel_plant(**kwargs):
    return plant([wc(1, "ASSEMBLY")], {FG: [op(1, run=6)]}, bom={FG: [(STEEL, 1.0, 0.0)]}, **kwargs)


def test_purchased_part_in_stock_starts_immediately():
    assert only_lot(run(steel_plant(stock={STEEL: 10.0})))["finish_h"] == pytest.approx(FIRST_SHIFT + 1.0)


def test_purchased_part_waits_for_its_scheduled_receipt():
    result = run(steel_plant(receipts={STEEL: [(200.0, 10.0)]}))
    assert only_lot(result)["finish_h"] == pytest.approx(200.0 + 1.0)                   # Tuesday 08:00 is inside the shift


def test_a_receipt_after_the_due_date_does_not_supply_the_job():
    result = run(steel_plant(receipts={STEEL: [(RELEASE + 168.0 + 1.0, 10.0)]}))              # one hour after the lot is due
    assert only_lot(result)["finish_h"] == pytest.approx(FIRST_SHIFT + 1.0)
    assert result.metrics["purchased_shortage_lines"] == 1


def test_purchased_part_with_no_supply_is_assumed_available_at_release_and_counted():
    result = run(steel_plant())
    assert only_lot(result)["finish_h"] == pytest.approx(FIRST_SHIFT + 1.0)
    assert result.metrics["purchased_shortage_lines"] == 1


def test_purchased_part_with_no_supply_can_be_given_an_unplanned_purchase_lead_time():
    result = run(steel_plant(), params(unplanned_purchase_lead_days=14.0))
    assert only_lot(result)["finish_h"] == pytest.approx(RELEASE + 14 * 24.0 + 6.0 + 1.0)    # Monday hour 504, shift at 510


def test_bom_scrap_inflates_purchased_quantity_only():
    p = plant([wc(1, "ASSEMBLY")], {FG: [op(1, run=6)]}, bom={FG: [(STEEL, 2.0, 0.5)]}, stock={STEEL: 25.0})
    result = run(p)
    assert result.jobs[0].kit_lines == [(STEEL, pytest.approx(25.0), "STOCK", 0.0), (STEEL, pytest.approx(15.0), "SHORTAGE", RELEASE)]


# ---- colour changeover and batching ----------------------------------------------------------------------------
def coating_plant():
    return plant([wc(1, "POWDER_COATING")], {FG: [op(1, run=6)]}, demand=[(0, FG, 80.0)])


def colour_sequence(result):
    jobs = sorted(result.jobs.values(), key=lambda j: j.op_log[0][1])
    return [j.colour for j in jobs]


def test_a_colour_change_costs_the_changeover_and_the_same_colour_a_reload():
    prm = params(max_lot_qty=10.0, colours=(("A", 0.5), ("B", 0.5)), changeover_minutes=40.0, same_colour_setup_minutes=5.0)
    result = run(coating_plant(), prm)
    sequence = colour_sequence(result)
    changes = sum(1 for a, b in zip(sequence, sequence[1:]) if a != b)
    assert changes >= 2 and result.metrics["changeovers"] == changes
    assert result.metrics["changeover_hours"] == pytest.approx(changes * 40 / 60)
    first = result.jobs[0]
    assert first.op_log[0][5] == pytest.approx(5 / 60)                                   # first job: no change, reload only


def test_batching_by_colour_needs_one_changeover_per_extra_colour():
    prm = params(max_lot_qty=10.0, colours=(("A", 0.5), ("B", 0.5)))
    today = run(coating_plant(), prm)
    batched = run(coating_plant(), prm, Policy(colour_window_days=10.0))
    assert len(set(colour_sequence(batched))) == 2
    assert batched.metrics["changeovers"] == 1
    assert batched.metrics["changeovers"] < today.metrics["changeovers"]
    assert batched.metrics["coating_setup_hours"] < today.metrics["coating_setup_hours"]


def test_a_zero_window_does_not_pull_later_due_work_forward():
    # two due dates a week apart: window 0 must not group across them; a long window does
    p = plant([wc(1, "POWDER_COATING")], {FG: [op(1, run=6)]}, demand=[(0, FG, 20.0), (1, FG, 20.0)])
    prm = params(max_lot_qty=10.0, colours=(("A", 0.5), ("B", 0.5)))
    zero = run(p, prm, Policy(colour_window_days=0.0))
    wide = run(p, prm, Policy(colour_window_days=30.0))
    order_zero = sorted(zero.jobs.values(), key=lambda j: j.op_log[0][1])
    assert [j.due_h for j in order_zero] == sorted(j.due_h for j in order_zero)          # still earliest-due-date between weeks
    assert wide.metrics["changeovers"] <= zero.metrics["changeovers"]


# ---- scrap -----------------------------------------------------------------------------------------------------
def assembly_plant():
    """FG needs 2 SUB per unit; SUB needs 1 STEEL. Unit values: SUB = 10 + 6 = 16, FG = 2 x 16 + 6 = 38."""
    return plant([wc(1, "ASSEMBLY"), wc(2, "BENDING"), wc(3, "INSPECTION", cost=0.0)],
                 {FG: [op(1, run=6), op(3, "Inspection", run=0)], SUB: [op(2, run=6)]},
                 bom={FG: [(SUB, 2.0, 0.0), ], SUB: [(STEEL, 1.0, 0.0)]}, stock={STEEL: 1000.0}, demand=[(0, FG, 10.0)])


def defect_params(**kw):
    return params(defect_rate_default=1.0, max_rework_rounds=1, **kw)


def test_final_inspection_scraps_the_whole_finished_good_and_remakes_it():
    result = run(assembly_plant(), defect_params(), Policy(defects=True, scrap_mode="final"))
    m = result.metrics
    assert m["scrapped_units"] == 10 and m["scrap_value"] == pytest.approx(10 * 38.0)
    assert m["remake_jobs"] == 1 and m["unfinished_jobs"] == 0
    assert only_lot(result)["finish_h"] == pytest.approx(FIRST_SHIFT + 3.0 + 3.0)         # first pass 3 h, then the remake 3 h


def test_replacing_only_the_failed_component_loses_less_than_scrapping_the_assembly():
    result = run(assembly_plant(), defect_params(), Policy(defects=True, scrap_mode="replace"))
    m = result.metrics
    assert m["scrapped_units"] == 20 and m["scrap_value"] == pytest.approx(20 * 16.0)    # the bad SUB units only
    assert m["scrap_value"] < 10 * 38.0 and m["unfinished_jobs"] == 0
    fg = result.jobs[0]
    assert [o.kind for o in fg.ops] == ["ROUTING", "ROUTING", "REWORK", "REINSPECT"]    # assemble, inspect, then rework and re-check
    assert any(o.name == "Re-inspection" for o in fg.ops)


def test_component_inspection_remakes_only_the_bad_units_and_the_parent_waits():
    result = run(assembly_plant(), defect_params(), Policy(defects=True, scrap_mode="component"))
    m = result.metrics
    assert m["scrapped_units"] == 20 and m["scrap_value"] == pytest.approx(20 * 16.0) and m["unfinished_jobs"] == 0
    remakes = [j for j in result.jobs.values() if j.kind == "REMAKE"]
    assert len(remakes) == 1 and remakes[0].item_id == SUB and remakes[0].qty == pytest.approx(20.0)
    assert result.jobs[0].kit_ready_h >= remakes[0].done_h - 1e-9                         # the finished good waited for them


def test_without_defects_nothing_is_scrapped_whatever_the_probability():
    result = run(assembly_plant(), defect_params(), Policy(defects=False))
    assert result.metrics["scrapped_units"] == 0 and result.metrics["remake_jobs"] == 0


def test_results_are_reproducible_for_a_seed_and_differ_between_seeds():
    prm = params(defect_rate_default=0.3, max_rework_rounds=3, colours=(("A", 0.5), ("B", 0.5)))
    policy = Policy(defects=True, scrap_mode="final")
    a, b = run(assembly_plant(), prm, policy, seed=7), run(assembly_plant(), prm, policy, seed=7)
    assert a.metrics == b.metrics
    assert {run(assembly_plant(), prm, policy, seed=s).metrics["scrapped_units"] for s in range(8)} != {a.metrics["scrapped_units"]}


# ---- kit priority ----------------------------------------------------------------------------------------------
def priority_plant():
    """Two finished goods. FGA needs SUB-a (work centre 2) and a slow part from work centre 3; FGB needs only SUB-b
    (work centre 2). With plain earliest-due-date work centre 2 serves FGA's part first although FGA cannot start
    until the slow part is made."""
    fga, fgb, sa, sb, slow = 1, 4, 2, 5, 6
    return plant([wc(1, "ASSEMBLY"), wc(2, "BENDING"), wc(3, "WELDING")],
                 {fga: [op(1, run=6)], fgb: [op(1, run=6)], sa: [op(2, run=60)], sb: [op(2, run=60)], slow: [op(3, run=300)]},
                 bom={fga: [(sa, 1.0, 0.0), (slow, 1.0, 0.0)], fgb: [(sb, 1.0, 0.0)]},
                 demand=[(0, fga, 10.0), (0, fgb, 10.0)]), fgb


def finish_of(result, item):
    return next(lot["finish_h"] for lot in result.fg_lots if lot["item_id"] == item)


def test_kit_priority_serves_the_component_that_unblocks_an_order_first():
    p, fgb = priority_plant()
    plain = run(p)
    boosted = run(p, policy=Policy(kit_priority=True))
    assert finish_of(boosted, fgb) < finish_of(plain, fgb)


def test_kit_priority_does_not_change_a_plant_without_competition():
    p = two_level()
    assert run(p, policy=Policy(kit_priority=True)).metrics["makespan_days"] == run(p).metrics["makespan_days"]


# ---- overtime --------------------------------------------------------------------------------------------------
def busy_plant():
    return plant([wc(1, "LASER_CUTTING", cost=60.0)], {FG: [op(1, run=60)]}, demand=[(0, FG, 60.0)])   # four 15 h lots


def test_unconditional_overtime_is_paid_even_when_there_is_nothing_to_do():
    p = plant([wc(1, "LASER_CUTTING", cost=60.0)], {FG: [op(1, run=6)]})
    result = run(p, policy=Policy(overtime="unconditional", overtime_work_centres=(1,), overtime_hours=8.0))
    m = result.metrics
    assert m["overtime_paid_h"] == 24.0 and m["overtime_busy_h"] == 0.0 and m["overtime_idle_h"] == 24.0
    assert m["overtime_cost"] == pytest.approx(24.0 * 60.0 * 1.5)


def test_gated_overtime_is_not_approved_when_no_work_is_waiting():
    p = plant([wc(1, "LASER_CUTTING", cost=60.0)], {FG: [op(1, run=6)]})
    result = run(p, policy=Policy(overtime="gated", overtime_work_centres=(1,)))
    assert result.metrics["overtime_paid_h"] == 0.0 and result.overtime == []


def test_overtime_with_waiting_work_is_productive_and_finishes_earlier():
    prm = params(max_lot_qty=15.0)
    plain = run(busy_plant(), prm)
    gated = run(busy_plant(), prm, Policy(overtime="gated", overtime_work_centres=(1,), overtime_hours=8.0))
    week1 = next(o for o in gated.overtime if o["week"] == 1)
    assert week1["paid_h"] == 8.0 and week1["busy_h"] == pytest.approx(8.0) and week1["idle_h"] == pytest.approx(0.0)
    assert gated.metrics["makespan_days"] < plain.metrics["makespan_days"]


def test_gated_overtime_skips_work_whose_consumer_is_missing_other_parts():
    """The Saturday job feeds an assembly that also needs a part which will not exist by Monday: paying for it
    would only create stock that waits. Gated overtime must refuse it; unconditional overtime pays regardless."""
    slow, other = 6, 7
    p = plant([wc(1, "ASSEMBLY"), wc(2, "BENDING", cost=60.0), wc(3, "WELDING")],
              {FG: [op(1, run=6)], SUB: [op(2, run=60)], slow: [op(3, run=3000)], other: [op(2, run=0.0)]},
              bom={FG: [(SUB, 1.0, 0.0), (slow, 1.0, 0.0)]}, demand=[(0, FG, 10.0)])
    prm = params(max_lot_qty=10.0, level_offset_days=7.0)
    gated = run(p, prm, Policy(overtime="gated", overtime_work_centres=(2,), overtime_hours=8.0))
    unconditional = run(p, prm, Policy(overtime="unconditional", overtime_work_centres=(2,), overtime_hours=8.0))
    assert gated.metrics["overtime_paid_h"] < unconditional.metrics["overtime_paid_h"]
    assert unconditional.metrics["overtime_idle_h"] > 0


# ---- invariants ------------------------------------------------------------------------------------------------
def busy_chain():
    return plant([wc(1, "ASSEMBLY"), wc(2, "LASER_CUTTING"), wc(3, "POWDER_COATING")],
                 {FG: [op(1, run=6)], SUB: [op(2, "Laser", run=18, setup=20, batch=7), op(3, "Paint", run=24)]},
                 bom={FG: [(SUB, 1.0, 0.0)], SUB: [(STEEL, 1.0, 0.0)]}, demand=[(0, FG, 130.0), (1, FG, 90.0)])


def test_no_work_centre_runs_two_operations_at_once_and_nothing_runs_outside_a_shift():
    prm = params(max_lot_qty=10.0, colours=(("A", 0.5), ("B", 0.5)), seed=11)
    result = run(busy_chain(), prm)
    per_wc = {}
    for job in result.jobs.values():
        for index, start, end, work_h, wc_id, _, _ in job.op_log:
            per_wc.setdefault(wc_id, []).append((start, end))
            assert end >= start + work_h - 1e-9                                       # availability 1: never faster than the work
            for t in (start, end):
                day, within = divmod(t % 168.0, 24.0)
                assert day < 5, (t, wc_id)                                              # Monday to Friday only
                assert 6.0 - 1e-9 <= within <= 14.0 + 1e-9, (t, wc_id)                # only 06:00-14:00
    for wc_id, spans in per_wc.items():
        spans.sort()
        assert all(a_end <= b_start + 1e-9 for (_, a_end), (b_start, _) in zip(spans, spans[1:])), wc_id


def test_lots_cover_the_demand_net_of_finished_stock():
    p = busy_chain()
    p.stock = {FG: 25.0}
    result = run(p, params(max_lot_qty=10.0))
    assert sum(lot["qty"] for lot in result.fg_lots) == pytest.approx(130.0 + 90.0 - 25.0)
    assert all(lot["qty"] <= 10.0 + 1e-9 for lot in result.fg_lots)


def test_processing_hours_equal_the_work_in_the_operation_log():
    result = run(busy_chain(), params(max_lot_qty=10.0))
    logged = sum(entry[3] for job in result.jobs.values() for entry in job.op_log)
    assert result.metrics["processing_hours"] == pytest.approx(logged)
    assert result.metrics["unfinished_jobs"] == 0


def test_every_job_finishes_for_any_combination_of_policies():
    prm = params(max_lot_qty=10.0, colours=(("A", 0.5), ("B", 0.5)), defect_rate_default=0.2, max_rework_rounds=2)
    for mode in ("final", "replace", "component"):
        for overtime in ("none", "unconditional", "gated"):
            policy = Policy(defects=True, scrap_mode=mode, kit_priority=True, colour_window_days=5.0, overtime=overtime,
                            overtime_work_centres=(2, 3))
            result = run(busy_chain(), prm, policy, seed=5)
            assert result.metrics["unfinished_jobs"] == 0, (mode, overtime)


def test_monday_idle_counts_the_paid_worker_who_finds_nothing_to_do():
    p = plant([wc(1, "LASER_CUTTING")], {FG: [op(1, run=6)]})                         # 1 h of work in the first week
    policy = Policy(overtime="unconditional", overtime_work_centres=(1,), overtime_hours=8.0)
    m = run(p, policy=policy).metrics
    assert m["monday_idle_h"] == pytest.approx(7.0 + 8.0)                             # weeks 1 and 2: 7 h and 8 h idle
    assert m["monday_idle_after_overtime_h"] == pytest.approx(15.0)                   # both follow a paid Saturday
    assert run(p, policy=Policy(overtime_work_centres=(1,))).metrics["monday_idle_after_overtime_h"] == 0.0
