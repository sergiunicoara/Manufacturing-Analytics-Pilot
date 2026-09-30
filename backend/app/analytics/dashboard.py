"""Read models for the nine dashboard pages; every displayed value has evidence."""
from __future__ import annotations

import datetime as dt
from threading import Lock

import pandas as pd

from app.analytics import stage_history as sh
from app.analytics.blocking import build_blocking_index
from app.analytics.bom import explode
from app.analytics.buffers import recommend_buffers
from app.analytics.cost import (MISSING, OVERLAPPING, VALUED, CostAssumptions, case_economics,
                                five_case_summary, unit_costs)
from app.analytics.data_access import load_all_tables, write_consumption_audit
from app.analytics.data_version import data_version
from app.analytics.evidence import evidence, json_value, source
from app.analytics.forecast import (CONSUMPTION_POLICY, compute_accuracy_by_horizon, consume_forecast,
                                    realized_wape_by_item, reconstruct_forecast_history)
from app.analytics.parameter_export import build_package
from app.analytics.policy import PolicyThresholds, decoupling_candidates, recommend_policies
from app.analytics.scenario_demo import build_engine_inputs, cab100_item_ids, run_four_intervention_comparison
from app.analytics.scenario_store import ensure_run, save_cost_results
from app.config import settings
from app.db.connection import get_engine
from app.db.migrate import require_current_schema
from app.dq import summary as dq_summary
from app.dq.engine import findings_to_dataframe, run_all
from app.synthetic.run_generator import TABLE_ORDER as SOURCE_TABLES
from app.synthetic.timeline import REFERENCE_DATE

HORIZON = tuple(REFERENCE_DATE + dt.timedelta(weeks=w) for w in range(12))
CASES = ("BASELINE", "DEMAND_SHOCK_ONLY", "BUFFER_ONLY", "CAPACITY_ONLY", "COMBINED")


_context_lock = Lock()
_context_value = None
_tables_lock = Lock()
_tables_value = None


def context():
    """Compute once even when several page requests arrive together."""
    global _context_value
    if _context_value is None:
        with _context_lock:
            if _context_value is None:
                _context_value = _build_context()
    return _context_value


_findings_lock = Lock()
_findings_cache: tuple[int, list] | None = None


def findings_for(tables) -> list:
    """DQ findings for this loaded dataset, computed once and reused by every page and export; recomputed
    only when a different set of tables is passed (for example after an API restart or in tests)."""
    global _findings_cache
    cached = _findings_cache
    if cached is not None and cached[0] == id(tables):
        return cached[1]
    with _findings_lock:
        if _findings_cache is None or _findings_cache[0] != id(tables):
            _findings_cache = (id(tables), run_all(tables))
        return _findings_cache[1]


def source_tables():
    """Source tables alone, without running the reference scenarios."""
    global _tables_value
    if _tables_value is None:
        with _tables_lock:
            if _tables_value is None:
                _tables_value = load_all_tables(get_engine())
    return _tables_value


def consumption_events(tables) -> list[dict]:
    return consume_forecast(tables["customer_forecasts"], tables["forecast_versions"], tables["sales_order_lines"],
                            tables["sales_orders"], settings.forecast_consumption_window_weeks)["consumption_events"]


def _build_context():
    engine = get_engine()
    require_current_schema(engine)
    tables = source_tables()
    comparison = run_four_intervention_comparison(tables, list(HORIZON), demand_multiplier=1.4)
    comparison["data_version"] = data_version(tables, SOURCE_TABLES)
    run_ids = {}
    write_consumption_audit(engine, consumption_events(tables))
    for case in CASES:
        params = {"horizon_start": HORIZON[0], "horizon_weeks": len(HORIZON),
                  "demand_multiplier": 1.0 if case == "BASELINE" else 1.4,
                  "buffer_boost_per_item": 40.0 if case in {"BUFFER_ONLY", "COMBINED"} else 0.0,
                  "capacity_multipliers": comparison["per_work_centre_multipliers"] if case in {"CAPACITY_ONLY", "COMBINED"} else {},
                  "intervention_start_week": comparison["intervention_start_week"]}
        intervention = case if case in {"BUFFER_ONLY", "CAPACITY_ONLY", "COMBINED"} else "NONE"
        run_ids[case] = ensure_run(engine, case, params, comparison[case], intervention,
                                   recommend_buffers(tables, comparison[case]) if case == "DEMAND_SHOCK_ONLY" else [],
                                   comparison["data_version"])
    comparison["run_ids"] = run_ids
    comparison["economics"] = compute_economics(tables, comparison)
    for case in CASES:
        save_cost_results(engine, run_ids[case], cost_result_rows(comparison["economics"][case]))
    return tables, comparison


