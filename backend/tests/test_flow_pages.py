"""The Shop Floor Flow and Order Change Impact pages, the change route, and a calibration of the simulator against
the weekly period engine (same demand, same routings: the hours must agree to within the documented differences)."""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.analytics import dashboard, flow_pages
from app.analytics.flow.analysis import ForecastChange
from app.analytics.flow.model import FlowParameters, Policy
from app.analytics.flow.plant import build_plant
from app.analytics.flow.sim import FlowSimulation


@pytest.fixture(scope="module")
def computed(tables):
    from app.analytics.scenario_demo import run_four_intervention_comparison
    return tables, run_four_intervention_comparison(tables, list(dashboard.HORIZON))


@pytest.fixture(scope="module")
def flow(computed):
    tables, comparison = computed
    return flow_pages.shop_floor_flow(tables, comparison, dashboard.HORIZON)


@pytest.fixture(scope="module")
def impact(computed):
    tables, comparison = computed
    return flow_pages.change_page(tables, comparison, dashboard.HORIZON)


def every_entry(page):
    return page["metrics"] + page["series"] + page["rows"]


def test_pages_are_registered_and_every_entry_has_evidence(flow, impact):
    assert {"shop-floor-flow", "order-change-impact"} <= set(dashboard.PAGES)
    for page in (flow, impact):
        assert page["data_origin"] == "SYNTHETIC" and page["metrics"] and page["rows"]
        for entry in every_entry(page):
            assert entry["evidence"]["formula"].startswith("ANALYTICS_METHODS.md#")
            assert entry["evidence"]["inputs"], entry["label"]
            assert entry["evidence"]["data_origin"] == "SYNTHETIC"


def test_assumptions_are_stated_in_every_drawer(flow, impact):
    text = " ".join(flow["metrics"][0]["evidence"]["assumptions"])
    for phrase in ("Colour does not exist in the data", "40 minutes", "No scrap or inspection results", "Overtime",
                   "Open production orders are not used", "released", "excluded"):
        assert phrase in text, phrase
    assert "{shortages}" not in text
    assert any("Work in process on a machine" in a for a in impact["metrics"][0]["evidence"]["assumptions"])


def test_flow_page_covers_every_policy(flow):
    labels = [row["label"] for row in flow["rows"]]
    assert len(labels) == 13 and labels[0].startswith("Today")
    assert any("Group colours" in label for label in labels) and any("Inspect each component" in label for label in labels)
    assert any("overtime" in label.lower() for label in labels)
    assert flow["overtime_work_centres"], "overtime is tested at the lever's work centres and the most queued one"


def test_headline_numbers_match_a_direct_run(computed, flow):
    tables, comparison = computed
    params = FlowParameters()
    plant = build_plant(tables, params, list(dashboard.HORIZON))
    direct = FlowSimulation(plant, params, Policy()).run().metrics
    assert flow["metrics"][0]["value"] == round(direct["late_lots"])
    assert flow["metrics"][1]["value"] == round(direct["changeovers"])


def test_grouping_colours_never_adds_changeovers(flow):
    today = next(m for m in flow["metrics"] if m["label"] == "Colour changes, today")["value"]
    changes = [r for r in flow["rows"] if r["label"].startswith("Group colours")]
    assert changes and all(r["value"] <= today for r in changes)         # a colour-group row's headline is its colour changes
    saved = next(m for m in flow["metrics"] if m["label"] == "Colour changes saved by grouping colours")
    assert saved["value"] >= 0


def test_scrap_ordering_matches_the_mechanism(flow):
    by = {r["label"]: r["value"] for r in flow["rows"]}
    whole = next(v for k, v in by.items() if "whole product scrapped" in k)
    replace = next(v for k, v in by.items() if "replace only the failed component" in k)
    inspect = next(v for k, v in by.items() if "Inspect each component" in k)
    assert whole > replace >= inspect >= 0, (whole, replace, inspect)


def test_change_page_conserves_value(impact):
    sunk, reusable, stranded = (impact["metrics"][i]["value"] for i in range(3))
    assert sunk >= 0 and reusable >= 0
    assert stranded == pytest.approx(sunk - reusable, abs=1.5)                         # metrics are rounded to whole euros
    labels = [r["label"] for r in impact["rows"]]
    assert "Stock point · After laser cutting" in labels and "Stock point · Before painting" in labels
    assert any(label.startswith("At the change · ") for label in labels)


def test_change_route_returns_the_page_for_the_chosen_change(computed, monkeypatch):
    from app.api import ForecastChangeRequest, flow_change_impact
    monkeypatch.setattr(dashboard, "context", lambda: computed)
    page = flow_change_impact(ForecastChangeRequest(day=20, from_week=1, factor=0.5, families=["CAB-200"]))
    assert page["title"] == "Order Change Impact"
    assert page["change"]["factor"] == 0.5 and page["change"]["families"] == ["CAB-200"] and page["change"]["day"] == 20


def test_change_route_rejects_an_impossible_range(computed, monkeypatch):
    from app.api import ForecastChangeRequest, flow_change_impact
    monkeypatch.setattr(dashboard, "context", lambda: computed)
    with pytest.raises(HTTPException) as err:
        flow_change_impact(ForecastChangeRequest(from_week=5, to_week=2))
    assert err.value.status_code == 422


def test_change_request_limits_are_enforced():
    from pydantic import ValidationError
    from app.api import ForecastChangeRequest
    for bad in ({"factor": -0.1}, {"factor": 3.5}, {"from_week": 12}, {"day": 500}):
        with pytest.raises(ValidationError):
            ForecastChangeRequest(**bad)


def test_the_change_route_requires_the_operator_role():
    from app.api import router
    route = next(r for r in router.routes if getattr(r, "path", "") == "/api/flow/change-impact")
    assert any("require_operator" in str(d.call) for d in route.dependant.dependencies)


# ---- calibration against the weekly period engine -------------------------------------------------------------
def test_simulated_processing_hours_stay_close_to_the_period_engine(computed):
    """Both read the same demand, stock and routings. The simulator nets lot by lot instead of week by week and starts
    with an empty shop, so a gap is expected; a large one would mean one of them is wrong. Documented in
    ANALYTICS_METHODS.md#shop-floor-flow (measured on the full data set: simulator about 0.9 of the engine's hours)."""
    tables, comparison = computed
    params = FlowParameters()
    weeks = list(dashboard.HORIZON)[:params.horizon_weeks]
    engine = sum(r.required_hours for r in comparison["BASELINE"].work_centre_results if r.period_start_date in weeks)
    plant = build_plant(tables, params, list(dashboard.HORIZON))
    simulated = FlowSimulation(plant, params, Policy()).run().metrics["processing_hours"]
    assert 0.6 <= simulated / engine <= 1.15, (simulated, engine)


def test_the_simulator_does_not_touch_the_engines_inputs(computed):
    tables, comparison = computed
    before = {k: v.copy() for k, v in tables.items() if k in ("routing_operations", "bom_components", "inventory")}
    params = FlowParameters()
    plant = build_plant(tables, params, list(dashboard.HORIZON))
    FlowSimulation(plant, params, Policy(defects=True, scrap_mode="replace")).run()
    for name, frame in before.items():
        assert frame.equals(tables[name]), name
    assert plant.problems is not None and plant.excluded_demand >= 0


def test_excluded_items_and_demand_are_reported_not_hidden(computed):
    tables, _ = computed
    plant = build_plant(tables, FlowParameters(), list(dashboard.HORIZON))
    assert all(reason for _, reason in plant.problems)
    assert plant.excluded_demand >= 0 and isinstance(plant.skipped_operations, dict)
