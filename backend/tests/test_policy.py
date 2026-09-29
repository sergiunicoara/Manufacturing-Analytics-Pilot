import datetime as dt

import pandas as pd
import pytest

from app.analytics import policy as pol

D0 = dt.date(2026, 1, 5)


def _tables(tolerance_days: int, fg_days: int, sub_days: int, n_orders: int = 10, fg_done: int = 4,
            sub_done: int = 4, weekly_qty=None):
    items = pd.DataFrame({"item_id": [1, 2, 3, 4], "item_code": ["FG-A", "SUB-A", "RAW-A", "FG-B"],
                          "item_type": ["FG", "SUBASSY", "RAW", "FG"]})
    bom_headers = pd.DataFrame({"bom_id": [10, 11, 12], "parent_item_id": [1, 2, 4]})
    bom_components = pd.DataFrame({"bom_id": [10, 11, 12], "component_item_id": [2, 3, 2]})
    weekly_qty = weekly_qty or [10] * n_orders
    orders = pd.DataFrame({"sales_order_id": range(1, n_orders + 1),
                           "order_date": [D0 + dt.timedelta(weeks=i) for i in range(n_orders)]})
    lines = pd.DataFrame({"line_id": range(1, n_orders + 1), "sales_order_id": range(1, n_orders + 1),
                          "item_id": [1] * n_orders, "qty": weekly_qty[:n_orders],
                          "requested_ship_date": [D0 + dt.timedelta(weeks=i, days=tolerance_days) for i in range(n_orders)]})
    po_rows = [(1, fg_days)] * fg_done + [(2, sub_days)] * sub_done
    production_orders = pd.DataFrame({"production_order_id": range(1, len(po_rows) + 1),
                                      "item_id": [i for i, _ in po_rows], "status": "COMPLETED",
                                      "actual_start": [D0] * len(po_rows),
                                      "actual_finish": [D0 + dt.timedelta(days=d) for _, d in po_rows]})
    routing_headers = pd.DataFrame({"routing_id": [100, 101], "item_id": [2, 1]})
    routing_operations = pd.DataFrame({"routing_id": [100, 101], "work_centre_id": [9, 5]})
    return {"items": items, "bom_headers": bom_headers, "bom_components": bom_components, "sales_orders": orders,
            "sales_order_lines": lines, "production_orders": production_orders,
            "routing_headers": routing_headers, "routing_operations": routing_operations}


def _fg_a(tables, wape=0.1):
    recs = {r.item_id: r for r in pol.recommend_policies(tables, {1: wape})}
    return recs[1]


def test_make_to_order_at_exact_boundary():
    rec = _fg_a(_tables(tolerance_days=10, fg_days=4, sub_days=6))       # cumulative 10 = tolerance
    assert rec.policy == pol.MTO and rec.cumulative_days == 10


def test_assemble_to_order_when_only_own_route_fits():
    rec = _fg_a(_tables(tolerance_days=9, fg_days=4, sub_days=6))
    assert rec.policy == pol.ATO
    assert rec.own_route_days == 4 and rec.cumulative_days == 10


def test_make_to_stock_when_own_route_exceeds_tolerance():
    rec = _fg_a(_tables(tolerance_days=3, fg_days=4, sub_days=6))
    assert rec.policy == pol.MTS


def test_missing_inputs_give_insufficient_evidence_not_a_default():
    rec = _fg_a(_tables(tolerance_days=10, fg_days=4, sub_days=6, n_orders=4))
    assert rec.policy == pol.INSUFFICIENT and rec.confidence == "NONE"
    assert any("customer tolerance" in b for b in rec.blockers)
    rec = _fg_a(_tables(tolerance_days=10, fg_days=4, sub_days=6, sub_done=2))
    assert rec.policy == pol.INSUFFICIENT
    assert any("manufactured component" in b for b in rec.blockers)


def test_volatility_and_missing_forecast_error_lower_confidence_not_policy():
    stable = _fg_a(_tables(tolerance_days=3, fg_days=4, sub_days=6), wape=0.1)
    assert stable.confidence == "HIGH"
    volatile = _fg_a(_tables(tolerance_days=3, fg_days=4, sub_days=6,
                             weekly_qty=[1, 50, 1, 50, 1, 50, 1, 50, 1, 50]), wape=None)
    assert volatile.policy == pol.MTS
    assert volatile.confidence == "LOW"
    assert any("obsolescence" in a for a in volatile.alternatives)


def test_thresholds_are_configurable():
    tables = _tables(tolerance_days=10, fg_days=4, sub_days=6, n_orders=4)
    recs = pol.recommend_policies(tables, {1: 0.1}, pol.PolicyThresholds(min_order_count=4, min_demand_weeks=4))
    assert {r.item_id: r.policy for r in recs}[1] == pol.MTO


def test_decoupling_candidates_common_component_and_constraint_routing():
    tables = _tables(tolerance_days=10, fg_days=4, sub_days=6)
    candidates = {c["item_id"]: c for c in pol.decoupling_candidates(tables, constrained_work_centres={9})}
    assert set(candidates) == {2}                       # SUB-A: used by FG-A and FG-B, routed through WC 9
    assert candidates[2]["finished_goods_served"] == 2
    assert candidates[2]["routes_through_constraint"] is True
    assert pol.decoupling_candidates(tables, set(), pol.PolicyThresholds(min_common_parents=3)) == []