COST_ASSUMPTIONS = CostAssumptions()


def compute_economics(tables, comparison) -> dict:
    costs = unit_costs(tables["standard_costs"], tables["items"], tables["bom_headers"],
                       tables["bom_components"], HORIZON[0])
    levers = {"BUFFER_ONLY": (True, False), "COMBINED": (True, True), "CAPACITY_ONLY": (False, True)}
    result = {"unit_costs": costs}
    for case in CASES:
        with_buffer, with_capacity = levers.get(case, (False, False))
        result[case] = case_economics(
            comparison[case], costs, list(HORIZON), COST_ASSUMPTIONS,
            comparison.get("buffer_boost_by_item") if with_buffer else None,
            comparison.get("capacity_lever") if with_capacity else None, tables["work_centres"])
    return result


def cost_result_rows(econ: dict) -> list[dict]:
    a = COST_ASSUMPTIONS
    base = [f"Currency {a.currency}; synthetic standard costs valued on {HORIZON[0]}.",
            "Unknown (missing or overlapping) cost records are excluded and reported, never valued at zero."]
    return [
        {"metric": "average_inventory_capital", "value": econ["average_inventory_capital"], "units": a.currency,
         "provenance": "DERIVED", "status": "COMPUTED",
         "assumptions": base + ["End-of-period inventory state × effective standard unit cost, averaged over the horizon."]},
        {"metric": "inventory_carrying_cost", "value": econ["inventory_carrying_cost"], "units": a.currency,
         "provenance": "ASSUMED", "status": "COMPUTED",
         "assumptions": base + [f"ASSUMED annual carrying rate {a.annual_carrying_rate:.0%}, applied weekly "
                                f"({a.weeks_per_year} weeks/year) to end-of-period inventory value (balance held for one week)."]},
        {"metric": "buffer_capital", "value": econ["buffer_capital"], "units": a.currency,
         "provenance": "DERIVED", "status": "COMPUTED",
         "assumptions": base + ["One-time buffer boost quantity × standard unit cost: capital tied up, not a cash-flow estimate."]},
        {"metric": "intervention_cost", "value": econ["intervention_cost"], "units": a.currency,
         "provenance": "ASSUMED", "status": "COMPUTED",
         "assumptions": base + ["ASSUMED: every added scheduled hour is paid at the work centre's cost_per_hour "
                                f"× {a.added_hour_rate_multiplier:g}; effective hours are not paid hours.",
                                "Equipment, hiring and training costs are not modelled."]},
        {"metric": "wip_carrying_cost", "value": None, "units": a.currency, "provenance": "DERIVED",
         "status": "UNAVAILABLE", "assumptions": [econ["wip_carrying_cost_reason"]]},
        {"metric": "ending_backlog_hours", "value": econ["ending_backlog_hours"], "units": "hours",
         "provenance": "DERIVED", "status": "COMPUTED", "assumptions": ["Residual service risk: backlog left at horizon end."]},
    ]


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
               metric("Data quality findings", len(findings_for(tables)), "ANALYTICS_METHODS.md#data-quality",
                      [source("rule engine findings", len(findings_for(tables)), "dq_findings", "DERIVED")])]
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
    events = consumption_events(tables)
    policy = CONSUMPTION_POLICY.format(weeks=settings.forecast_consumption_window_weeks)
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
                       [source("accuracy buckets", [r["label"] for r in rows], "forecast_history", "DERIVED")]),
                metric("Forecast consumption events", len(events), "ANALYTICS_METHODS.md#forecast-consumption",
                       [source("consumed quantity", round(sum(float(e["consumed_qty"]) for e in events), 2),
                               "forecast_consumption", "DERIVED"),
                        source("order lines that consumed forecast", len({e["sales_order_line_id"] for e in events}),
                               "sales_order_lines", "DERIVED")],
                       ["Consumption events are persisted to the forecast_consumption audit table."],
                       [policy])],
            "series": rows, "series_label": "WAPE by forecast horizon", "unit": "%", "rows": rows,
            "consumption_policy": policy}


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
    tables = source_tables()
    snapshot, aged = sh.wip_ageing(tables["wip"])
    valid_age = aged.loc[aged["age_class"] == "VALID"]
    names = tables["work_centres"].set_index("work_centre_id")["name"]
    age_assumptions = ["Age = snapshot_date − stage_entered_at in whole days, for the latest recorded WIP snapshot.",
                       "Recorded WIP comes from the wip snapshot table (synthetic); it is separate from the "
                       "projected WIP estimate of the period engine."]
    age_common = dict(units="days", time_scope=f"wip snapshot {snapshot}", data_origin="SYNTHETIC",
                      exclusions=[f"INVALID_AGE (entry after snapshot): {int((aged['age_class'] == 'INVALID_AGE').sum())} records"])
    rows = []
    for wc, group in valid_age.groupby("work_centre_id", dropna=False):
        label = "Unassigned" if pd.isna(wc) else str(names.get(int(wc), wc))
        median_age = _num(group["age_days"].median())
        rows.append({"label": f"{label} · recorded WIP age", "value": median_age,
                     "wip_records": len(group), "wip_qty": _num(group["qty"].sum()),
                     "max_age_days": _num(group["age_days"].max()),
                     "evidence": evidence(median_age, "ANALYTICS_METHODS.md#wip-ageing",
                                          [source("WIP records", len(group), f"work_centre:{wc}"),
                                           source("WIP quantity", _num(group["qty"].sum()), f"work_centre:{wc}"),
                                           source("oldest record age (days)", _num(group["age_days"].max()),
                                                  f"work_centre:{wc}", "DERIVED")],
                                          ["median of record ages at the snapshot"], age_assumptions,
                                          provenance="MEASURED", **age_common)})
    rows.sort(key=lambda r: -(r["value"] or 0))
    median_all = _num(valid_age["age_days"].median())
    return {"title": "WIP & Lead Time", "metrics": [metric("Final week WIP estimate", series[-1]["value"],
                "ANALYTICS_METHODS.md#period-engine", series[-1]["evidence"]["inputs"]),
                {"label": f"Recorded WIP median age (days, {snapshot})", "value": median_all,
                 "evidence": evidence(median_all, "ANALYTICS_METHODS.md#wip-ageing",
                                      [source("recorded WIP records", len(valid_age), "wip"),
                                       source("recorded WIP quantity", _num(valid_age["qty"].sum()), "wip")],
                                      ["median of snapshot_date − stage_entered_at"], age_assumptions,
                                      provenance="MEASURED", **age_common)}],
            "series": series, "series_label": "Demand shock WIP (projected)", "unit": "units", "rows": rows,
            "total_rows": len(rows)}


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
            "intervention_start_week": runs["intervention_start_week"].isoformat(),
            "data_version": runs.get("data_version")}


