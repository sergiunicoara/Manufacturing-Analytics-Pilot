"""The two pages built on the flow simulator: Shop Floor Flow (policies against today's practice) and Order Change
Impact (what is in stock at each stock point, and what a change of forecast does to it). dashboard.py supplies the
tables and the period engine's reference cases; everything here is derived from them and from the assumptions below.
"""
from __future__ import annotations

import dataclasses
import datetime as dt
from collections import defaultdict
from threading import Lock

from app.analytics.evidence import evidence, json_value, source
from app.analytics.flow.analysis import (DEFAULT_WIP_POINTS, METRIC_KEYS, ForecastChange, change_impact, forecast_revisions,
                                         point_history, policy_suite, stage_table, waiting_passes, weekly_stock)
from app.analytics.flow.model import FlowParameters, FlowPlant, Policy
from app.analytics.flow.plant import build_plant
from app.analytics.flow.sim import FlowSimulation

CURRENCY = "EUR"
REPLICATIONS = 10
DEFAULT_CHANGE = ForecastChange(day=17.0, from_week=2, to_week=None, factor=0.0, families=("CAB-100",))
LABELS = {"late_lots": "late lots", "mean_lead_time_days": "lead time (days)", "changeovers": "colour changes",
          "coating_setup_hours": "coating setup hours", "component_wait_days": "component wait (days)",
          "scrapped_units": "units scrapped", "scrap_value": f"value scrapped ({CURRENCY})",
          "overtime_paid_h": "overtime paid (h)", "overtime_idle_h": "overtime idle (h)",
          "overtime_stranded_h": "overtime output left waiting (h)", "overtime_cost": f"overtime cost ({CURRENCY})",
          "monday_idle_h": "idle hours on Mondays at the overtime work centres",
          "monday_idle_after_overtime_h": "idle hours on the Monday after paid overtime",
          "remake_jobs": "remake jobs", "processing_hours": "processing hours"}

_lock = Lock()
_state: dict = {}


def assumptions(params: FlowParameters, plant: FlowPlant) -> list[str]:
    mix = ", ".join(f"{name} {share:.0%}" for name, share in params.colours)
    return [
        f"Colour does not exist in the data. Each finished-good lot gets a RAL colour from an assumed mix ({mix}), "
        "drawn with a fixed seed; the parts it needs inherit it, so painting is the step that commits a colour.",
        f"A colour change on a coating work centre costs {params.changeover_minutes:g} minutes (the plant's figure); "
        f"re-loading the same colour is assumed to cost {params.same_colour_setup_minutes:g} minutes. Coating setup "
        "follows this rule instead of the routing's batch setup; every other work centre uses the routing.",
        f"No scrap or inspection results exist in the data. Each unit is defective with an assumed probability of "
        f"{params.defect_rate_default:.0%} (a BOM line's scrap_pct where it has one). Component inspection "
        f"({params.component_inspection_minutes:g} min/unit), rework ({params.rework_minutes:g} min/unit) and "
        f"re-inspection ({params.reinspection_minutes:g} min/unit) are assumed. Inspection is assumed to find every defect.",
        f"Overtime is a Saturday window of up to 8 h at the chosen work centres, paid at {params.overtime_premium:g} times "
        "the work centre's hourly cost (assumed premium).",
        f"Open production orders are not used: they were generated independently of capacity and overload Welding 1 about "
        f"15 times. Lots come from the same weekly demand as the period engine (finished goods netted against stock and "
        f"receipts, at most {params.max_lot_qty:g} units per lot), exploded through the current BOM and routings.",
        f"Each BOM level below the finished good is released {params.level_offset_days:g} days earlier; the clock starts "
        f"{params.lead_in_weeks} weeks before the first demand week with an empty shop. A lot is late when it finishes after "
        "the end of its demand week.",
        "A purchased part with no stock or scheduled receipt before the lot is due is assumed available when the lot is "
        f"released (procurement is not what is tested); this affects {{shortages}} component lines.",
        f"{len(plant.problems)} items (overlapping routing or BOM revisions) and {plant.excluded_demand:,.0f} demand units that "
        f"depend on them are excluded; {sum(plant.skipped_operations.values())} routing operations without standard times or "
        "blocked by data quality are left out, as in the period engine.",
        "One operation at a time per work centre; earliest due date unless the policy changes it; no operator, tooling or "
        "maintenance limits beyond the capacity calendar's availability.",
    ]


