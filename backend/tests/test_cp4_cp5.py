"""CP4/CP5 evidence, constraint gating, pages, and copilot refusal."""
from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

from app.analytics import dashboard
from app.analytics.buffers import recommend_buffers
from app.analytics.period_engine import MaterialRequirementPeriodResult, PeriodEngineOutput, WorkCentrePeriodResult
from app.api import CopilotRequest, _has_evidence, copilot_ask
from app.synthetic.timeline import REFERENCE_DATE


def _wc(classification):
    return WorkCentrePeriodResult(9, REFERENCE_DATE, 40, 40, 35, 45, 128.57,
        5, 15, 35, 12, 3, 5, 7, 5, .2, classification)


def _buffer_tables():
    return {"items": pd.DataFrame([{"item_id": 2, "item_code": "FRAME"}]),
            "routing_headers": pd.DataFrame([{"routing_id": 1, "item_id": 2}]),
            "routing_operations": pd.DataFrame([{"routing_id": 1, "work_centre_id": 9}])}


def test_buffer_requires_constraint_classification_and_preserves_formula():
    material = [MaterialRequirementPeriodResult(2, "FRAME", REFERENCE_DATE, 70, 0, 0, 70, True)]
    tables = _buffer_tables()
    assert recommend_buffers(tables, PeriodEngineOutput([_wc("OVERLOADED")], material)) == []
    recs = recommend_buffers(tables, PeriodEngineOutput([_wc("CANDIDATE_CONSTRAINT")], material))
    assert len(recs) == 1
    assert recs[0]["recommended_min"] + recs[0]["recommended_max"] == pytest.approx(200)
    assert recs[0]["recommended_min"] < 100 < recs[0]["recommended_max"]
    assert recs[0]["evidence"]["value"] == {"min": recs[0]["recommended_min"], "max": recs[0]["recommended_max"]}
    assert recs[0]["inputs"] and recs[0]["assumptions"]


def test_empty_evidence_is_rejected_and_llm_is_not_called(monkeypatch):
    assert not _has_evidence([])
    assert not _has_evidence({})
    monkeypatch.setattr("app.api._deterministic_tool", lambda request: ("get_kpi", []))
    answer = copilot_ask(CopilotRequest(question="Explain item 999999"))
    assert answer["insufficient_evidence"]
    assert answer["llm_used"] is False


@pytest.fixture(scope="module")
def computed_context(tables):
    from app.analytics.scenario_demo import run_four_intervention_comparison
    comparison = run_four_intervention_comparison(tables, list(dashboard.HORIZON))
    return tables, comparison


def test_every_page_supplies_evidence(monkeypatch, computed_context):
    monkeypatch.setattr(dashboard, "context", lambda: computed_context)
    monkeypatch.setattr(dashboard, "source_tables", lambda: computed_context[0])
    for slug, build in dashboard.PAGES.items():
        page = build()
        assert page["title"] and page["metrics"], slug
        assert all(m["evidence"]["formula"] for m in page["metrics"]), slug
        assert all(p["evidence"]["inputs"] for p in page["series"]), slug
        assert all(r["evidence"]["formula"] for r in page["rows"]), slug


def test_story_numbers_come_from_computed_results(monkeypatch, computed_context):
    from app.api import story
    monkeypatch.setattr(dashboard, "context", lambda: computed_context)
    monkeypatch.setattr(dashboard, "source_tables", lambda: computed_context[0])
    result = story()
    assert len(result["beats"]) == 4
    assert str(dashboard.scenarios()["rows"][1]["value"]) in result["beats"][1]["narration"]
    assert all(beat["evidence"]["formula"] for beat in result["beats"])


def test_cost_questions_route_to_the_deterministic_cost_tool():
    from app.api import CopilotRequest, _requested_tools
    assert "get_cost" in _requested_tools(CopilotRequest(question="What does the capacity option cost?"))
    assert "get_cost" not in _requested_tools(CopilotRequest(question="Which work centre is the constraint?"))


def test_cost_tool_returns_evidence_for_each_case(monkeypatch, computed_context):
    from app.api import CopilotRequest, _deterministic_tool, _has_evidence
    monkeypatch.setattr(dashboard, "context", lambda: computed_context)
    tool, rows = _deterministic_tool(CopilotRequest(question="combined cost", tool="get_cost"))
    assert tool == "get_cost" and [r["case"] for r in rows] == ["COMBINED"]
    assert _has_evidence(rows)
    assert rows[0]["wip_carrying_cost"] is None