def data_quality():
    tables = source_tables()
    findings = findings_for(tables)
    groups = dq_summary.explain(findings, tables)
    coverage = dq_summary.entity_coverage(findings, tables)
    blocking = [f for f in findings if f.classification == "BLOCKING"]
    blocking_records = sum(row["records_with_blocking_findings"] for row in coverage)
    rows = []
    for g in groups:
        ev = evidence(g["findings"], "ANALYTICS_METHODS.md#data-quality",
                      [source("findings", g["findings"], g["rule_id"], "DERIVED"),
                       source("unique affected records", g["unique_records"], g["entity"], "DERIVED"),
                       source("source table rows", g["table_rows"], g["entity"])],
                      [f"Grouped by rule, origin, classification and impact scope; "
                       f"records affected = {g['unique_records']} ÷ {g['table_rows']} rows"],
                      [g["description"]], units="findings", data_origin="SYNTHETIC",
                      coverage={"records_affected_pct": g["records_affected_pct"]})
        rows.append({"label": f"{g['rule_id']} · {g['entity']}", "value": g["findings"],
                     "classification": g["classification"], "origin": g["origin"],
                     "impact_scope": g["impact_scope"], "unique_records": g["unique_records"],
                     "records_affected_pct": g["records_affected_pct"], "evidence": ev})
    share_note = ("The share of findings classified BLOCKING is not the share of unusable data: "
                  "a finding may block one entity or one KPI, and several findings can hit one record.")
    return {"title": "Data Quality", "metrics": [
                metric("Findings", len(findings), "ANALYTICS_METHODS.md#data-quality",
                       [source("rule findings", len(findings), "dq rule engine", "DERIVED")]),
                metric("Blocking findings", len(blocking), "ANALYTICS_METHODS.md#data-quality",
                       [source("blocking findings", len(blocking), "dq rule engine", "DERIVED")], None, [share_note]),
                metric("Records with a blocking finding", blocking_records, "ANALYTICS_METHODS.md#data-quality",
                       [source(f"{row['entity']} ({row['blocking_records_pct']}% of rows)",
                               row["records_with_blocking_findings"], row["entity"], "DERIVED") for row in coverage],
                       ["unique (entity, record) pairs with at least one BLOCKING finding"], [share_note]),
                metric("Rule groups", len(groups), "ANALYTICS_METHODS.md#data-quality",
                       [source("rule × origin × classification × scope groups", len(groups), "dq rule engine", "DERIVED")])],
            "series": [], "rows": rows, "total_rows": len(rows), "entity_coverage": coverage,
            "findings_total": len(findings), "findings_export": "/api/dq/findings.csv"}


