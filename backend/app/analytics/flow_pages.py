"""The two pages built on the flow simulator: Shop Floor Flow (policies against today's practice) and Order Change
Impact (what is in stock at each stock point, and what a change of forecast does to it). dashboard.py supplies the
tables and the period engine's reference cases; everything here is derived from them and from the assumptions below.
"""
from __future__ import annotations

import dataclasses
from collections import defaultdict
from threading import Lock

from app.analytics.evidence import evidence, json_value, source
from app.analytics.flow.analysis import (DEFAULT_WIP_POINTS, METRIC_KEYS, ForecastChange, change_impact, point_history,
                                         policy_suite)
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
                     f"{m['overtime_stranded_h']:.0f} h of output left waiting, {m['overtime_cost']:,.0f} {CURRENCY}")
    return " · ".join(parts)


PRIMARY = {"baseline": "late_lots", "colour": "changeovers", "priority": "component_wait_days", "scrap": "scrap_value",
           "overtime": "overtime_idle_h", "combined": "late_lots"}


def shop_floor_flow(tables, comparison, horizon) -> dict:
    state = _state_for(tables, comparison, horizon)
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
