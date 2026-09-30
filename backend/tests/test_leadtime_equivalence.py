"""The indexed LeadTimeCalculator must return exactly what the original pandas implementation returned."""
import datetime as dt
import math

import pytest

from app.analytics.leadtime import LeadTimeCalculator, compute_lead_time_for_item
from app.analytics.period_engine import run_period_engine
from app.analytics.scenario_demo import build_engine_inputs, cab100_item_ids
from app.synthetic.timeline import REFERENCE_DATE
from tests.reference_leadtime import reference_compute_lead_time_for_item

HORIZON = [REFERENCE_DATE + dt.timedelta(weeks=w) for w in range(12)]
SAMPLE_WEEKS = (0, 3, 6, 9, 11)


@pytest.fixture(scope="module")
def cases(tables):
    inputs = build_engine_inputs(tables, HORIZON)
    baseline = run_period_engine(**inputs)
    shocked_inputs = dict(inputs)
    shocked_inputs["demand_multiplier_by_item"] = {i: 1.4 for i in cab100_item_ids(tables["items"])}
    shocked_inputs["capacity_multiplier_by_work_centre"] = {9: (1.1, HORIZON[4])}
    return {"baseline": baseline, "shock+capacity": run_period_engine(**shocked_inputs)}


def _same(new, old):
    if old is None or new is None:
        return new is None and old is None
    fields = ("processing_days", "queue_days", "transfer_days", "total_days")
    return (all(math.isclose(getattr(new, f), getattr(old, f), rel_tol=1e-12, abs_tol=1e-12) for f in fields)
            and new.route_item_ids == old.route_item_ids)


def test_indexed_lead_time_matches_the_original_implementation(tables, cases):
    items = tables["items"]
    fg = items.loc[items["item_type"] == "FG", "item_id"].astype(int).tolist()
    subassy = items.loc[items["item_type"] == "SUBASSY", "item_id"].astype(int).tolist()[::25]
    calc = LeadTimeCalculator(tables["routing_headers"], tables["routing_operations"], items,
                              tables["bom_headers"], tables["bom_components"])
    compared = with_queue = none_results = 0
    for name, output in cases.items():
        by_period = {(r.work_centre_id, r.period_start_date): r for r in output.work_centre_results}
        for item_id in fg + subassy:
            for week in SAMPLE_WEEKS:
                period = HORIZON[week]
                new = calc.compute(item_id, period, by_period)
                old = reference_compute_lead_time_for_item(
                    item_id, period, tables["routing_headers"], tables["routing_operations"], by_period, items,
                    tables["bom_headers"], tables["bom_components"])
                assert _same(new, old), (name, item_id, period, new, old)
                compared += 1
                none_results += new is None
                with_queue += new is not None and new.queue_days > 0
    assert compared > 1000
    assert with_queue > 0 and none_results >= 0     # queue term exercised; None (unmeasurable) paths agree too


def test_wrapper_and_calculator_agree_and_scenarios_differ_only_in_queue(tables, cases):
    items = tables["items"]
    args = (tables["routing_headers"], tables["routing_operations"])
    extra = (items, tables["bom_headers"], tables["bom_components"])
    period = HORIZON[9]
    maps = {n: {(r.work_centre_id, r.period_start_date): r for r in o.work_centre_results} for n, o in cases.items()}
    for item_id in cab100_item_ids(items):          # some routes are unmeasurable (data-quality gaps): skip those
        base = compute_lead_time_for_item(item_id, period, *args, maps["baseline"], *extra)
        shock = compute_lead_time_for_item(item_id, period, *args, maps["shock+capacity"], *extra)
        if base is not None and shock is not None:
            break
    else:
        pytest.fail("no measurable CAB-100 item found")
    assert base.processing_days == pytest.approx(shock.processing_days)
    assert base.transfer_days == pytest.approx(shock.transfer_days)
    assert shock.queue_days >= base.queue_days