def recommendations():
    tables, runs = context()
    recs = recommend_buffers(tables, runs["DEMAND_SHOCK_ONLY"])
    return {"title": "Recommendation", "metrics": [metric("Analytical buffer candidates", len(recs),
                "ANALYTICS_METHODS.md#buffer-recommendations",
                [source("constraint-aware candidate count", len(recs), "scenario:DEMAND_SHOCK_ONLY", "DERIVED")])],
            "series": [], "rows": recs, "total_rows": len(recs)}


STAGE_HISTORY_WINDOW = sh.ObservationWindow(start=None, end=REFERENCE_DATE)
def stage_history_assumptions(ops) -> list[str]:
    return [
        "Recorded elapsed time = actual_finish − actual_start. It includes non-working time (nights, weekends "
        "outside the work centre's shifts) and waiting inside the operation; it is not productive processing time.",
        sh.resolution_assumption(sh.timestamp_resolution_mix(ops)),
        sh.TIMEZONE_ASSUMPTION,
        "The timestamps are generated by the synthetic data seed; they are not real plant observations.",
    ]
_STAGE_COMMON = dict(units="hours (calendar elapsed)", time_scope=STAGE_HISTORY_WINDOW.label(),
                     data_origin="SYNTHETIC")


def _num(value, digits=1):
    return None if value is None or pd.isna(value) else round(float(value), digits)


def stage_history_frame():
    tables = source_tables()
    blocking = build_blocking_index(findings_to_dataframe(findings_for(tables)))
    classified = sh.classify_operations(tables["production_order_operations"], tables["production_orders"],
                                        blocking, STAGE_HISTORY_WINDOW)
    classified["standard_processing_hours"] = sh.standard_processing_hours(
        classified, tables["routing_operations"], tables["production_orders"])
    return tables, classified


