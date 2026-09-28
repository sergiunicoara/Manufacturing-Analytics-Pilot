"""Material netting tests: inventory exclusion (BLOCKING/negative), scheduled
receipt timing, and shortage calculation."""
from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

from app.analytics.netting import net_requirement

NEED_BY = dt.date(2026, 6, 15)


def _inventory(rows):
    return pd.DataFrame(rows, columns=["on_hand_qty", "is_blocked"])


def _po_lines(rows):
    return pd.DataFrame(rows, columns=["qty_ordered", "qty_received", "expected_receipt_date", "status"])


def test_blocking_dq_inventory_is_excluded_from_usable_inventory():
    inventory = _inventory([
        (100.0, False),  # usable
        (50.0, True),    # flagged BLOCKING by DQ engine -> excluded
    ])
    result = net_requirement(1, "ITEM-A", gross_requirement=120.0, need_by_date=NEED_BY,
                              inventory_rows=inventory, purchase_order_lines=_po_lines([]))
    assert result.usable_inventory == 100.0
    assert result.excluded_inventory == 50.0
    assert "BLOCKING" in result.excluded_inventory_reason
    assert result.net_requirement == pytest.approx(20.0)


def test_negative_inventory_is_excluded_even_if_not_flagged():
    inventory = _inventory([
        (100.0, False),
        (-30.0, False),  # negative but not (yet) flagged — still must not contribute
    ])
    result = net_requirement(1, "ITEM-A", gross_requirement=90.0, need_by_date=NEED_BY,
                              inventory_rows=inventory, purchase_order_lines=_po_lines([]))
    assert result.usable_inventory == 100.0
    assert result.excluded_inventory == -30.0
    assert "negative" in result.excluded_inventory_reason
    assert result.net_requirement == pytest.approx(0.0)  # 90 - 100 floored at 0, not 90-70


def test_scheduled_receipt_before_need_by_date_counts():
    po_lines = _po_lines([
        (200.0, 0.0, NEED_BY - dt.timedelta(days=2), "OPEN"),
    ])
    result = net_requirement(1, "ITEM-A", gross_requirement=150.0, need_by_date=NEED_BY,
                              inventory_rows=_inventory([]), purchase_order_lines=po_lines)
    assert result.scheduled_receipts == 200.0
    assert result.net_requirement == 0.0
    assert result.shortage_quantity == 0.0


def test_scheduled_receipt_after_need_by_date_does_not_count():
    po_lines = _po_lines([
        (200.0, 0.0, NEED_BY + dt.timedelta(days=1), "OPEN"),  # arrives too late
    ])
    result = net_requirement(1, "ITEM-A", gross_requirement=150.0, need_by_date=NEED_BY,
                              inventory_rows=_inventory([]), purchase_order_lines=po_lines)
    assert result.scheduled_receipts == 0.0
    assert result.net_requirement == pytest.approx(150.0)
    assert result.shortage_quantity == pytest.approx(150.0)


def test_partial_receipt_counts_only_remaining_quantity():
    po_lines = _po_lines([
        (200.0, 120.0, NEED_BY - dt.timedelta(days=1), "PARTIAL"),
    ])
    result = net_requirement(1, "ITEM-A", gross_requirement=100.0, need_by_date=NEED_BY,
                              inventory_rows=_inventory([]), purchase_order_lines=po_lines)
    assert result.scheduled_receipts == 80.0  # 200 - 120 remaining
    assert result.net_requirement == pytest.approx(20.0)


def test_closed_purchase_order_lines_do_not_count_as_scheduled_receipts():
    po_lines = _po_lines([
        (200.0, 200.0, NEED_BY - dt.timedelta(days=1), "RECEIVED"),
    ])
    result = net_requirement(1, "ITEM-A", gross_requirement=50.0, need_by_date=NEED_BY,
                              inventory_rows=_inventory([]), purchase_order_lines=po_lines)
    assert result.scheduled_receipts == 0.0
    assert result.net_requirement == pytest.approx(50.0)


def test_shortage_is_zero_when_fully_covered():
    result = net_requirement(1, "ITEM-A", gross_requirement=50.0, need_by_date=NEED_BY,
                              inventory_rows=_inventory([(60.0, False)]), purchase_order_lines=_po_lines([]))
    assert result.net_requirement == 0.0
    assert result.shortage_quantity == 0.0


def test_shortage_equals_net_requirement_when_undercovered():
    result = net_requirement(1, "ITEM-A", gross_requirement=500.0, need_by_date=NEED_BY,
                              inventory_rows=_inventory([(60.0, False)]),
                              purchase_order_lines=_po_lines([(100.0, 0.0, NEED_BY, "OPEN")]))
    assert result.net_requirement == pytest.approx(340.0)
    assert result.shortage_quantity == pytest.approx(340.0)
