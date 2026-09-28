"""Production orders + operations, purchase orders + lines.

These represent "what the ERP recorded happened/is planned" — they are not
already reconciled against demand via MRP logic (that reconciliation, i.e.
BOM explosion + material netting against these very inventory/PO/production
records, is exactly what the analytics engine computes from CP2 onward).
Some realistic slippage between planned and actual dates is included so the
later lead-time reconstruction has something real to measure.
"""
from __future__ import annotations

import datetime as dt

import pandas as pd

from app.synthetic.context import GenContext
from app.synthetic.timeline import HISTORY_START, REFERENCE_DATE, FUTURE_END


def _status_for_finish(planned_finish: dt.date, has_actual: bool) -> str:
    if planned_finish < REFERENCE_DATE - dt.timedelta(weeks=1):
        return "COMPLETED"
    if planned_finish < REFERENCE_DATE + dt.timedelta(weeks=1):
        return "IN_PROGRESS"
    return "RELEASED" if planned_finish < REFERENCE_DATE + dt.timedelta(weeks=4) else "PLANNED"


def generate_production_orders(ctx: GenContext) -> None:
    rng = ctx.rng
    site_id = int(ctx.tables["sites"].iloc[0]["site_id"])
    items = ctx.tables["items"]
    manufactured = items.loc[items["item_type"].isin(["FG", "SUBASSY"])]

    bom_by_parent = ctx.tables["bom_headers"].set_index("parent_item_id")["bom_id"].to_dict()
    routing_by_item = ctx.tables["routing_headers"].set_index("item_id")["routing_id"].to_dict()
    routing_ops = ctx.tables["routing_operations"]
    routing_ops_by_routing = {
        rid: grp.sort_values("seq_no") for rid, grp in routing_ops.groupby("routing_id")
    }

    po_seq = ctx.id_seq("production_orders")
    op_seq = ctx.id_seq("production_order_operations")

    po_rows: list[dict] = []
    op_rows: list[dict] = []

    horizon_days = (FUTURE_END - HISTORY_START).days

    for _, item in manufactured.iterrows():
        item_id = int(item["item_id"])
        item_type = item["item_type"]
        n_orders = int(rng.integers(1, 10))
        bom_id = bom_by_parent.get(item_id)
        routing_id = routing_by_item.get(item_id)
        ops = routing_ops_by_routing.get(routing_id)

        for _ in range(n_orders):
            start_offset_days = int(rng.integers(0, horizon_days))
            planned_start = HISTORY_START + dt.timedelta(days=start_offset_days)
            duration_days = int(rng.integers(3, 15))
            planned_finish = planned_start + dt.timedelta(days=duration_days)
            qty = float(rng.integers(5, 60) if item_type == "FG" else rng.integers(10, 250))

            status = _status_for_finish(planned_finish, has_actual=True)
            actual_start = actual_finish = None
            if status in ("COMPLETED", "IN_PROGRESS"):
                slip_days = int(rng.integers(-1, 4))
                actual_start = planned_start + dt.timedelta(days=max(0, slip_days))
                if status == "COMPLETED":
                    actual_duration = max(1, duration_days + int(rng.integers(-1, 5)))
                    actual_finish = actual_start + dt.timedelta(days=actual_duration)

            production_order_id = po_seq.next()
            po_rows.append({
                "production_order_id": production_order_id,
                "order_number": f"PO-PROD-{production_order_id:07d}",
                "item_id": item_id,
                "bom_id": bom_id,
                "routing_id": routing_id,
                "site_id": site_id,
                "qty": qty,
                "status": status,
                "planned_start": planned_start,
                "planned_finish": planned_finish,
                "actual_start": actual_start,
                "actual_finish": actual_finish,
            })

            if ops is not None:
                op_span_days = max(1, duration_days) / max(1, len(ops))
                for i, (_, op) in enumerate(ops.iterrows()):
                    op_planned_start = planned_start + dt.timedelta(days=op_span_days * i)
                    op_planned_finish = planned_start + dt.timedelta(days=op_span_days * (i + 1))
                    op_actual_start = op_actual_finish = None
                    op_status = "PENDING"
                    if status in ("COMPLETED", "IN_PROGRESS"):
                        op_status = "COMPLETED" if status == "COMPLETED" or i == 0 else "PENDING"
                        if op_actual_start is None and actual_start is not None:
                            op_actual_start = actual_start + dt.timedelta(days=op_span_days * i)
                        if op_status == "COMPLETED" and actual_finish is not None:
                            op_actual_finish = actual_start + dt.timedelta(days=op_span_days * (i + 1))
                    op_rows.append({
                        "po_operation_id": op_seq.next(),
                        "production_order_id": production_order_id,
                        "routing_operation_id": int(op["routing_operation_id"]),
                        "seq_no": int(op["seq_no"]),
                        "work_centre_id": op["work_centre_id"],
                        "planned_start": op_planned_start,
                        "planned_finish": op_planned_finish,
                        "actual_start": op_actual_start,
                        "actual_finish": op_actual_finish,
                        "status": op_status,
                    })

    ctx.add_table("production_orders", pd.DataFrame(po_rows))
    ctx.add_table("production_order_operations", pd.DataFrame(op_rows))


