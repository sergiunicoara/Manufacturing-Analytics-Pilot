"""Build a FlowPlant from the loaded tables.

Demand and stock come from the same inputs as the weekly period engine (`build_engine_inputs`), so the simulator starts
from the same facts: the same finished-good demand per week, the same usable stock (entity-blocked items excluded),
the same open purchase-order receipts. Routings and BOMs are the revisions effective on the first demand week. Items whose
routing or BOM cannot be used (missing or overlapping revisions) are excluded; single routing operations that are
blocked by data quality or lack a work centre or standard times are left out, as in the period engine, and counted and listed, with the
demand that depended on them: the simulator does not guess.
"""
from __future__ import annotations

import datetime as dt
import math

import pandas as pd

from app.analytics.capacity import RoutingLoad
from app.analytics.flow.model import FlowParameters, FlowPlant, OpTemplate, WorkCentre
from app.analytics.mrp import BomExploder
from app.analytics.scenario_demo import build_engine_inputs

DEFAULT_AVAILABILITY = 0.85


def _date(value) -> dt.date:
    return pd.Timestamp(value).date()


def _work_centres(tables, origin: dt.date, end: dt.date) -> dict:
    calendar = tables["capacity_calendar"].copy()
    calendar["week"] = pd.to_datetime(calendar["week_start_date"]).dt.date
    window = calendar.loc[(calendar["week"] >= origin) & (calendar["week"] <= end) & (calendar["calendar_hours"] > 0)]
    ratio = (window["effective_hours"] / window["calendar_hours"]).groupby(window["work_centre_id"]).mean()
    out = {}
    for row in tables["work_centres"].to_dict("records"):
        wc_id = int(row["work_centre_id"])
        hours = float(row["shifts_per_day"]) * float(row["hours_per_shift"])
        if not hours > 0:
            continue                                        # no working time: operations there are left out and counted
        availability = float(ratio.get(wc_id, DEFAULT_AVAILABILITY))
        out[wc_id] = WorkCentre(wc_id, str(row["name"]), str(row["process_type"]), hours, float(row["cost_per_hour"]),
                                min(max(availability, 0.05), 1.0))
    return out


def _routings(tables, as_of: dt.date, work_centres: dict, blocking) -> tuple[dict, dict, dict]:
    """(item -> operations, item -> reason it cannot be used, item -> operations skipped for missing data).
    Like the period engine, an operation without a work centre or standard times is left out and reported, not guessed;
    overlapping routing revisions make the item unusable."""
    load = RoutingLoad(tables["routing_headers"], tables["routing_operations"])
    unique, ambiguous = load.routing_by_item(as_of)
    ops_by_routing = {}
    for row in tables["routing_operations"].sort_values(["routing_id", "seq_no"]).to_dict("records"):
        ops_by_routing.setdefault(int(row["routing_id"]), []).append(row)
    routings, problems, skipped = {}, {}, {}
    for item_id in set(unique) | set(ambiguous):
        if item_id in ambiguous:
            problems[item_id] = "overlapping routing revisions"
            continue
        ops = []
        for row in ops_by_routing.get(unique[item_id], []):
            numbers = [row["setup_time_minutes"], row["run_time_minutes_per_unit"], row["yield_pct"], row["batch_size"]]
            if (pd.isna(row["work_centre_id"]) or int(row["work_centre_id"]) not in work_centres
                    or any(pd.isna(n) for n in numbers) or float(row["batch_size"]) <= 0 or float(row["yield_pct"]) <= 0
                    or blocking.is_kpi_blocked("routing_operation", int(row["routing_operation_id"]))):
                skipped[item_id] = skipped.get(item_id, 0) + 1
                continue
            ops.append(OpTemplate(int(row["work_centre_id"]), str(row["operation_name"]), float(row["setup_time_minutes"]),
                                  float(row["run_time_minutes_per_unit"]), float(row["yield_pct"]),
                                  max(float(row["batch_size"]), 1.0),
                                  0.0 if pd.isna(row["transfer_time_minutes"]) else float(row["transfer_time_minutes"])))
        routings[item_id] = ops
    return routings, problems, skipped


