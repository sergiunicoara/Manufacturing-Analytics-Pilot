"""Dashboard, scenario, story, and evidence-gated copilot HTTP surface."""
from __future__ import annotations

import datetime as dt
import re

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field

from app.analytics import dashboard, parameter_export, stage_history
from app.analytics.buffers import recommend_buffers
from app.analytics.evidence import evidence, json_value, source
from app.analytics.period_engine import run_period_engine
from app.analytics.scenario_demo import build_engine_inputs, cab100_item_ids, items_loading_work_centres
from app.analytics.scenario_store import save_run
from app import security
from app.config import settings
from app.db.connection import get_engine

router = APIRouter(dependencies=[Depends(security.require_reader)])


def _page(slug: str, item_id: int | None = None):
    fn = dashboard.PAGES.get(slug)
    if fn is None:
        raise HTTPException(status_code=404, detail="Unknown dashboard page")
    try:
        return fn(item_id) if slug == "bom-explorer" else fn()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Analytics unavailable: {exc}") from exc


@router.get("/api/pages/{slug}")
def page(slug: str, item_id: int | None = None):
    return _page(slug, item_id)


@router.get("/api/stage-performance/records")
def stage_performance_records(work_centre_id: int | None = None, record_class: str | None = None,
                              offset: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=1000)):
    if record_class is not None and record_class not in stage_history.RECORD_CLASSES:
        raise HTTPException(status_code=422, detail=f"record_class must be one of {stage_history.RECORD_CLASSES}")
    try:
        return dashboard.stage_records(work_centre_id, record_class, offset, limit)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Analytics unavailable: {exc}") from exc


@router.get("/api/dq/findings")
def dq_findings(rule_id: str | None = None, classification: str | None = None, origin: str | None = None,
                entity: str | None = None, offset: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=1000)):
    try:
        return dashboard.dq_findings(rule_id, classification, origin, entity, offset, limit)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Analytics unavailable: {exc}") from exc


@router.get("/api/dq/findings.csv")
def dq_findings_csv(rule_id: str | None = None, classification: str | None = None, origin: str | None = None,
                    entity: str | None = None):
    try:
        body = dashboard.dq_findings_csv(rule_id, classification, origin, entity)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Analytics unavailable: {exc}") from exc
    return Response(body, media_type="text/csv",
                    headers={"Content-Disposition": 'attachment; filename="dq_findings.csv"'})


@router.get("/api/parameters/package.json")
def parameter_package_json():
    try:
        package = dashboard.parameter_package()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Analytics unavailable: {exc}") from exc
    return Response(parameter_export.to_json(package), media_type="application/json",
                    headers={"Content-Disposition": f'attachment; filename="{package.package_id}.json"'})