def _state_for(tables, comparison, horizon) -> dict:
    cached = _state.get("value")
    if cached is not None and cached["tables"] is tables:
        return cached
    with _lock:
        cached = _state.get("value")
        if cached is not None and cached["tables"] is tables:
            return cached
        params = FlowParameters()
        plant = build_plant(tables, params, list(horizon))
        baseline = FlowSimulation(plant, params, Policy()).run()
        waits: dict = defaultdict(float)
        for job in baseline.jobs.values():
            for index, start, _, _, wc_id, _, _ in job.op_log:
                ready = job.kit_ready_h if index == 0 else job.op_log[index - 1][2]
                waits[wc_id] += max(0.0, start - (ready or 0.0))
        busiest = max(waits, key=waits.get) if waits else None
        lever = {int(w) for w in comparison.get("per_work_centre_multipliers", {})}
        overtime_wcs = tuple(sorted(lever | ({busiest} if busiest is not None else set())))
        suite = policy_suite(plant, params, REPLICATIONS, overtime_work_centres=overtime_wcs)
        value = {"tables": tables, "params": params, "plant": plant, "baseline": baseline, "suite": suite,
                 "overtime_wcs": overtime_wcs, "lever_wcs": sorted(lever), "busiest": busiest,
                 "default_change": None}
        _state["value"] = value
        return value


def _metric(label, value, formula, inputs, assumptions_list=None, trace=None, **common):
    return {"label": label, "value": json_value(value),
            "evidence": evidence(value, formula, inputs, trace, assumptions_list, **common)}


def _entry_inputs(entry: dict) -> list[dict]:
    reps = entry["reps"]
    note = f"mean of {reps} seeded runs" if reps > 1 else "one run"
    out = [source(f"{LABELS.get(key, key)} ({note})", round(entry["metrics"][key], 2), f"flow:{entry['key']}:{key}", "DERIVED")
           for key in LABELS if key in entry["metrics"]]
    if entry["std"]:
        out.append(source("standard deviation of value scrapped across runs", round(entry["std"]["scrap_value"], 2),
                          f"flow:{entry['key']}:scrap_value_std", "DERIVED"))
    out += [source(f"policy · {k}", v, "Policy", "ASSUMED") for k, v in entry["policy"].items()
            if k not in ("name",) and v not in (None, False, "none", ())]
    return out


def _summary(entry: dict) -> str:
    m = entry["metrics"]
    parts = [f"{m['late_lots']:.0f} of {m['lots']:.0f} lots late", f"lead time {m['mean_lead_time_days']:.1f} d"]
    if entry["group"] in ("baseline", "colour", "combined"):
        parts.append(f"{m['changeovers']:.0f} colour changes ({m['coating_setup_hours']:.0f} h coating setup)")
    if entry["group"] in ("scrap", "combined"):
        parts.append(f"{m['scrapped_units']:.0f} units / {m['scrap_value']:,.0f} {CURRENCY} scrapped")
    if entry["group"] in ("priority", "baseline"):
        parts.append(f"component wait {m['component_wait_days']:.0f} d")
    if entry["group"] == "overtime" or m["overtime_paid_h"]:
        parts.append(f"overtime {m['overtime_paid_h']:.0f} h paid, {m['overtime_idle_h']:.0f} h idle, "
                     f"{m['overtime_stranded_h']:.0f} h of output left waiting, {m['overtime_cost']:,.0f} {CURRENCY}; "
                     f"{m['monday_idle_after_overtime_h']:.0f} h idle on the Monday after")
    return " · ".join(parts)


PRIMARY = {"baseline": "late_lots", "colour": "changeovers", "priority": "component_wait_days", "scrap": "scrap_value",
           "overtime": "overtime_idle_h", "combined": "late_lots"}


OVERRIDE_FIELDS = {"changeover_minutes": "changeover_minutes", "same_colour_setup_minutes": "same_colour_setup_minutes",
                   "defect_rate": "defect_rate_default", "overtime_premium": "overtime_premium",
                   "max_lot_qty": "max_lot_qty", "demand_multiplier": "demand_multiplier"}


def _custom_state(state: dict, tables, horizon, overrides: dict) -> dict:
    """The same pages for chosen assumptions: nothing here is cached or persisted."""
    fields = {OVERRIDE_FIELDS[k]: v for k, v in overrides.items() if k in OVERRIDE_FIELDS and v is not None}
    params = dataclasses.replace(state["params"], **fields)
    rebuild = any(k in fields for k in ("demand_multiplier", "max_lot_qty"))
    plant = build_plant(tables, params, list(horizon)) if rebuild else state["plant"]
    hours = float(overrides.get("overtime_hours") or 8.0)
    reps = int(overrides.get("replications") or REPLICATIONS)
    suite = policy_suite(plant, params, reps, overtime_work_centres=state["overtime_wcs"], overtime_hours=hours)
    return {**state, "params": params, "plant": plant, "suite": suite, "overtime_hours": hours, "custom": True}