def stage_performance():
    tables, classified = stage_history_frame()
    assumptions = stage_history_assumptions(tables["production_order_operations"])
    counts = sh.record_class_counts(classified)
    exclusions = [f"{name}: {count} operations" for name, count in counts.items()
                  if name != sh.COMPLETED_VALID and count]
    common = {**_STAGE_COMMON, "exclusions": exclusions}
    stats = sh.summarize_elapsed(classified, ["work_centre_id"])
    valid = classified.loc[classified["record_class"] == sh.COMPLETED_VALID]
    standard = valid.groupby("work_centre_id")["standard_processing_hours"].median().rename("standard_p50_hours")
    stats = stats.join(standard, on="work_centre_id")
    names = tables["work_centres"].set_index("work_centre_id")["name"]
    gaps = sh.inter_operation_gaps(classified)

    rows = []
    for row in stats.sort_values("n_observations", ascending=False).itertuples():
        wc = int(row.work_centre_id)
        coverage = {"valid_observations": int(row.n_observations), "eligible_operations": int(row.n_eligible),
                    "coverage_pct": _num(row.coverage_pct)}
        ev = evidence(_num(row.p50_hours), "ANALYTICS_METHODS.md#recorded-stage-elapsed",
                      [source("recorded operations (valid, completed)", int(row.n_observations), f"work_centre:{wc}"),
                       source("P85 recorded elapsed hours", _num(row.p85_hours), f"work_centre:{wc}", "DERIVED"),
                       source("P95 recorded elapsed hours", _num(row.p95_hours), f"work_centre:{wc}", "DERIVED"),
                       source("same-day (0 h) observations", int(row.n_zero_elapsed), f"work_centre:{wc}"),
                       source("routing-standard processing hours, median over the same operations",
                              _num(row.standard_p50_hours, 2), f"work_centre:{wc}", "DERIVED")],
                      ["elapsed_hours = actual_finish − actual_start for COMPLETED_VALID operations",
                       "median and upper percentiles over that cohort",
                       "coverage = valid observations ÷ operations that are COMPLETED or carry a finish time"],
                      assumptions, provenance="MEASURED", coverage=coverage, **common)
        rows.append({"label": str(names.get(wc, wc)), "value": _num(row.p50_hours), "work_centre_id": wc,
                     "recorded_p50_hours": _num(row.p50_hours), "recorded_p85_hours": _num(row.p85_hours),
                     "recorded_p95_hours": _num(row.p95_hours), "n_observations": int(row.n_observations),
                     "coverage_pct": _num(row.coverage_pct),
                     "modelled_standard_p50_hours": _num(row.standard_p50_hours, 2), "evidence": ev})
    total_eligible = int(stats["n_eligible"].sum())
    coverage = {"valid_observations": counts[sh.COMPLETED_VALID], "eligible_operations": total_eligible,
                "coverage_pct": round(100.0 * counts[sh.COMPLETED_VALID] / total_eligible, 1) if total_eligible else None}
    median = _num(valid["elapsed_hours"].median())
    open_ops = counts[sh.OPEN_STARTED] + counts[sh.NOT_STARTED]
    overlaps = int((gaps["gap_class"] == "OVERLAP").sum())
    metrics = [
        {"label": "Recorded median elapsed (h)", "value": median,
         "evidence": evidence(median, "ANALYTICS_METHODS.md#recorded-stage-elapsed",
                              [source("valid completed operations", counts[sh.COMPLETED_VALID], "production_order_operations")],
                              ["median of actual_finish − actual_start over COMPLETED_VALID operations"],
                              assumptions, provenance="MEASURED", coverage=coverage, **common)},
        {"label": "Observation coverage (%)", "value": coverage["coverage_pct"],
         "evidence": evidence(coverage["coverage_pct"], "ANALYTICS_METHODS.md#recorded-stage-elapsed",
                              [source(name, count, "production_order_operations") for name, count in counts.items()],
                              ["coverage = COMPLETED_VALID ÷ operations that are COMPLETED or carry a finish time"],
                              assumptions, provenance="DERIVED", coverage=coverage, **common)},
        {"label": "Open operations (excluded)", "value": open_ops,
         "evidence": evidence(open_ops, "ANALYTICS_METHODS.md#recorded-stage-elapsed",
                              [source("started, not finished", counts[sh.OPEN_STARTED], "production_order_operations"),
                               source("not started", counts[sh.NOT_STARTED], "production_order_operations")],
                              ["Unfinished operations are reported separately, never as zero-duration observations."],
                              assumptions, provenance="MEASURED", **common)},
        {"label": "Overlapping consecutive operations", "value": overlaps,
         "evidence": evidence(overlaps, "ANALYTICS_METHODS.md#recorded-stage-elapsed",
                              [source("consecutive valid operation pairs", len(gaps), "production_order_operations"),
                               source("median inter-operation elapsed gap (h)", _num(gaps["gap_hours"].median()),
                                      "production_order_operations", "DERIVED")],
                              ["gap = next.actual_start − previous.actual_finish within one production order",
                               "negative gaps are overlaps; they are counted, never clamped to zero"],
                              assumptions + ["An inter-operation gap is elapsed time, not proven queue time."],
                              provenance="MEASURED", **common)},
    ]
    return {"title": "Stage Performance", "metrics": metrics, "series": rows,
            "series_label": "Recorded median elapsed hours per operation, by work centre", "unit": "hours",
            "rows": rows, "total_rows": len(rows),
            "timestamp_resolution_hours": sh.timestamp_resolution_hours(tables["production_order_operations"]),
            "timestamp_resolution_mix": sh.timestamp_resolution_mix(tables["production_order_operations"]),
            "record_class_counts": counts, "observation_window": STAGE_HISTORY_WINDOW.label(),
            "data_origin": "SYNTHETIC"}


