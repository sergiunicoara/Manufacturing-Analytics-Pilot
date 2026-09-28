"""Inventory and WIP snapshots.

Snapshots are generated weekly for the trailing 12 weeks up to the
synthetic "reference date" (not the full history, to keep volume sane) —
enough to show trend/ageing without ballooning row counts across ~1,000
items.
"""
from __future__ import annotations

import datetime as dt

import pandas as pd

from app.synthetic.context import GenContext
from app.synthetic.timeline import REFERENCE_DATE

SNAPSHOT_WEEKS = [REFERENCE_DATE - dt.timedelta(weeks=w) for w in range(11, -1, -1)]


def generate_inventory(ctx: GenContext) -> None:
    rng = ctx.rng
    seq = ctx.id_seq("inventory")
    items = ctx.tables["items"]
    warehouses = ctx.tables["warehouses"].set_index("warehouse_type")["warehouse_id"]

    wh_for_type = {
        "RAW": int(warehouses["RAW"]),
        "PACKAGING": int(warehouses["RAW"]),
        "SUBASSY": int(warehouses["WIP"]),
        "FG": int(warehouses["FG"]),
    }

    rows: list[dict] = []
    for _, item in items.iterrows():
        item_id = int(item["item_id"])
        item_type = item["item_type"]
        warehouse_id = wh_for_type[item_type]
        uom = item["uom"]
        base_level = float(rng.uniform(20, 500) if item_type in ("RAW", "PACKAGING") else rng.uniform(2, 60))
        level = base_level
        for week in SNAPSHOT_WEEKS:
            # Sawtooth-ish: mild random walk with occasional replenishment jump.
            level = max(0.0, level + float(rng.normal(0, base_level * 0.08)))
            if rng.random() < 0.2:
                level += base_level * float(rng.uniform(0.3, 0.8))
            rows.append({
                "inventory_id": seq.next(),
                "item_id": item_id,
                "warehouse_id": warehouse_id,
                "snapshot_date": week,
                "on_hand_qty": round(level, 2),
                "uom": uom,
            })
    ctx.add_table("inventory", pd.DataFrame(rows))


def generate_wip(ctx: GenContext) -> None:
    seq = ctx.id_seq("wip")
    production_orders = ctx.tables["production_orders"]
    po_ops = ctx.tables["production_order_operations"]

    active_statuses = {"RELEASED", "IN_PROGRESS"}
    active_orders = production_orders.loc[production_orders["status"].isin(active_statuses)]

    ops_by_po = {po_id: grp.sort_values("seq_no") for po_id, grp in po_ops.groupby("production_order_id")}

    rows: list[dict] = []
    for _, po in active_orders.iterrows():
        po_id = int(po["production_order_id"])
        ops = ops_by_po.get(po_id)
        for week in SNAPSHOT_WEEKS:
            if not (po["planned_start"] <= week <= po["planned_finish"]):
                continue
            work_centre_id = None
            stage_entered_at = po["planned_start"]
            if ops is not None and not ops.empty:
                current = ops.loc[
                    (ops["planned_start"].apply(lambda d: d.date() if hasattr(d, "date") else d) <= week)
                ]
                if not current.empty:
                    row = current.iloc[-1]
                    work_centre_id = row["work_centre_id"]
                    ps = row["planned_start"]
                    stage_entered_at = ps.date() if hasattr(ps, "date") else ps
            rows.append({
                "wip_id": seq.next(),
                "production_order_id": po_id,
                "work_centre_id": work_centre_id,
                "item_id": int(po["item_id"]),
                "qty": po["qty"],
                "stage_entered_at": stage_entered_at,
                "snapshot_date": week,
            })
    ctx.add_table("wip", pd.DataFrame(rows))


def generate_all(ctx: GenContext) -> None:
    generate_inventory(ctx)
    generate_wip(ctx)