def shop_floor_flow(tables, comparison, horizon, overrides: dict | None = None) -> dict:
    state = _state_for(tables, comparison, horizon)
    if overrides:
        state = _custom_state(state, tables, horizon, overrides)
    params, plant, suite = state["params"], state["plant"], state["suite"]
    by_key = {e["key"]: e for e in suite}
    shortages = int(by_key["today"]["metrics"]["purchased_shortage_lines"])
    notes = [a.replace("{shortages}", str(shortages)) for a in assumptions(params, plant)]
    common = dict(units="mixed", time_scope=f"{horizon[0]} to {horizon[min(params.horizon_weeks, len(horizon)) - 1]} "
                                            f"({params.horizon_weeks} demand weeks plus {params.lead_in_weeks} lead-in weeks)",
                  data_origin="SYNTHETIC", coverage={"lots": int(by_key["today"]["metrics"]["lots"]),
                                                    "excluded_items": len(plant.problems),
                                                    "excluded_demand_units": round(plant.excluded_demand),
                                                    "purchased_shortage_lines": shortages})
    today, colour = by_key["today"], [e for e in suite if e["group"] == "colour"]
    best = min(colour, key=lambda e: e["metrics"]["changeovers"]) if colour else today
    final, replace, comp = by_key["scrap_final"], by_key["scrap_replace"], by_key["scrap_component"]
    unconditional = by_key.get("overtime_unconditional")
    custom = state.get("custom", False)
    metrics = [
        _metric("Finished-good lots late, today", round(today["metrics"]["late_lots"]), "ANALYTICS_METHODS.md#shop-floor-flow",
                _entry_inputs(today), notes, **common),
        _metric("Colour changes, today", round(today["metrics"]["changeovers"]), "ANALYTICS_METHODS.md#shop-floor-flow",
                [source("colour changes on coating lines", today["metrics"]["changeovers"], "flow:today", "DERIVED"),
                 source("changeover minutes (the plant's figure)", params.changeover_minutes, "FlowParameters", "ASSUMED")],
                notes, **common),
        _metric("Colour changes saved by grouping colours", round(today["metrics"]["changeovers"] - best["metrics"]["changeovers"]),
                "ANALYTICS_METHODS.md#shop-floor-flow",
                [source("colour changes, today", today["metrics"]["changeovers"], "flow:today", "DERIVED"),
                 source(f"colour changes, best window ({best['label']})", best["metrics"]["changeovers"], f"flow:{best['key']}", "DERIVED"),
                 source("coating setup hours saved", round(today["metrics"]["coating_setup_hours"] - best["metrics"]["coating_setup_hours"], 1),
                        "flow:colour", "DERIVED")], notes,
                ["Grouping only reorders ready work: a lot is pulled ahead of an earlier-due lot of another colour only when it is due within the window."],
                **common),
        _metric(f"Standard value scrapped today ({CURRENCY})", round(final["metrics"]["scrap_value"]), "ANALYTICS_METHODS.md#shop-floor-flow",
                _entry_inputs(final), notes, ["Scrapped units are priced at standard material plus routing hours at each work centre's hourly cost."], **common),
        _metric(f"Scrap value avoided by replacing only the failed component ({CURRENCY})",
                round(final["metrics"]["scrap_value"] - replace["metrics"]["scrap_value"]), "ANALYTICS_METHODS.md#shop-floor-flow",
                _entry_inputs(replace), notes, **common),
        _metric(f"Scrap value avoided by inspecting each component ({CURRENCY})",
                round(final["metrics"]["scrap_value"] - comp["metrics"]["scrap_value"]), "ANALYTICS_METHODS.md#shop-floor-flow",
                _entry_inputs(comp), notes, **common),
    ]
    if unconditional:
        paid = unconditional["metrics"]["overtime_paid_h"]
        idle = unconditional["metrics"]["overtime_idle_h"]
        metrics.append(_metric("Overtime hours idle when always paid", round(idle), "ANALYTICS_METHODS.md#shop-floor-flow",
                               _entry_inputs(unconditional) + [source("share of paid overtime that was idle",
                                                                        round(idle / paid, 3) if paid else None, "flow", "DERIVED")],
                               notes, **common))
        monday = unconditional["metrics"]["monday_idle_after_overtime_h"]
        metrics.append(_metric("Idle hours on the Monday after paid overtime", round(monday), "ANALYTICS_METHODS.md#shop-floor-flow",
                               _entry_inputs(unconditional) + [source("idle hours on all Mondays at these work centres, today",
                                                                        round(today["metrics"]["monday_idle_h"], 1), "flow:today", "DERIVED")],
                               notes, ["A Monday window is idle when the work centre had no work in it: the paid worker finds nothing to do."],
                               **common))
    rows, series = [], []
    for entry in suite:
        primary = PRIMARY[entry["group"]]
        inputs = _entry_inputs(entry)
        ev = evidence(round(entry["metrics"][primary], 2), "ANALYTICS_METHODS.md#shop-floor-flow", inputs,
                      [f"Policy: {entry['label']}.", f"Headline = {LABELS.get(primary, primary)}.",
                       "Same plant, demand and seed as the 'today' run; only the policy differs."], notes, provenance="DERIVED", **common)
        rows.append({"label": entry["label"], "value": round(entry["metrics"][primary], 1), "group": entry["group"],
                     "reason": f"Headline: {LABELS.get(primary, primary)} · " + _summary(entry), "evidence": ev})
        series.append({"label": entry["label"], "value": round(entry["metrics"]["mean_lead_time_days"], 2),
                       "evidence": evidence(round(entry["metrics"]["mean_lead_time_days"], 2), "ANALYTICS_METHODS.md#shop-floor-flow",
                                            inputs, ["Mean time from release to completion of the finished-good lots."], notes,
                                            provenance="DERIVED", **{**common, "units": "days"})})
    return {"title": "Shop Floor Flow", "metrics": metrics, "series": series,
            "series_label": "Mean finished-good lead time by shop-floor policy (days)", "unit": "days",
            "rows": rows, "total_rows": len(rows), "assumptions": notes, "overtime_work_centres": list(state["overtime_wcs"]),
            "settings": {"changeover_minutes": params.changeover_minutes, "same_colour_setup_minutes": params.same_colour_setup_minutes,
                         "defect_rate": params.defect_rate_default, "overtime_hours": state.get("overtime_hours", 8.0),
                         "overtime_premium": params.overtime_premium, "max_lot_qty": params.max_lot_qty,
                         "demand_multiplier": params.demand_multiplier, "custom": custom},
            "data_origin": "SYNTHETIC"}