def dq_findings(rule_id=None, classification=None, origin=None, entity=None, offset=0, limit=100):
    findings = dq_summary.filter_findings(findings_for(source_tables()), rule_id, classification, origin, entity)
    return {"total": len(findings), "offset": offset, "limit": limit,
            "findings": [dq_summary.finding_dict(f) for f in findings[offset:offset + limit]]}


def dq_findings_csv(rule_id=None, classification=None, origin=None, entity=None) -> str:
    return dq_summary.to_csv(dq_summary.filter_findings(findings_for(source_tables()), rule_id, classification,
                                                        origin, entity))


def decision_economics():
    tables, runs = context()
    econ = runs.get("economics") or compute_economics(tables, runs)
    costs = econ["unit_costs"]
    summary = five_case_summary({case: econ[case] for case in CASES})
    a = COST_ASSUMPTIONS
    persisted = cost_result_rows(econ["DEMAND_SHOCK_ONLY"])
    statuses = [c.status for c in costs.values()]
    valued = statuses.count(VALUED)
    assumed = sum(1 for c in costs.values() if c.status == VALUED and c.provenance == "ASSUMED")
    cost_coverage = {"items": len(costs), "valued": valued, "missing": statuses.count(MISSING),
                     "overlapping": statuses.count(OVERLAPPING), "valued_with_assumed_inputs": assumed}
    common = dict(units=a.currency, time_scope=f"{HORIZON[0]} to {HORIZON[-1]} (12 weekly periods)",
                  data_origin="SYNTHETIC", coverage=cost_coverage)
    run_ids = runs.get("run_ids", {})
    rows = []
    for row in summary:
        case = row["case"]
        e = econ[case]
        inputs = [source("average inventory capital", round(e["average_inventory_capital"], 2), f"scenario:{case}", "DERIVED"),
                  source("inventory carrying cost (horizon)", round(e["inventory_carrying_cost"], 2), f"scenario:{case}", "ASSUMED"),
                  source("buffer capital tied up", round(e["buffer_capital"], 2), f"scenario:{case}", "DERIVED"),
                  source("intervention cost (added paid hours)", round(e["intervention_cost"], 2), f"scenario:{case}", "ASSUMED"),
                  source("WIP carrying cost", None, f"scenario:{case}", "DERIVED"),
                  source("ending backlog hours (residual service risk)", round(e["ending_backlog_hours"], 1), f"scenario:{case}", "DERIVED")]
        inputs += [source(f"added hours · work centre {line['work_centre_id']}",
                          f"{line['added_scheduled_hours_per_week']} h/week × {line['active_weeks']} weeks × "
                          f"{line['hourly_rate']:g} {a.currency}/h", f"work_centre:{line['work_centre_id']}", "ASSUMED")
                   for line in e["intervention_lines"]]
        period_expense = row["intervention_cost"] + row["inventory_carrying_cost"]
        ev = evidence(round(period_expense, 2), "ANALYTICS_METHODS.md#cost",
                      inputs,
                      ["Headline = period expense over the horizon: inventory carrying cost + added paid hours.",
                       "Buffer capital is a balance tied up at standard cost and is NOT added to the expense; "
                       "residual backlog hours are reported beside it, not converted to money.",
                       f"Deltas are relative to DEMAND_SHOCK_ONLY."],
                      [p for r in persisted for p in r["assumptions"]] + [e["wip_carrying_cost_reason"],
                       "No ROI, profit or avoided-loss estimate: the required revenue and penalty inputs are absent."],
                      provenance="ASSUMED", exclusions=[f"{cost_coverage['missing']} items without an effective cost record",
                                                        f"{cost_coverage['overlapping']} items with overlapping cost records"],
                      **common)
        rows.append({"label": case, "value": round(period_expense, 2), "period_expense": round(period_expense, 2), "run_id": run_ids.get(case), **row, "evidence": ev})
    metrics = [
        metric("Items with a valued standard cost", valued, "ANALYTICS_METHODS.md#cost",
               [source(k, v, "standard_costs", "DERIVED") for k, v in cost_coverage.items()],
               [f"Effective record on {HORIZON[0]}; missing or overlapping records stay unknown."]),
        metric("Annual carrying rate (assumed)", a.annual_carrying_rate, "ANALYTICS_METHODS.md#cost",
               [source("annual_carrying_rate", a.annual_carrying_rate, "CostAssumptions", "ASSUMED")], None,
               ["Configurable planning assumption, not a measured cost of capital."]),
    ]
    return {"title": "Decision Economics", "metrics": metrics, "series": rows,
            "series_label": f"Period expense per case ({a.currency}): inventory carrying + added paid hours",
            "unit": a.currency, "rows": rows, "total_rows": len(rows), "assumptions": a.as_dict(),
            "comparison_case": "DEMAND_SHOCK_ONLY", "data_origin": "SYNTHETIC",
            "data_version": runs.get("data_version")}