def build_plant(tables: dict, params: FlowParameters, horizon: list[dt.date], inputs: dict | None = None) -> FlowPlant:
    inputs = inputs or build_engine_inputs(tables, list(horizon))
    weeks = list(horizon)[: params.horizon_weeks]
    first = weeks[0]
    origin = first - dt.timedelta(weeks=params.lead_in_weeks)
    work_centres = _work_centres(tables, origin, weeks[-1])
    routings, problems, skipped_ops = _routings(tables, first, work_centres, inputs["blocking_index"])

    items = tables["items"]
    codes = {int(i): str(c) for i, c in zip(items["item_id"], items["item_code"])}
    families = {int(i): str(f) for i, f in zip(items["item_id"], items["product_family"])}
    exploder = BomExploder(items, tables["bom_headers"], tables["bom_components"])

    demand_raw = []
    week_index = {w: i for i, w in enumerate(weeks)}
    for week, by_item in inputs["top_level_demand"].items():
        week_date = _date(week)
        if week_date not in week_index:
            continue
        for item_id, qty in by_item.items():
            factor = params.demand_multiplier if families.get(int(item_id)) in params.demand_multiplier_families else 1.0
            if qty * factor > 0:
                demand_raw.append((week_index[week_date], int(item_id), float(qty) * factor))

    bom: dict = {}
    problem_items = dict(problems)
    stack = sorted({item for _, item, _ in demand_raw})
    seen = set(stack)
    while stack:
        item_id = stack.pop()
        one_level = exploder.explode_one_level(item_id, 1.0, first)
        if one_level.is_leaf:
            if item_id in routings:                      # a routing but no BOM: made from nothing, still a job
                bom.setdefault(item_id, [])
            continue
        if one_level.incomplete or any(child.incomplete for child in one_level.children):
            problem_items[item_id] = "BOM revision ambiguous or a component invalid"
            continue
        bom[item_id] = [(child.component_item_id, child.step.quantity_per, child.step.scrap_pct) for child in one_level.children]
        for child in one_level.children:
            if child.component_item_id not in seen:
                seen.add(child.component_item_id)
                stack.append(child.component_item_id)
    for item_id in bom:
        if item_id not in routings and item_id not in problem_items:
            problem_items[item_id] = "manufactured item without a routing"

    def usable(item_id: int, trail=()) -> bool:
        if item_id in problem_items:
            return False
        return all(usable(comp, trail + (item_id,)) for comp, _, _ in bom.get(item_id, ()) if comp not in trail)

    demand, excluded = [], 0.0
    for week, item_id, qty in demand_raw:
        if usable(item_id):
            demand.append((week, item_id, qty))
        else:
            excluded += qty

    origin_ts = pd.Timestamp(origin)
    receipts: dict = {}
    for row in tables["purchase_order_lines"].to_dict("records"):
        if str(row["status"]) not in ("OPEN", "PARTIAL") or pd.isna(row["expected_receipt_date"]):
            continue
        remaining = float(row["qty_ordered"]) - (0.0 if pd.isna(row["qty_received"]) else float(row["qty_received"]))
        if remaining > 0:
            hours = max(0.0, (pd.Timestamp(row["expected_receipt_date"]) - origin_ts).total_seconds() / 3600.0)
            receipts.setdefault(int(row["item_id"]), []).append((hours, remaining))

    cost_rows = tables["standard_costs"]
    cost_rows = cost_rows.loc[(cost_rows["effective_from"].map(_date) <= first)
                              & (cost_rows["effective_to"].isna() | (cost_rows["effective_to"].map(lambda v: _date(v) if not pd.isna(v) else first) >= first))]
    material_cost = {int(i): float(c) for i, c in zip(cost_rows["item_id"], cost_rows["material_cost"]) if not math.isnan(float(c))}

    return FlowPlant(work_centres=work_centres, item_codes=codes, item_families=families, routings=routings,
                     bom={i: lines for i, lines in bom.items() if i not in problem_items},
                     stock={int(i): float(q) for i, q in inputs["initial_inventory"].items()}, receipts=receipts,
                     material_cost=material_cost, demand=demand,
                     problems=sorted((int(i), reason) for i, reason in problem_items.items()), excluded_demand=excluded,
                     skipped_operations={int(i): n for i, n in skipped_ops.items() if i in bom or i in routings})
