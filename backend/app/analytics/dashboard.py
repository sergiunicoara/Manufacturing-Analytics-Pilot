"""Read models for the nine dashboard pages; every displayed value has evidence."""
from __future__ import annotations

import datetime as dt
from threading import Lock

import pandas as pd

from app.analytics.bom import explode
from app.analytics.buffers import recommend_buffers
from app.analytics.data_access import load_all_tables
from app.analytics.evidence import evidence, json_value, source
from app.analytics.forecast import compute_accuracy_by_horizon, reconstruct_forecast_history
from app.analytics.scenario_demo import build_engine_inputs, cab100_item_ids, run_four_intervention_comparison
from app.analytics.scenario_store import ensure_run
from app.db.connection import get_engine
from app.dq.engine import run_all
from app.synthetic.timeline import REFERENCE_DATE

HORIZON = tuple(REFERENCE_DATE + dt.timedelta(weeks=w) for w in range(12))
CASES = ("BASELINE", "DEMAND_SHOCK_ONLY", "BUFFER_ONLY", "CAPACITY_ONLY", "COMBINED")


_context_lock = Lock()
_context_value = None


def context():
    """Compute once even when several page requests arrive together."""
    global _context_value
    if _context_value is None:
        with _context_lock:
            if _context_value is None:
                _context_value = _build_context()
    return _context_value


def _build_context():
    engine = get_engine()
    tables = load_all_tables(engine)
    comparison = run_four_intervention_comparison(tables, list(HORIZON), demand_multiplier=1.4)
    for case in CASES:
        params = {"horizon_start": HORIZON[0], "horizon_weeks": len(HORIZON),
                  "demand_multiplier": 1.0 if case == "BASELINE" else 1.4,
                  "buffer_boost_per_item": 40.0 if case in {"BUFFER_ONLY", "COMBINED"} else 0.0,
                  "capacity_multipliers": comparison["per_work_centre_multipliers"] if case in {"CAPACITY_ONLY", "COMBINED"} else {},
                  "intervention_start_week": comparison["intervention_start_week"]}
        intervention = case if case in {"BUFFER_ONLY", "CAPACITY_ONLY", "COMBINED"} else "NONE"
        ensure_run(engine, case, params, comparison[case], intervention,
                   recommend_buffers(tables, comparison[case]) if case == "DEMAND_SHOCK_ONLY" else [])
    return tables, comparison


def metric(label, value, formula, inputs=None, trace=None, assumptions=None):
    return {"label": label, "value": json_value(value), "evidence": evidence(value, formula, inputs, trace, assumptions)}


def point(label, value, formula, inputs=None, trace=None, assumptions=None, **extra):
    return {"label": str(label), "value": json_value(value), **extra,
            "evidence": evidence(value, formula, inputs, trace, assumptions)}


def plant_overview():
    tables, runs = context()
    base = runs["BASELINE"]
    final = [r for r in base.work_centre_results if r.period_start_date == HORIZON[-1]]
    backlog = sum(r.backlog_hours_end for r in final)
    wip = sum(r.wip_qty for r in final)
    constrained = sum(r.constraint_classification in {"CANDIDATE_CONSTRAINT", "PRIMARY_CONSTRAINT"} for r in final)
    metrics = [metric("Ending backlog hours", round(backlog, 1), "ANALYTICS_METHODS.md#period-engine",
                      [source("work centre ending backlog", [r.backlog_hours_end for r in final], "period_engine_results", "DERIVED")]),
               metric("Ending WIP units estimate", round(wip, 1), "ANALYTICS_METHODS.md#period-engine",
                      [source("work centre WIP estimates", [r.wip_qty for r in final], "period_engine_results", "DERIVED")]),
               metric("Constrained work centres", constrained, "ANALYTICS_METHODS.md#constraint-classification",
                      [source("final classifications", [r.constraint_classification for r in final], "period_engine_results", "DERIVED")]),
               metric("Data quality findings", len(run_all(tables)), "ANALYTICS_METHODS.md#data-quality",
                      [source("rule engine findings", len(run_all(tables)), "dq_findings", "DERIVED")])]
    trend = []
    for date in HORIZON:
        rows = [r for r in base.work_centre_results if r.period_start_date == date]
        trend.append(point(date.isoformat(), round(sum(r.backlog_hours_end for r in rows), 2),
                           "ANALYTICS_METHODS.md#period-engine",
                           [source("ending backlog hours by work centre", [r.backlog_hours_end for r in rows], date, "DERIVED")]))
    return {"title": "Plant Overview", "metrics": metrics, "series": trend,
            "series_label": "Total backlog hours", "unit": "hours", "rows": []}


