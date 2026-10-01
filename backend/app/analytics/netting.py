"""Material netting [CORR-4]: gross requirement - usable inventory -
scheduled receipts = net requirement / shortage.

Pure function — the caller is responsible for having already determined
which inventory rows are excluded (via `is_blocked`, sourced from a
BLOCKING dq_findings lookup by the orchestration layer) before calling this.
That keeps this module free of any DB/DQ-engine coupling: it only knows
"blocked or not", not why.
"""
from __future__ import annotations

import datetime as dt

import pandas as pd

from app.analytics.models import NettingResult


def net_requirement(
    item_id: int,
    item_code: str,
    gross_requirement: float,
    need_by_date: dt.date,
    inventory_rows: pd.DataFrame,
    purchase_order_lines: pd.DataFrame,
) -> NettingResult:
    """
    inventory_rows: columns [on_hand_qty, is_blocked] — one row per
        inventory snapshot record being considered (typically: the most
        recent snapshot per warehouse for this item).
    purchase_order_lines: columns [qty_ordered, qty_received,
        expected_receipt_date, status] — every open PO line for this item.
    """
    usable_mask = (~inventory_rows["is_blocked"]) & (inventory_rows["on_hand_qty"] > 0)
    usable_inventory = float(inventory_rows.loc[usable_mask, "on_hand_qty"].sum()) if not inventory_rows.empty else 0.0

    excluded_rows = inventory_rows.loc[~usable_mask] if not inventory_rows.empty else inventory_rows
    excluded_inventory = float(excluded_rows["on_hand_qty"].sum()) if not excluded_rows.empty else 0.0
    excluded_reason = None
    if not excluded_rows.empty:
        reasons = []
        if (excluded_rows["is_blocked"]).any():
            reasons.append("excluded: inventory row has a BLOCKING data-quality finding")
        if (excluded_rows["on_hand_qty"] <= 0).any():
            reasons.append("excluded: non-positive/negative on-hand quantity")
        excluded_reason = "; ".join(reasons) if reasons else None

    if purchase_order_lines.empty:
        scheduled_receipts = 0.0
    else:
        open_mask = purchase_order_lines["status"].isin(["OPEN", "PARTIAL"])
        # [CP2 req. 6] only receipts expected before the need-by date count —
        # a receipt arriving after the item is needed doesn't cover the need.
        timing_mask = purchase_order_lines["expected_receipt_date"].apply(_as_date) <= need_by_date
        remaining_qty = purchase_order_lines["qty_ordered"] - purchase_order_lines["qty_received"]
        scheduled_receipts = float(remaining_qty.loc[open_mask & timing_mask].clip(lower=0).sum())

    net = max(0.0, gross_requirement - usable_inventory - scheduled_receipts)
    shortage = net  # scheduled_receipts already excludes late-arriving supply, so any
    # remaining net requirement is, by construction, unmet before the need-by date.

    return NettingResult(
        item_id=item_id,
        item_code=item_code,
        need_by_date=need_by_date,
        gross_requirement=gross_requirement,
        usable_inventory=usable_inventory,
        excluded_inventory=excluded_inventory,
        excluded_inventory_reason=excluded_reason,
        scheduled_receipts=scheduled_receipts,
        net_requirement=net,
        shortage_quantity=shortage,
    )


def net_single_position(gross_requirement: float, on_hand: float, receipts_due: float) -> tuple[float, float, float]:
    """Scalar form of net_requirement for one unblocked on-hand position and receipts already due by the
    need date: (usable_inventory, scheduled_receipts, net_requirement). Same rules: non-positive on-hand
    is not usable, negative receipts are clipped to zero. Used by the period engine in its inner loop."""
    usable = float(on_hand) if on_hand > 0 else 0.0
    receipts = float(receipts_due) if receipts_due > 0 else 0.0
    return usable, receipts, max(0.0, gross_requirement - usable - receipts)


def _as_date(value) -> dt.date:
    if isinstance(value, dt.date) and not isinstance(value, dt.datetime):
        return value
    return pd.to_datetime(value).date()