def _stage_evidence(label, value, inputs, notes, common, units=CURRENCY):
    return evidence(round(value, 2), "ANALYTICS_METHODS.md#order-change-impact", inputs,
                    [f"{label}: work already done at the moment of the change, read from the simulated plan."], notes,
                    provenance="DERIVED", **{**common, "units": units})


def change_page(tables, comparison, horizon, change: ForecastChange | None = None) -> dict:
    state = _state_for(tables, comparison, horizon)
    params, plant = state["params"], state["plant"]
    change = change or DEFAULT_CHANGE
    use_default = change == DEFAULT_CHANGE
    if use_default and state["default_change"] is not None:
        out = state["default_change"]
    else:
        out = change_impact(plant, params, Policy(), change)
        if use_default:
            state["default_change"] = out
    shortages = int(out["metrics_before"]["purchased_shortage_lines"])
    notes = [a.replace("{shortages}", str(shortages)) for a in assumptions(params, plant)] + [
        "The changed plan (run B) is the same plant re-simulated with the new forecast from the start; the work already "
        "done at the moment of the change is read from the original plan (run A). Work cannot be un-done: only the "
        "unwanted share of what exists at the change is counted, and it is reusable only when other open demand still "
        "needs that item (and, after painting, the same colour).",
        "Work in process on a machine at the moment of the change is counted as sunk and not reusable.",
        "Value = purchased material at standard cost plus operation hours at each work centre's hourly cost, including "
        "the finished components consumed."]
    c = {**out["change"], "families": list(out["change"]["families"]), "item_ids": list(out["change"]["item_ids"])}
    scope = ", ".join(c["families"]) if c["families"] else "all families"
    weeks = f"from week {c['from_week']}" + (f" to week {c['to_week']}" if c["to_week"] is not None else " onward")
    what = "cancelled" if c["factor"] == 0 else (f"reduced to {c['factor']:.0%}" if c["factor"] < 1 else f"raised to {c['factor']:.0%}")
    common = dict(units=CURRENCY, time_scope=f"change on day {c['day']:g}; demand {weeks}; {scope}",
                  data_origin="SYNTHETIC", coverage={"lots_changed": out["lots_changed"], "factor": c["factor"]})
    before, after = out["metrics_before"], out["metrics_after"]
    hours_change = after["processing_hours"] - before["processing_hours"]
    base_inputs = [source("work already done on the unwanted share (sunk)", round(out["sunk_value"], 2), "flow:change", "DERIVED"),
                   source("reusable by other open demand", round(out["reusable_value"], 2), "flow:change", "DERIVED"),
                   source("stranded", round(out["stranded_value"], 2), "flow:change", "DERIVED"),
                   source("of which running on a machine at the change", round(out["in_process_value"], 2), "flow:change", "DERIVED"),
                   source("lots changed", out["lots_changed"], "flow:change", "DERIVED")]
    metrics = [
        _metric(f"Work already done on the unwanted forecast ({CURRENCY})", round(out["sunk_value"]), "ANALYTICS_METHODS.md#order-change-impact",
                base_inputs, notes, **common),
        _metric(f"Reusable by other open orders ({CURRENCY})", round(out["reusable_value"]), "ANALYTICS_METHODS.md#order-change-impact",
                base_inputs, notes, **common),
        _metric(f"Stranded, no other use ({CURRENCY})", round(out["stranded_value"]), "ANALYTICS_METHODS.md#order-change-impact",
                base_inputs, notes, **common),
        _metric("Change in mean lead time (days)", round(after["mean_lead_time_days"] - before["mean_lead_time_days"], 2),
                "ANALYTICS_METHODS.md#order-change-impact",
                [source("mean lead time before (days)", round(before["mean_lead_time_days"], 2), "flow:A", "DERIVED"),
                 source("mean lead time after (days)", round(after["mean_lead_time_days"], 2), "flow:B", "DERIVED"),
                 source("late lots before", before["late_lots"], "flow:A", "DERIVED"),
                 source("late lots after", after["late_lots"], "flow:B", "DERIVED")], notes, **{**common, "units": "days"}),
        _metric("Change in processing hours", round(hours_change, 1), "ANALYTICS_METHODS.md#order-change-impact",
                [source("processing hours before", round(before["processing_hours"], 1), "flow:A", "DERIVED"),
                 source("processing hours after", round(after["processing_hours"], 1), "flow:B", "DERIVED")],
                notes, **{**common, "units": "hours"}),
    ]
    rows, series = [], []
    for point in out["points"]:
        history = point["history_before"]
        inputs = [source("units in stock at the change (unwanted share)", round(point["units_at_change"], 1), "flow:change", "DERIVED"),
                  source("value of the unwanted share", round(point["unwanted_value"], 2), "flow:change", "DERIVED"),
                  source("reusable value", round(point["reusable_value"], 2), "flow:change", "DERIVED"),
                  source("stranded value", round(point["stranded_value"], 2), "flow:change", "DERIVED"),
                  source("units that passed through before the change plan", round(history["units_through"], 1), "flow:A", "DERIVED"),
                  source("mean wait (days) before / after", f"{point['average_wait_days_before']:.2f} / {point['average_wait_days_after']:.2f}", "flow", "DERIVED"),
                  source("average stock (units) before / after", f"{point['average_units_before']:.1f} / {point['average_units_after']:.1f}", "flow", "DERIVED"),
                  source("peak stock (units) before / after", f"{point['peak_units_before']:.1f} / {point['peak_units_after']:.1f}", "flow", "DERIVED")]
        detail = (f"{point['units_at_change']:.0f} unwanted units in stock at the change · reusable {point['reusable_value']:,.0f} · "
                  f"average stock {point['average_units_before']:.0f} → {point['average_units_after']:.0f} units · "
                  f"mean wait {point['average_wait_days_before']:.1f} → {point['average_wait_days_after']:.1f} d")
        ev = _stage_evidence(point["name"], point["stranded_value"], inputs, notes, common)
        rows.append({"label": f"Stock point · {point['name']}", "value": round(point["stranded_value"], 2), "reason": detail, "evidence": ev})
        series.append({"label": point["name"], "value": round(point["stranded_value"], 2), "evidence": ev})
    for stage in sorted(out["stages"], key=lambda s: -s["value"])[:12]:
        inputs = [source("unwanted units waiting", round(stage["units"], 1), "flow:change", "DERIVED"),
                  source("value", round(stage["value"], 2), "flow:change", "DERIVED"),
                  source("reusable value", round(stage["reusable_value"], 2), "flow:change", "DERIVED")]
        rows.append({"label": stage["stage"], "value": round(stage["stranded_value"], 2),
                     "reason": f"{stage['units']:.0f} unwanted units · value {stage['value']:,.0f} · reusable {stage['reusable_value']:,.0f}",
                     "evidence": _stage_evidence(stage["stage"], stage["stranded_value"], inputs, notes, common)})
    for resource in sorted(out["resources"], key=lambda r: r["change"])[:8]:
        inputs = [source("processing hours before", round(resource["hours_before"], 1), "flow:A", "DERIVED"),
                  source("processing hours after", round(resource["hours_after"], 1), "flow:B", "DERIVED")]
        rows.append({"label": f"Work centre hours · {resource['process']}", "value": round(resource["change"], 1),
                     "reason": f"{resource['hours_before']:.0f} h → {resource['hours_after']:.0f} h",
                     "evidence": _stage_evidence(resource["process"], resource["change"], inputs, notes, common, "hours")})
    for audit in out["audit_at_change"]:
        inputs = [source("jobs", audit["jobs"], "flow:audit", "DERIVED"), source("units", round(audit["units"], 1), "flow:audit", "DERIVED"),
                  source("value built in", round(audit["value"], 2), "flow:audit", "DERIVED"),
                  source("mean age (days)", round(audit["mean_age_days"], 2), "flow:audit", "DERIVED")]
        rows.append({"label": f"At the change · {audit['stage']}", "value": round(audit["value"], 2),
                     "reason": f"{audit['jobs']} lots · {audit['units']:.0f} units · mean age {audit['mean_age_days']:.1f} d",
                     "evidence": _stage_evidence(audit["stage"], audit["value"], inputs, notes, common)})
    if not series:
        series = [{"label": "No stock point", "value": 0, "evidence": evidence(0, "ANALYTICS_METHODS.md#order-change-impact", base_inputs, None, notes)}]
    return {"title": "Order Change Impact", "metrics": metrics, "series": series,
            "series_label": f"Stranded value by stock point, {scope} {what} {weeks} (day {c['day']:g}; {CURRENCY})",
            "unit": CURRENCY, "rows": rows, "total_rows": len(rows), "assumptions": notes, "change": c,
            "stock_points": [dataclasses.asdict(p) for p in DEFAULT_WIP_POINTS], "data_origin": "SYNTHETIC"}