@router.get("/api/parameters/package.csv")
def parameter_package_csv():
    try:
        package = dashboard.parameter_package()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Analytics unavailable: {exc}") from exc
    return Response(parameter_export.to_csv(package), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="{package.package_id}.csv"'})


@router.get("/api/parameters/schema.json")
def parameter_package_schema():
    return Response(parameter_export.json_schema(), media_type="application/schema+json")


@router.get("/kpi/plant-overview")
def plant_overview():
    return _page("plant-overview")


class ScenarioRequest(BaseModel):
    name: str = Field(default="CUSTOM", min_length=1, max_length=200)
    demand_multiplier: float = Field(default=1.4, ge=0.5, le=3.0)
    buffer_boost_per_item: float = Field(default=0.0, ge=0, le=10000)
    capacity_multiplier: float = Field(default=1.0, ge=1.0, le=3.0)
    intervention_start_week: int = Field(default=4, ge=1, le=12)


@router.post("/api/scenarios/run", dependencies=[Depends(security.require_operator)])
def run_scenario(request: ScenarioRequest):
    try:
        tables, comparison = dashboard.context()
        inputs = build_engine_inputs(tables, list(dashboard.HORIZON))
        ids = cab100_item_ids(tables["items"])
        inputs["demand_multiplier_by_item"] = {item_id: request.demand_multiplier for item_id in ids}
        wc_ids = comparison["emergent_constraints"]
        buffer_ids = items_loading_work_centres(tables["routing_headers"], tables["routing_operations"], wc_ids)
        if request.buffer_boost_per_item:
            inputs["buffer_boost_by_item"] = {item_id: request.buffer_boost_per_item for item_id in buffer_ids}
        if request.capacity_multiplier > 1:
            starts = dashboard.HORIZON[request.intervention_start_week - 1]
            inputs["capacity_multiplier_by_work_centre"] = {wc_id: (request.capacity_multiplier, starts) for wc_id in wc_ids}
        result = run_period_engine(**inputs)
        intervention_type = ("COMBINED" if request.buffer_boost_per_item and request.capacity_multiplier > 1 else
                             "BUFFER_ONLY" if request.buffer_boost_per_item else
                             "CAPACITY_ONLY" if request.capacity_multiplier > 1 else "NONE")
        recs = recommend_buffers(tables, result)
        run_id = save_run(get_engine(), request.name, request.model_dump(), result, intervention_type, recs,
                          comparison.get("data_version"))
        final = [r for r in result.work_centre_results if r.period_start_date == dashboard.HORIZON[-1]]
        backlog = sum(r.backlog_hours_end for r in final)
        return {"run_id": run_id, "intervention_type": intervention_type,
                "metrics": [dashboard.metric("Final backlog hours", round(backlog, 2),
                            "ANALYTICS_METHODS.md#period-engine",
                            [source("final backlog by work centre", [r.backlog_hours_end for r in final], run_id, "DERIVED")])],
                "recommendation_count": len(recs),
                "evidence": evidence(run_id, "ANALYTICS_METHODS.md#scenario-persistence",
                                     [source("parameters", request.model_dump(), run_id, "ASSUMED"),
                                      source("seed", settings.synthetic_seed, run_id, "ASSUMED")])}
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Scenario run unavailable: {exc}") from exc


class ForecastChangeRequest(BaseModel):
    day: float = Field(default=14.0, ge=-30, le=120)
    from_week: int = Field(default=2, ge=0, le=11)
    to_week: int | None = Field(default=None, ge=0, le=11)
    factor: float = Field(default=0.0, ge=0.0, le=3.0)
    families: list[str] = Field(default_factory=list, max_length=10)


@router.post("/api/flow/change-impact", dependencies=[Depends(security.require_operator)])
def flow_change_impact(request: ForecastChangeRequest):
    """Re-run the shop-floor simulation for a changed forecast and return the Order Change Impact page for it."""
    from app.analytics.flow.analysis import ForecastChange
    if request.to_week is not None and request.to_week < request.from_week:
        raise HTTPException(status_code=422, detail="to_week must not be before from_week")
    try:
        change = ForecastChange(day=request.day, from_week=request.from_week, to_week=request.to_week,
                                factor=request.factor, families=tuple(request.families))
        return dashboard.order_change_impact(change)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Change impact unavailable: {exc}") from exc


@router.get("/story")
def story():
    overview = _page("plant-overview")
    capacity = _page("capacity")
    scenarios = _page("scenario-lab")
    values = {r["label"]: r["value"] for r in scenarios["rows"]}
    top_wc = next((r for r in capacity["rows"] if r["classification"] == "PRIMARY_CONSTRAINT"),
                  capacity["rows"][0] if capacity["rows"] else None)
    return {"horizon_weeks": len(dashboard.HORIZON), "beats": [
        {"title": "The operating baseline", "narration": f"The modeled baseline ends with {overview['metrics'][0]['value']} backlog hours.",
         "evidence": overview["metrics"][0]["evidence"]},
        {"title": "Demand shock", "narration": f"A 40% CAB-100 demand shock ends with {values.get('DEMAND_SHOCK_ONLY')} backlog hours.",
         "evidence": next(r["evidence"] for r in scenarios["rows"] if r["label"] == "DEMAND_SHOCK_ONLY")},
        {"title": "Where capacity tightens", "narration": (f"{top_wc['label']} reaches {top_wc['value']}% utilization in the last week." if top_wc else "No work centre result is available."),
         "evidence": top_wc["evidence"] if top_wc else evidence(None, "ANALYTICS_METHODS.md#three-tier-capacity")},
        {"title": "Compare interventions", "narration": (f"Capacity only ends at {values.get('CAPACITY_ONLY')} backlog hours; combined ends at {values.get('COMBINED')} hours."),
         "evidence": next(r["evidence"] for r in scenarios["rows"] if r["label"] == "COMBINED")},
    ], "horizon_rationale": "Twelve weeks includes the shock and intervention response. Baseline deterioration is displayed in the comparison; it is not a stable control."}


COST_TERMS = ("cost", "capital", "expense", "carrying", "money", "eur")


class CopilotRequest(BaseModel):
    question: str = Field(min_length=3, max_length=1000)
    tool: str | None = None
    scenario: ScenarioRequest | None = None


def finding_concerns_item(finding: dict, item_id: str) -> bool:
    """A finding is about an item only when its entity is the item master or its blocking scope names that
    item; an inventory row or order that happens to share the number is a different record."""
    return ((finding["entity"] == "items" and str(finding["record_id"]) == item_id)
            or (finding.get("affected_entity_type") == "item" and str(finding.get("affected_entity_id")) == item_id))


def _deterministic_tool(request: CopilotRequest) -> tuple[str, dict | list]:
    question = request.question.lower()
    tool = request.tool
    if tool is None:
        tool = ("run_scenario" if request.scenario else
                "get_dq_findings" if any(s in question for s in ("quality", "finding", "blocking")) else
                "explain_recommendation" if any(s in question for s in ("recommend", "buffer")) else "get_kpi")
    item_match = re.search(r"\bitem\s*(\d+)\b", question)
    wc_match = re.search(r"\b(?:work\s*centre|wc)\s*(\d+)\b", question)
    code_match = re.search(r"\b[A-Z]{2,}(?:-[A-Z0-9]+)+\b", request.question)
    if tool != "run_scenario" and (item_match or wc_match or code_match):
        tables, _ = dashboard.context()
        if item_match and int(item_match.group(1)) not in set(tables["items"]["item_id"]):
            return tool, []
        if wc_match and int(wc_match.group(1)) not in set(tables["work_centres"]["work_centre_id"]):
            return tool, []
        if code_match and code_match.group(0) not in set(tables["items"]["item_code"].astype(str)):
            return tool, []
    if tool == "run_scenario":
        if request.scenario is None:
            return tool, {}
        return tool, run_scenario(request.scenario)
    if tool == "get_dq_findings":
        if item_match:
            findings = dashboard.dq_findings(limit=10_000)["findings"]
            matches = [f for f in findings if finding_concerns_item(f, item_match.group(1))]
            return tool, [{**f, "evidence": evidence(f["classification"], "ANALYTICS_METHODS.md#data-quality",
                                                     [source("rule", f["rule_id"], f["record_id"], "DERIVED"),
                                                      source("entity", f["entity"], f["record_id"])],
                                                     [], [f["description"]])} for f in matches[:20]]
        return tool, _page("data-quality")["rows"][:20]
    if tool == "get_cost":
        rows = _page("decision-economics")["rows"]
        case = next((c for c in dashboard.CASES if c.lower() in question or c.lower().replace("_", " ") in question), None)
        return tool, [r for r in rows if case is None or r["case"] == case]
    if tool == "explain_recommendation":
        data = _page("recommendation")
        matches = data["rows"]
        if item_match:
            matches = [r for r in matches if str(r["item_id"]) == item_match.group(1)]
        if code_match:
            matches = [r for r in matches if r["item_code"] == code_match.group(0)]
        return tool, matches[:10]
    if tool == "get_kpi":
        if item_match:
            data = _page("bom-explorer", int(item_match.group(1)))
            return tool, {} if data.get("error") else data
        if wc_match:
            rows = _page("capacity")["rows"]
            return tool, [r for r in rows if r["work_centre_id"] == int(wc_match.group(1))]
        if code_match:
            tables, _ = dashboard.context()
            rows = tables["items"].loc[tables["items"]["item_code"] == code_match.group(0)]
            if len(rows) != 1:
                return tool, []
            return tool, _page("bom-explorer", int(rows.iloc[0]["item_id"]))
        slug = "scenario-lab" if "scenario" in question or "shock" in question else "plant-overview"
        return tool, _page(slug)
    raise HTTPException(status_code=422, detail=f"Unknown copilot tool: {tool}")


def _has_evidence(payload) -> bool:
    if not payload:
        return False
    if isinstance(payload, list):
        return any(isinstance(row, dict) and row.get("evidence") for row in payload)
    if isinstance(payload, dict):
        return bool(payload.get("evidence") or payload.get("metrics") or payload.get("rows"))
    return False


def _requested_tools(request: CopilotRequest) -> list[str]:
    if request.tool:
        return [request.tool]
    if request.scenario:
        return ["run_scenario"]
    question = request.question.lower()
    chosen = []
    if any(term in question for term in ("quality", "finding", "blocking")):
        chosen.append("get_dq_findings")
    if any(term in question for term in ("recommend", "buffer")):
        chosen.append("explain_recommendation")
    if any(term in question for term in COST_TERMS):
        chosen.append("get_cost")
    if not chosen or any(term in question for term in ("kpi", "capacity", "backlog", "scenario", "shock")):
        chosen.insert(0, "get_kpi")
    return chosen


@router.post("/copilot/ask", dependencies=[Depends(security.require_operator)])
def copilot_ask(request: CopilotRequest):
    bundle = []
    for tool_name in _requested_tools(request):
        tool, payload = _deterministic_tool(request.model_copy(update={"tool": tool_name}))
        if not _has_evidence(payload):
            return {"answer": f"Insufficient evidence to answer — no matching data was returned by {tool} for {request.question}.",
                    "tool": tool, "evidence": bundle, "llm_used": False, "insufficient_evidence": True}
        bundle.append({"tool": tool, "result": json_value(payload)})
    if not security.llm_allowed():
        return {"answer": "Deterministic evidence is available below.", "tool": bundle[0]["tool"],
                "evidence": bundle, "llm_used": False, "insufficient_evidence": False}
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        response = client.messages.create(model=settings.anthropic_model, max_tokens=350,
            system="Explain only the supplied deterministic evidence. Do not invent or calculate metrics. State limitations plainly.",
            messages=[{"role": "user", "content": f"Question: {request.question}\nEvidence: {security.minimise_for_llm(bundle)}"}])
        answer = " ".join(block.text for block in response.content if block.type == "text")
        return {"answer": answer, "tool": bundle[0]["tool"], "evidence": bundle,
                "llm_used": True, "insufficient_evidence": False}
    except Exception as exc:
        return {"answer": f"Deterministic evidence is available; explanation service unavailable ({type(exc).__name__}).",
                "tool": bundle[0]["tool"], "evidence": bundle, "llm_used": False, "insufficient_evidence": False}
