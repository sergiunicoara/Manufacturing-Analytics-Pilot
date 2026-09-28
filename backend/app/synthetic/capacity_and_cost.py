"""Capacity calendar (three explicit tiers per CORR-5) and standard costs
(material/labour/overhead, tagged MEASURED/ASSUMED per component).

Clean baseline only — dq_injection.py introduces the deliberate errors
(zero-capacity weeks, missing cost records) as a separate, explicit pass.
"""
from __future__ import annotations

import datetime as dt

import pandas as pd

from app.synthetic.context import GenContext
from app.synthetic.timeline import CAPACITY_CALENDAR_WEEKS

EFFECTIVE_FROM = dt.date(2024, 1, 1)


def generate_capacity_calendar(ctx: GenContext) -> None:
    rng = ctx.rng
    seq = ctx.id_seq("capacity_calendar")
    work_centres = ctx.tables["work_centres"]

    rows: list[dict] = []
    for _, wc in work_centres.iterrows():
        calendar_hours_base = float(wc["shifts_per_day"] * wc["hours_per_shift"] * wc["days_per_week"])
        # A per-work-centre baseline availability, with small week-to-week noise.
        base_availability = float(rng.uniform(0.82, 0.94))
        for week in CAPACITY_CALENDAR_WEEKS:
            planned_downtime = float(rng.choice([0, 0, 0, 4, 8], p=[0.7, 0.1, 0.1, 0.07, 0.03]))
            availability_pct = min(0.99, max(0.5, base_availability + float(rng.normal(0, 0.03))))
            available_hours = calendar_hours_base - planned_downtime
            effective_hours = available_hours * availability_pct
            rows.append({
                "capacity_calendar_id": seq.next(),
                "work_centre_id": int(wc["work_centre_id"]),
                "week_start_date": week,
                "calendar_hours": calendar_hours_base,
                "planned_downtime_hours": planned_downtime,
                "availability_pct": round(availability_pct, 4),
                "available_hours": round(available_hours, 2),
                "effective_hours": round(effective_hours, 2),
            })
    ctx.add_table("capacity_calendar", pd.DataFrame(rows))


def generate_standard_costs(ctx: GenContext) -> None:
    rng = ctx.rng
    seq = ctx.id_seq("standard_costs")
    items = ctx.tables["items"]

    routing_headers = ctx.tables["routing_headers"].set_index("item_id")["routing_id"].to_dict()
    routing_ops = ctx.tables["routing_operations"]
    wc_cost = ctx.tables["work_centres"].set_index("work_centre_id")["cost_per_hour"].to_dict()
    ops_by_routing = {rid: grp for rid, grp in routing_ops.groupby("routing_id")}

    bom_components = ctx.tables["bom_components"]
    bom_headers = ctx.tables["bom_headers"].set_index("bom_id")["parent_item_id"]
    comps_by_parent: dict[int, pd.DataFrame] = {}
    if not bom_components.empty:
        merged = bom_components.merge(
            bom_headers.rename("parent_item_id"), left_on="bom_id", right_index=True
        )
        comps_by_parent = {pid: grp for pid, grp in merged.groupby("parent_item_id")}

    # material_cost per raw/packaging item: a plausible per-unit purchase
    # price (independent random draw — purchase_order_lines.unit_cost varies
    # around this due to normal price variance, which is realistic).
    raw_cost: dict[int, float] = {}
    for _, item in items.loc[items["item_type"].isin(["RAW", "PACKAGING"])].iterrows():
        raw_cost[int(item["item_id"])] = float(rng.uniform(0.5, 45.0))

    rows: list[dict] = []

    def labour_cost_for(item_id: int) -> float:
        routing_id = routing_headers.get(item_id)
        if routing_id is None or routing_id not in ops_by_routing:
            return 0.0
        total = 0.0
        for _, op in ops_by_routing[routing_id].iterrows():
            cost_rate = wc_cost.get(op["work_centre_id"], 50.0)
            setup = op["setup_time_minutes"] or 0.0
            run = op["run_time_minutes_per_unit"] or 0.0
            batch = max(1, int(op["batch_size"]))
            minutes_per_unit = (setup / batch) + run
            total += (minutes_per_unit / 60.0) * cost_rate
        return total

    def material_cost_for(item_id: int) -> float:
        if item_id in raw_cost:
            return raw_cost[item_id]
        comps = comps_by_parent.get(item_id)
        if comps is None or comps.empty:
            return float(rng.uniform(5, 50))
        total = 0.0
        for _, comp in comps.iterrows():
            comp_id = int(comp["component_item_id"])
            comp_cost = raw_cost.get(comp_id)
            if comp_cost is None:
                comp_cost = float(rng.uniform(5, 50))  # component not yet costed (deeper level) — approximate
            total += comp_cost * float(comp["quantity_per"]) / max(1e-6, (1 - float(comp["scrap_pct"])))
        return total

    # Process items bottom-up-ish: raw first (already have cost), then
    # subassemblies/FG in an order where components are likely already
    # costed (items were generated depth-first per variant, which already
    # tends to produce components before parents; a second pass fixes the
    # rest via the fallback random cost above rather than requiring a
    # perfect topological sort for this synthetic baseline).
    for _, item in items.iterrows():
        item_id = int(item["item_id"])
        material = round(material_cost_for(item_id), 4)
        labour = round(labour_cost_for(item_id), 4)
        overhead_pct = 0.18  # ASSUMED overhead adder — see ASSUMPTIONS.md
        overhead = round((material + labour) * overhead_pct, 4)
        cost_source = "MEASURED" if item["item_type"] in ("RAW", "PACKAGING") else (
            "ASSUMED" if rng.random() < 0.15 else "MEASURED"
        )
        rows.append({
            "standard_cost_id": seq.next(),
            "item_id": item_id,
            "effective_from": EFFECTIVE_FROM,
            "effective_to": None,
            "material_cost": material,
            "labour_cost": labour,
            "overhead_cost": overhead,
            "cost_source": cost_source,
        })

    ctx.add_table("standard_costs", pd.DataFrame(rows))


def generate_all(ctx: GenContext) -> None:
    generate_capacity_calendar(ctx)
    generate_standard_costs(ctx)