def _weeks_label(params: FlowParameters, week: int) -> str:
    return f"week {week + 1}"


def stock_points_page(tables, comparison, horizon) -> dict:
    """Work in progress at each named stock point over the demand weeks, and the audit of every stage on one day."""
    state = _state_for(tables, comparison, horizon)
    params, plant, baseline = state["params"], state["plant"], state["baseline"]
    notes = [a.replace("{shortages}", str(int(baseline.metrics["purchased_shortage_lines"]))) for a in assumptions(params, plant)] + [
        "A stock point is where work waits between two operations. 'Between welding and painting' is parts after welding or "
        "grinding; 'Before painting' is any work waiting for a coating line, so the two overlap on welded parts. History is "
        "reconstructed from the operation log of the simulated plan (an empty shop two weeks before the first demand week)."]
    passes = waiting_passes(baseline)
    common = dict(units="units", time_scope=f"{params.horizon_weeks} demand weeks", data_origin="SYNTHETIC",
                  coverage={"lots": int(baseline.metrics["lots"]), "excluded_items": len(plant.problems)})
    metrics, series, rows = [], [], []
    for point in DEFAULT_WIP_POINTS:
        history = point_history(baseline, point, passes)
        inputs = [source("units that passed through", round(history["units_through"], 1), "flow:A", "DERIVED"),
                  source("value that passed through", round(history["value_through"], 2), "flow:A", "DERIVED"),
                  source("mean wait (days)", round(history["mean_wait_days"], 2), "flow:A", "DERIVED"),
                  source("90th percentile wait (days)", round(history["p90_wait_days"], 2), "flow:A", "DERIVED"),
                  source("longest wait (days)", round(history["max_wait_days"], 2), "flow:A", "DERIVED"),
                  source("peak stock (units)", round(history["peak_units"], 1), "flow:A", "DERIVED"),
                  source("average stock (units)", round(history["average_units"], 1), "flow:A", "DERIVED"),
                  source("average value in stock", round(history["average_value"], 2), "flow:A", "DERIVED")]
        metrics.append(_metric(f"Average stock · {point.name}", round(history["average_units"], 1),
                               "ANALYTICS_METHODS.md#order-change-impact", inputs, notes, **common))
        rows.append({"label": point.name, "value": round(history["mean_wait_days"], 2),
                     "reason": (f"Headline: mean wait (days) · {history['units_through']:.0f} units passed · longest wait "
                                f"{history['max_wait_days']:.1f} d · peak {history['peak_units']:.0f} units · average value "
                                f"{history['average_value']:,.0f} {CURRENCY}"),
                     "evidence": evidence(round(history["mean_wait_days"], 2), "ANALYTICS_METHODS.md#order-change-impact", inputs,
                                          ["Mean wait of the lots, weighted by quantity."], notes, **{**common, "units": "days"})})
        for week in weekly_stock(history, params.horizon_weeks):
            ev = evidence(round(week["units"], 1), "ANALYTICS_METHODS.md#order-change-impact",
                          [source("average units in stock that week", round(week["units"], 1), "flow:A", "DERIVED"),
                           source("average value in stock that week", round(week["value"], 2), "flow:A", "DERIVED"), inputs[5]],
                          [f"{point.name}, {_weeks_label(params, week['week'])}: average of the daily stock."], notes,
                          provenance="DERIVED", **common)
            series.append({"label": f"{point.name} · {_weeks_label(params, week['week'])}", "value": round(week["units"], 1), "evidence": ev})
    day = 17
    at_h = params.lead_in_weeks * 168.0 + day * 24.0
    for audit in stage_table(baseline, at_h, passes):
        inputs = [source("lots", audit["jobs"], "flow:audit", "DERIVED"), source("units", round(audit["units"], 1), "flow:audit", "DERIVED"),
                  source("value built in", round(audit["value"], 2), "flow:audit", "DERIVED"),
                  source("mean age (days)", round(audit["mean_age_days"], 2), "flow:audit", "DERIVED")]
        rows.append({"label": f"Day {day} audit · {audit['stage']}", "value": round(audit["value"], 2),
                     "reason": f"Headline: value built in ({CURRENCY}) · {audit['jobs']} lots · {audit['units']:.0f} units · mean age {audit['mean_age_days']:.1f} d",
                     "evidence": evidence(round(audit["value"], 2), "ANALYTICS_METHODS.md#order-change-impact", inputs,
                                          [f"Every stage on day {day} of the demand weeks."], notes, provenance="DERIVED",
                                          **{**common, "units": CURRENCY})})
    return {"title": "Stock Points", "metrics": metrics, "series": series,
            "series_label": "Average units in stock at each stock point, by week", "unit": "units", "rows": rows,
            "total_rows": len(rows), "assumptions": notes, "stock_points": [dataclasses.asdict(p) for p in DEFAULT_WIP_POINTS],
            "data_origin": "SYNTHETIC"}