def generate_purchase_orders(ctx: GenContext) -> None:
    rng = ctx.rng
    site_id = int(ctx.tables["sites"].iloc[0]["site_id"])
    items = ctx.tables["items"]
    purchased = items.loc[items["item_type"].isin(["RAW", "PACKAGING"])]
    suppliers = ctx.tables["suppliers"]

    # Deterministic supplier assignment per item.
    supplier_ids = suppliers["supplier_id"].tolist()
    item_supplier = {
        int(item_id): int(rng.choice(supplier_ids)) for item_id in purchased["item_id"]
    }
    supplier_lead_time = dict(zip(suppliers["supplier_id"], suppliers["default_lead_time_days"]))

    po_seq = ctx.id_seq("purchase_orders")
    line_seq = ctx.id_seq("purchase_order_lines")
    po_rows: list[dict] = []
    line_rows: list[dict] = []

    horizon_days = (FUTURE_END - HISTORY_START).days

    for _, item in purchased.iterrows():
        item_id = int(item["item_id"])
        supplier_id = item_supplier[item_id]
        lead_time = int(supplier_lead_time[supplier_id])
        n_orders = int(rng.integers(3, 15))
        for _ in range(n_orders):
            order_offset = int(rng.integers(0, horizon_days))
            order_date = HISTORY_START + dt.timedelta(days=order_offset)
            expected_receipt = order_date + dt.timedelta(days=lead_time + int(rng.integers(-2, 6)))
            qty_ordered = float(rng.integers(50, 2000))

            if expected_receipt < REFERENCE_DATE - dt.timedelta(days=3):
                status = "RECEIVED"
                qty_received = qty_ordered
                actual_receipt = expected_receipt + dt.timedelta(days=int(rng.integers(-2, 5)))
            elif expected_receipt < REFERENCE_DATE:
                status = "PARTIAL"
                qty_received = round(qty_ordered * float(rng.uniform(0.3, 0.8)))
                actual_receipt = None
            else:
                status = "OPEN"
                qty_received = 0.0
                actual_receipt = None

            purchase_order_id = po_seq.next()
            po_rows.append({
                "purchase_order_id": purchase_order_id,
                "order_number": f"PO-PUR-{purchase_order_id:07d}",
                "supplier_id": supplier_id,
                "site_id": site_id,
                "order_date": order_date,
                "status": status,
            })
            line_rows.append({
                "po_line_id": line_seq.next(),
                "purchase_order_id": purchase_order_id,
                "item_id": item_id,
                "qty_ordered": qty_ordered,
                "qty_received": qty_received,
                "unit_cost": round(float(rng.uniform(0.5, 45.0)), 4),
                "expected_receipt_date": expected_receipt,
                "actual_receipt_date": actual_receipt,
                "status": status,
            })

    ctx.add_table("purchase_orders", pd.DataFrame(po_rows))
    ctx.add_table("purchase_order_lines", pd.DataFrame(line_rows))


def generate_all(ctx: GenContext) -> None:
    generate_production_orders(ctx)
    generate_purchase_orders(ctx)