def demand_forecast():
    tables, _ = context()
    history = reconstruct_forecast_history(tables["customer_forecasts"], tables["forecast_versions"],
                                            tables["sales_order_lines"], tables["sales_orders"])
    accuracy = compute_accuracy_by_horizon(history)
    rows = []
    for _, r in accuracy.iterrows():
        bucket = str(r["horizon_bucket"])
        rows.append(point(bucket, round(float(r["wape"]) * 100, 2) if pd.notna(r["wape"]) else None,
                          "ANALYTICS_METHODS.md#forecast-accuracy",
                          [source("observation count", int(r["n_observations"]), bucket, "DERIVED"),
                           source("sum actual quantity", float(r["sum_actual_qty"]), bucket, "MEASURED")],
                          ["WAPE = sum absolute forecast error / sum actual quantity"],
                          n_observations=int(r["n_observations"]), mae=json_value(float(r["mae"])),
                          bias=json_value(float(r["bias"]) * 100)))
    return {"title": "Demand & Forecast", "metrics": [metric("Forecast revisions", len(history),
                "ANALYTICS_METHODS.md#forecast-reconstruction", [source("forecast IDs", len(history), "customer_forecasts")]),
                metric("Horizon buckets", len(rows), "ANALYTICS_METHODS.md#forecast-accuracy",
                       [source("accuracy buckets", [r["label"] for r in rows], "forecast_history", "DERIVED")])],
            "series": rows, "series_label": "WAPE by forecast horizon", "unit": "%", "rows": rows}


def production_flow():
    _, runs = context()
    base = runs["BASELINE"]
    series = []
    for date in HORIZON:
        rows = [r for r in base.material_results if r.period_start_date == date]
        gross = sum(r.gross_requirement for r in rows)
        net = sum(r.net_requirement for r in rows)
        series.append(point(date.isoformat(), round(net, 1), "ANALYTICS_METHODS.md#material-netting",
                            [source("gross requirement", gross, date, "DERIVED"),
                             source("net requirement", net, date, "DERIVED")],
                            ["Net requirement is calculated after usable inventory and scheduled receipts."], gross=round(gross, 1)))
    total_gross = sum(r.gross_requirement for r in base.material_results)
    total_net = sum(r.net_requirement for r in base.material_results)
    return {"title": "Production Flow", "metrics": [metric("Gross requirement", round(total_gross),
                "ANALYTICS_METHODS.md#material-netting", [source("material period gross", total_gross, "material_requirements", "DERIVED")]),
                metric("Net requirement", round(total_net), "ANALYTICS_METHODS.md#material-netting",
                       [source("material period net", total_net, "material_requirements", "DERIVED")])],
            "series": series, "series_label": "Net material requirement", "unit": "units", "rows": []}


def bom_explorer(item_id: int | None = None):
    tables, _ = context()
    items = tables["items"]
    if item_id is None:
        ids = cab100_item_ids(items)
        item_id = ids[0] if ids else None
    if item_id is None or item_id not in set(items["item_id"]):
        return {"title": "BOM Explorer", "metrics": [], "series": [], "rows": [], "error": "Item not found"}
    result = explode(item_id, 1.0, HORIZON[0], items, tables["bom_headers"], tables["bom_components"])
    rows = [point(line.component_item_code, round(line.gross_requirement, 3),
                  "ANALYTICS_METHODS.md#bom-explosion",
                  [source("BOM component path", [s.bom_component_id for s in line.path],
                          line.path[-1].bom_component_id if line.path else None),
                   source("quantity per parent", line.path[-1].quantity_per if line.path else 0,
                          line.path[-1].bom_component_id if line.path else None)],
                  ["Multiply each effective-dated component quantity and scrap factor along the path."],
                  depth=len(line.path), blocked_reason=line.blocked_reason, item_id=line.component_item_id)
            for line in result.lines]
    return {"title": "BOM Explorer", "item_id": item_id,
            "metrics": [metric("BOM lines", len(rows), "ANALYTICS_METHODS.md#bom-explosion",
                               [source("exploded paths", len(rows), item_id, "DERIVED")]),
                        metric("Blocked branches", len(result.blocked_branches), "ANALYTICS_METHODS.md#bom-explosion",
                               [source("incomplete paths", len(result.blocked_branches), item_id, "DERIVED")])],
            "series": [], "rows": rows}