def revision_ratios(tables, plant: FlowPlant, params: FlowParameters, horizon) -> tuple[list, list]:
    """For each weekly forecast snapshot from the first demand week on, the ratio new/old forecast per (week, item) against
    the previous snapshot. Returns (snapshot dates, ratios). A snapshot lists only some items and weeks, so an item or week missing from
    either snapshot is left unchanged, not read as zero."""
    weeks = list(horizon)[: params.horizon_weeks]
    index = {w: i for i, w in enumerate(weeks)}
    versions = tables["forecast_versions"].copy()
    versions["day"] = versions["snapshot_date"].map(lambda v: v.date() if hasattr(v, "date") else v)
    versions = versions.loc[(versions["day"] >= weeks[0]) & (versions["day"] <= weeks[-1])].sort_values("day")
    forecasts = tables["customer_forecasts"]
    items = {item for _, item, _ in plant.demand}
    snapshots = []
    for version_id in versions["forecast_version_id"]:
        rows = forecasts.loc[forecasts["forecast_version_id"] == version_id]
        qty: dict = defaultdict(float)
        for week, item, q in zip(rows["delivery_period_start"], rows["item_id"], rows["qty"]):
            day = week.date() if hasattr(week, "date") else week
            if day in index and int(item) in items:
                qty[(index[day], int(item))] += float(q)
        snapshots.append(qty)
    ratios = []
    for before, after in zip(snapshots, snapshots[1:]):
        ratios.append({key: after[key] / old for key, old in before.items() if old > 0 and after.get(key, 0.0) > 0})
    return [d for d in versions["day"]], ratios


