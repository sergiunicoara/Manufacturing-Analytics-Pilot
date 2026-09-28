"""CP2 baseline demonstration: explodes every currently-open (not COMPLETED)
finished-goods production order through its full multi-level BOM, aggregates
gross component requirements, nets them against current usable inventory and
open purchase-order receipts, and persists the result to
material_requirements under one baseline scenario_runs row.

Scope note (documented, not a silent simplification): this is a *single*
point-in-time netting pass using the most recent inventory snapshot and each
component's earliest contributing need-by date — not the full weekly
period-stepped time series. That is CP3's period_engine.py, which will call
analytics.netting.net_requirement once per work-centre-period across the
planning horizon instead of once per item here. Gross requirement here is
defined as "components implied by currently-open FG production orders",
which avoids double-counting between an FG order and separately-scheduled
subassembly orders (see CP2 report for the full rationale).

Usage: python -m app.analytics.run_baseline_netting
"""
from __future__ import annotations

import datetime as dt
import json

import pandas as pd
import sqlalchemy as sa

from app.analytics.bom import explode
from app.analytics.data_access import load_all_tables
from app.analytics.netting import net_requirement
from app.db.connection import get_engine, reflect_metadata
from app.synthetic.context import GenContext  # noqa: F401  (type hint context only)


def _get_or_create_baseline_run(engine: sa.engine.Engine, metadata: sa.MetaData, seed: int) -> int:
    with engine.begin() as conn:
        existing = conn.execute(
            sa.text("SELECT run_id FROM scenario_runs WHERE is_baseline = 1 AND name = :name"),
            {"name": "CP2 baseline material requirements"},
        ).fetchone()
        if existing:
            return int(existing[0])
        table = metadata.tables["scenario_runs"]
        result = conn.execute(table.insert().values(
            name="CP2 baseline material requirements",
            parameters_json=json.dumps({"basis": "open FG production orders", "seed": seed}),
            is_baseline=True,
            intervention_type="NONE",
            seed=seed,
        ))
        return int(result.inserted_primary_key[0])


def compute_baseline_material_requirements(tables: dict[str, pd.DataFrame]) -> list[dict]:
    items = tables["items"]
    production_orders = tables["production_orders"]
    bom_headers = tables["bom_headers"]
    bom_components = tables["bom_components"]
    inventory = tables["inventory"]
    purchase_order_lines = tables["purchase_order_lines"]
    dq_findings = tables.get("dq_findings", pd.DataFrame(columns=["entity", "record_id", "classification"]))

    fg_items = set(items.loc[items["item_type"] == "FG", "item_id"])
    open_fg_orders = production_orders.loc[
        (production_orders["item_id"].isin(fg_items)) & (production_orders["status"] != "COMPLETED")
    ]

    aggregated: dict[int, float] = {}
    need_by: dict[int, dt.date] = {}

    for _, po in open_fg_orders.iterrows():
        result = explode(
            item_id=int(po["item_id"]), qty=float(po["qty"]), as_of_date=_as_date(po["planned_start"]),
            items_df=items, bom_headers_df=bom_headers, bom_components_df=bom_components,
        )
        for item_id, qty in result.aggregated_requirements.items():
            aggregated[item_id] = aggregated.get(item_id, 0.0) + qty
            finish = _as_date(po["planned_finish"])
            if item_id not in need_by or finish < need_by[item_id]:
                need_by[item_id] = finish

    blocked_inventory_ids = set(
        dq_findings.loc[
            (dq_findings["entity"] == "inventory") & (dq_findings["classification"] == "BLOCKING"), "record_id"
        ].astype(str)
    ) if not dq_findings.empty else set()

    latest_inventory = (
        inventory.sort_values("snapshot_date").groupby(["item_id", "warehouse_id"], as_index=False).last()
    )
    items_by_id = items.set_index("item_id")

    rows: list[dict] = []
    for item_id, gross in aggregated.items():
        item_code = str(items_by_id.loc[item_id, "item_code"]) if item_id in items_by_id.index else "UNKNOWN"
        item_inventory = latest_inventory.loc[latest_inventory["item_id"] == item_id].copy()
        item_inventory["is_blocked"] = item_inventory["inventory_id"].astype(str).isin(blocked_inventory_ids)

        item_po_lines = purchase_order_lines.loc[purchase_order_lines["item_id"] == item_id]

        result = net_requirement(
            item_id=item_id, item_code=item_code, gross_requirement=gross,
            need_by_date=need_by[item_id], inventory_rows=item_inventory[["on_hand_qty", "is_blocked"]],
            purchase_order_lines=item_po_lines[["qty_ordered", "qty_received", "expected_receipt_date", "status"]],
        )
        rows.append({
            "item_id": result.item_id, "item_code": result.item_code, "period_start_date": result.need_by_date,
            "gross_requirement": round(result.gross_requirement, 2),
            "usable_inventory": round(result.usable_inventory, 2),
            "scheduled_receipts": round(result.scheduled_receipts, 2),
            "net_requirement": round(result.net_requirement, 2),
            "shortage_flag": result.shortage_quantity > 0,
            "provenance": result.provenance,
            "excluded_inventory": round(result.excluded_inventory, 2),
            "excluded_inventory_reason": result.excluded_inventory_reason,
        })
    return rows


def _as_date(value) -> dt.date:
    if isinstance(value, dt.date) and not isinstance(value, dt.datetime):
        return value
    return pd.to_datetime(value).date()


def main() -> None:
    from app.config import settings

    engine = get_engine()
    metadata = reflect_metadata(engine)
    tables = load_all_tables(engine)
    with engine.connect() as conn:
        tables["dq_findings"] = pd.read_sql_table("dq_findings", conn)

    run_id = _get_or_create_baseline_run(engine, metadata, settings.synthetic_seed)
    rows = compute_baseline_material_requirements(tables)
    for row in rows:
        row["run_id"] = run_id

    table = metadata.tables["material_requirements"]
    with engine.begin() as conn:
        conn.execute(sa.text("DELETE FROM material_requirements WHERE run_id = :run_id"), {"run_id": run_id})
        if rows:
            conn.execute(table.insert(), rows)

    n_shortage = sum(1 for r in rows if r["shortage_flag"])
    print(f"Baseline material netting: run_id={run_id}, {len(rows)} components netted, {n_shortage} in shortage.")

    rows_sorted = sorted(rows, key=lambda r: r["net_requirement"], reverse=True)
    print("\nTop 5 by net requirement:")
    for row in rows_sorted[:5]:
        print(f"  {row['item_code']:35s} gross={row['gross_requirement']:>10.2f} "
              f"inv={row['usable_inventory']:>10.2f} receipts={row['scheduled_receipts']:>10.2f} "
              f"net={row['net_requirement']:>10.2f} need_by={row['period_start_date']}")


if __name__ == "__main__":
    main()