def capacity():
    tables, runs = context()
    base = runs["DEMAND_SHOCK_ONLY"]
    names = tables["work_centres"].set_index("work_centre_id")["name"].to_dict()
    final = [r for r in base.work_centre_results if r.period_start_date == HORIZON[-1]]
    rows = [point(names.get(r.work_centre_id, str(r.work_centre_id)),
                  round(r.utilization_pct * 100, 1) if pd.notna(r.utilization_pct) else None,
                  "ANALYTICS_METHODS.md#three-tier-capacity",
                  [source("calendar hours", r.calendar_hours, r.work_centre_id),
                   source("available hours", r.available_hours, r.work_centre_id, "DERIVED"),
                   source("effective hours", r.effective_hours, r.work_centre_id, "DERIVED"),
                   source("required hours", r.required_hours, r.work_centre_id, "DERIVED")],
                  ["Utilization = required hours / effective hours × 100."],
                  work_centre_id=r.work_centre_id, classification=r.constraint_classification,
                  backlog_hours=round(r.backlog_hours_end, 1)) for r in final]
    rows.sort(key=lambda r: r["value"] if r["value"] is not None else -1, reverse=True)
    return {"title": "Capacity", "metrics": [metric("Primary constraints", sum(r.constraint_classification == "PRIMARY_CONSTRAINT" for r in final),
                "ANALYTICS_METHODS.md#constraint-classification",
                [source("classification", [r.constraint_classification for r in final], HORIZON[-1], "DERIVED")])],
            "series": rows, "series_label": "Final week utilization", "unit": "%", "rows": rows}


def wip_lead_time():
    _, runs = context()
    series = []
    for date in HORIZON:
        rows = [r for r in runs["DEMAND_SHOCK_ONLY"].work_centre_results if r.period_start_date == date]
        series.append(point(date.isoformat(), round(sum(r.wip_qty for r in rows), 2),
                            "ANALYTICS_METHODS.md#period-engine",
                            [source("WIP by work centre", [r.wip_qty for r in rows], date, "DERIVED")]))
    return {"title": "WIP & Lead Time", "metrics": [metric("Final week WIP estimate", series[-1]["value"],
                "ANALYTICS_METHODS.md#period-engine", series[-1]["evidence"]["inputs"])],
            "series": series, "series_label": "Demand shock WIP", "unit": "units", "rows": []}


def scenarios():
    _, runs = context()
    rows = []
    for case in CASES:
        result = runs[case]
        final = [r for r in result.work_centre_results if r.period_start_date == HORIZON[-1]]
        value = round(sum(r.backlog_hours_end for r in final), 1)
        rows.append(point(case, value, "ANALYTICS_METHODS.md#scenario-comparison",
                          [source("final work centre backlogs", [r.backlog_hours_end for r in final], case, "DERIVED")],
                          ["All cases use the same 12-week calendar and fixed synthetic seed."],
                          constrained=sum(r.constraint_classification == "PRIMARY_CONSTRAINT" for r in final)))
    return {"title": "Scenario Lab", "metrics": [metric("Compared cases", len(rows),
                "ANALYTICS_METHODS.md#scenario-comparison", [source("case names", list(CASES), "scenario_runs", "DERIVED")])],
            "series": rows, "series_label": "Final backlog by scenario", "unit": "hours", "rows": rows,
            "intervention_start_week": runs["intervention_start_week"].isoformat()}


def data_quality():
    tables, _ = context()
    findings = run_all(tables)
    rows = [point(f"{f.rule_id}: {f.record_id}", f.severity, "ANALYTICS_METHODS.md#data-quality",
                  [source("rule", f.rule_id, f.record_id, "DERIVED"),
                   source("entity", f.entity, f.record_id)],
                  [f.description], classification=f.classification, origin=f.origin,
                  impact_scope=f.impact_scope, record_id=f.record_id, entity=f.entity) for f in findings[:200]]
    return {"title": "Data Quality", "metrics": [metric("Findings", len(findings),
                "ANALYTICS_METHODS.md#data-quality", [source("rule findings", len(findings), "dq_findings", "DERIVED")]),
                metric("Blocking", sum(f.classification == "BLOCKING" for f in findings),
                       "ANALYTICS_METHODS.md#data-quality", [source("blocking findings", sum(f.classification == "BLOCKING" for f in findings), "dq_findings", "DERIVED")])],
            "series": [], "rows": rows, "total_rows": len(findings)}


def recommendations():
    tables, runs = context()
    recs = recommend_buffers(tables, runs["DEMAND_SHOCK_ONLY"])
    return {"title": "Recommendation", "metrics": [metric("Analytical buffer candidates", len(recs),
                "ANALYTICS_METHODS.md#buffer-recommendations",
                [source("constraint-aware candidate count", len(recs), "scenario:DEMAND_SHOCK_ONLY", "DERIVED")])],
            "series": [], "rows": recs, "total_rows": len(recs)}


PAGES = {"plant-overview": plant_overview, "demand-forecast": demand_forecast,
         "production-flow": production_flow, "bom-explorer": bom_explorer,
         "capacity": capacity, "wip-lead-time": wip_lead_time,
         "scenario-lab": scenarios, "data-quality": data_quality,
         "recommendation": recommendations}