def forecast_updates_page(tables, comparison, horizon) -> dict:
    state = _state_for(tables, comparison, horizon)
    params, plant = state["params"], state["plant"]
    if state.get("revisions") is None:
        dates, ratios = revision_ratios(tables, plant, params, horizon)
        state["revisions"] = (dates, forecast_revisions(plant, params, Policy(), ratios))
    dates, revisions = state["revisions"]
    notes = [a.replace("{shortages}", "0") for a in assumptions(params, plant)] + [
        "Replays the weekly forecast snapshots in the data. At the Sunday that starts demand week j the plan for weeks after j is "
        "scaled by new/old forecast per item and week (an item or week missing from either snapshot is left unchanged: snapshots are sparse, so a gap is not a zero forecast); work for earlier "
        "weeks keeps its plan. Each update is compared with the plan before it as a forecast change at that moment: the unwanted share "
        "of work that exists is split into reusable and stranded as on Order Change Impact."]
    common = dict(units=CURRENCY, time_scope=f"snapshots {dates[0]} to {dates[-1]}" if dates else "no snapshots",
                  data_origin="SYNTHETIC", coverage={"updates": len(revisions)})
    rows, series = [], []
    total_stranded = sum(r["stranded_value"] for r in revisions)
    for r in revisions:
        sunday = dates[r["revision"]] - dt.timedelta(days=1) if r["revision"] < len(dates) else None
        label = f"Update received Sunday {sunday}" if sunday else f"Update {r['revision']}"
        before, after = r["metrics_before"], r["metrics_after"]
        inputs = [source("units added to the plan", round(r["units_added"], 1), "flow:revision", "DERIVED"),
                  source("units removed from the plan", round(r["units_removed"], 1), "flow:revision", "DERIVED"),
                  source("work already done on removed demand", round(r["sunk_value"], 2), "flow:revision", "DERIVED"),
                  source("reusable", round(r["reusable_value"], 2), "flow:revision", "DERIVED"),
                  source("stranded", round(r["stranded_value"], 2), "flow:revision", "DERIVED"),
                  source("mean lead time before / after (days)", f"{before['mean_lead_time_days']:.2f} / {after['mean_lead_time_days']:.2f}", "flow", "DERIVED"),
                  source("late lots before / after", f"{before['late_lots']:.0f} / {after['late_lots']:.0f}", "flow", "DERIVED"),
                  source("processing hours before / after", f"{before['processing_hours']:.0f} / {after['processing_hours']:.0f}", "flow", "DERIVED")]
        ev = evidence(round(r["stranded_value"], 2), "ANALYTICS_METHODS.md#order-change-impact", inputs,
                      [f"{label}: stranded value of the work that existed when the update arrived."], notes, provenance="DERIVED", **common)
        series.append({"label": f"Sunday {sunday}" if sunday else f"Update {r['revision']}", "value": round(r["stranded_value"], 2), "evidence": ev})
        rows.append({"label": label, "value": round(r["stranded_value"], 2),
                     "reason": (f"Headline: stranded value ({CURRENCY}) · +{r['units_added']:.0f} / -{r['units_removed']:.0f} units · lead time "
                                f"{before['mean_lead_time_days']:.1f} → {after['mean_lead_time_days']:.1f} d · late lots {before['late_lots']:.0f} → "
                                f"{after['late_lots']:.0f} · hours {after['processing_hours'] - before['processing_hours']:+.0f}"),
                     "evidence": ev})
        for point in r["points"]:
            pin = [source("stranded value", round(point["stranded_value"], 2), "flow:revision", "DERIVED"),
                   source("reusable value", round(point["reusable_value"], 2), "flow:revision", "DERIVED"),
                   source("unwanted units in stock", round(point["units_at_change"], 1), "flow:revision", "DERIVED"),
                   source("average stock before / after (units)", f"{point['average_units_before']:.1f} / {point['average_units_after']:.1f}", "flow", "DERIVED")]
            rows.append({"label": f"{label} · {point['name']}", "value": round(point["stranded_value"], 2),
                         "reason": f"{point['units_at_change']:.0f} unwanted units · reusable {point['reusable_value']:,.0f} · average stock "
                                   f"{point['average_units_before']:.0f} → {point['average_units_after']:.0f} units",
                         "evidence": evidence(round(point["stranded_value"], 2), "ANALYTICS_METHODS.md#order-change-impact", pin,
                                              [f"{point['name']} at the update."], notes, provenance="DERIVED", **common)})
    first_lead = revisions[0]["metrics_before"]["mean_lead_time_days"] if revisions else 0.0
    last_lead = revisions[-1]["metrics_after"]["mean_lead_time_days"] if revisions else 0.0
    base_inputs = [source("updates replayed", len(revisions), "forecast_versions", "DERIVED"),
                   source("stranded across the updates", round(total_stranded, 2), "flow:revisions", "DERIVED"),
                   source("units added", round(sum(r["units_added"] for r in revisions), 1), "flow:revisions", "DERIVED"),
                   source("units removed", round(sum(r["units_removed"] for r in revisions), 1), "flow:revisions", "DERIVED")]
    metrics = [
        _metric("Weekly forecast updates replayed", len(revisions), "ANALYTICS_METHODS.md#order-change-impact", base_inputs, notes, **common),
        _metric(f"Work stranded by the updates ({CURRENCY})", round(total_stranded), "ANALYTICS_METHODS.md#order-change-impact", base_inputs, notes, **common),
        _metric("Units removed from the plan", round(sum(r["units_removed"] for r in revisions)), "ANALYTICS_METHODS.md#order-change-impact", base_inputs, notes, **{**common, "units": "units"}),
        _metric("Units added to the plan", round(sum(r["units_added"] for r in revisions)), "ANALYTICS_METHODS.md#order-change-impact", base_inputs, notes, **{**common, "units": "units"}),
        _metric("Mean lead time, first plan to last plan (days)", round(last_lead - first_lead, 2), "ANALYTICS_METHODS.md#order-change-impact",
                [source("first plan (days)", round(first_lead, 2), "flow", "DERIVED"), source("last plan (days)", round(last_lead, 2), "flow", "DERIVED")],
                notes, **{**common, "units": "days"}),
    ]
    if not series:
        series = [{"label": "No update", "value": 0, "evidence": evidence(0, "ANALYTICS_METHODS.md#order-change-impact", base_inputs, None, notes)}]
    return {"title": "Forecast Updates", "metrics": metrics, "series": series,
            "series_label": f"Stranded value caused by each Sunday forecast update ({CURRENCY})", "unit": CURRENCY, "rows": rows,
            "total_rows": len(rows), "assumptions": notes, "data_origin": "SYNTHETIC"}