POLICY_THRESHOLDS = PolicyThresholds()


def _policy_inputs():
    tables, runs = context()
    shock = runs["DEMAND_SHOCK_ONLY"]
    constrained = {r.work_centre_id for r in shock.work_centre_results
                   if r.constraint_classification in {"CANDIDATE_CONSTRAINT", "PRIMARY_CONSTRAINT"}}
    policies = recommend_policies(tables, realized_wape_by_item(tables), POLICY_THRESHOLDS)
    candidates = decoupling_candidates(tables, constrained, POLICY_THRESHOLDS)
    return tables, runs, policies, candidates


def planning_policy():
    _, _, policies, candidates = _policy_inputs()
    thresholds = POLICY_THRESHOLDS.as_dict()
    common = dict(units="policy", time_scope="sales order and production order history up to the reference date",
                  data_origin="SYNTHETIC")

    def fmt(v, digits=2):
        return None if v is None else round(float(v), digits)

    rows = []
    for p in policies:
        inputs = [source("customer tolerance days (median order → requested ship)", fmt(p.customer_tolerance_days, 1),
                         f"item:{p.item_id}"),
                  source("own-route recorded days (median completed production order)", fmt(p.own_route_days, 1),
                         f"item:{p.item_id}"),
                  source("cumulative recorded days (own route + longest manufactured branch)",
                         fmt(p.cumulative_days, 1), f"item:{p.item_id}", "DERIVED"),
                  source("weekly demand CV", fmt(p.demand_cv), f"item:{p.item_id}", "DERIVED"),
                  source("share of zero-demand weeks", fmt(p.intermittency), f"item:{p.item_id}", "DERIVED"),
                  source("realized forecast WAPE", fmt(p.forecast_wape), f"forecast:{p.item_id}", "DERIVED")]
        inputs += [source(f"threshold · {k}", v, "PolicyThresholds", "ASSUMED") for k, v in thresholds.items()]
        ev = evidence(p.policy, "ANALYTICS_METHODS.md#planning-policy", inputs, p.reasons or p.blockers,
                      ["Heuristic for planner review, informed by decoupling-point ideas; not a DDMRP implementation.",
                       "Policy selection is separate from buffer sizing (Analytical Buffer Recommendation).",
                       "Alternatives: " + "; ".join(p.alternatives) if p.alternatives else "No alternative listed."],
                      provenance="DERIVED", exclusions=p.blockers or None, **common)
        rows.append({"label": p.item_code, "value": p.policy, "item_id": p.item_id, "policy": p.policy,
                     "confidence": p.confidence, "reason": (p.reasons or p.blockers or [""])[0], "evidence": ev})
    for c in candidates:
        ev = evidence(True, "ANALYTICS_METHODS.md#planning-policy",
                      [source("finished goods served", c["finished_goods_served"], f"item:{c['item_id']}", "DERIVED"),
                       source("routes through a candidate/primary constraint", c["routes_through_constraint"],
                              f"item:{c['item_id']}", "DERIVED"),
                       source("threshold · min_common_parents", POLICY_THRESHOLDS.min_common_parents,
                              "PolicyThresholds", "ASSUMED")],
                      c["reasons"], ["A candidate location for review, not a placement decision."],
                      provenance="DERIVED", **{**common, "units": "flag"})
        rows.append({"label": f"Decoupling candidate · {c['item_code']}", "value": "CANDIDATE", "item_id": c["item_id"],
                     "reason": " ".join(c["reasons"]), "evidence": ev})
    counts = {}
    for p in policies:
        counts[p.policy] = counts.get(p.policy, 0) + 1
    metrics = [metric(f"{name.replace('_', ' ').title()} items", count, "ANALYTICS_METHODS.md#planning-policy",
                      [source("finished goods", count, "items", "DERIVED")]) for name, count in sorted(counts.items())]
    metrics.append(metric("Decoupling candidates", len(candidates), "ANALYTICS_METHODS.md#planning-policy",
                          [source("candidate components", len(candidates), "bom_components", "DERIVED")]))
    return {"title": "Planning Policy", "metrics": metrics, "series": [], "rows": rows, "total_rows": len(rows),
            "thresholds": thresholds, "parameter_package": "/api/parameters/package.json",
            "parameter_package_csv": "/api/parameters/package.csv", "data_origin": "SYNTHETIC"}


def parameter_package():
    tables, runs, policies, candidates = _policy_inputs()
    site_code = str(tables["sites"].iloc[0]["site_code"])
    buffers = recommend_buffers(tables, runs["DEMAND_SHOCK_ONLY"])
    return build_package(policies, buffers, candidates, site_code, HORIZON[0],
                         runs.get("run_ids", {}).get("DEMAND_SHOCK_ONLY"), "DEMAND_SHOCK_ONLY",
                         dt.datetime.now(dt.timezone.utc).replace(microsecond=0))


def stage_records(work_centre_id: int | None = None, record_class: str | None = None,
                  offset: int = 0, limit: int = 100):
    _, classified = stage_history_frame()
    frame = classified
    if work_centre_id is not None:
        frame = frame.loc[frame["work_centre_id"] == work_centre_id]
    if record_class is not None:
        frame = frame.loc[frame["record_class"] == record_class]
    columns = ["po_operation_id", "production_order_id", "seq_no", "work_centre_id", "item_id", "site_id",
               "status", "actual_start", "actual_finish", "record_class", "elapsed_hours",
               "standard_processing_hours"]
    page = frame.sort_values("po_operation_id")[columns].iloc[offset:offset + limit]
    return {"total": len(frame), "offset": offset, "limit": limit, "data_origin": "SYNTHETIC",
            "records": json_value(page.astype(object).where(page.notna(), None).to_dict("records"))}


PAGES = {"plant-overview": plant_overview, "demand-forecast": demand_forecast,
         "production-flow": production_flow, "bom-explorer": bom_explorer,
         "capacity": capacity, "wip-lead-time": wip_lead_time,
         "scenario-lab": scenarios, "data-quality": data_quality,
         "recommendation": recommendations, "stage-performance": stage_performance,
         "decision-economics": decision_economics, "planning-policy": planning_policy}
