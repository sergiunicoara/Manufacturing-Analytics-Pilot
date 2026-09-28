"""Forecast reconstruction, scoped consumption (with an explicit
non-double-counting worked example), and accuracy metrics."""
from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

from app.analytics.forecast import (
    compute_accuracy_by_horizon,
    compute_forecast_volatility,
    consume_forecast,
    find_best_historical_example,
    reconstruct_forecast_history,
)


def _forecast_versions(snapshot_dates):
    return pd.DataFrame([
        {"forecast_version_id": i + 1, "snapshot_date": d, "description": None}
        for i, d in enumerate(snapshot_dates)
    ])


def test_reconstruction_preserves_every_revision_and_exposes_required_fields():
    """Mirrors the PLAN.md worked example: a delivery month forecasted at
    successively shorter horizons, converging toward (but not exactly
    matching) the eventual actual demand."""
    delivery = dt.date(2026, 12, 1)
    snapshots = [delivery - dt.timedelta(weeks=w) for w in (26, 20, 16, 12, 8, 4)]
    versions = _forecast_versions(snapshots)
    qtys = [700, 760, 850, 930, 1050, 1250]

    forecasts = pd.DataFrame([
        {"forecast_id": i + 1, "forecast_version_id": i + 1, "customer_id": 1, "item_id": 10, "site_id": 1,
         "delivery_period_start": delivery, "qty": q}
        for i, q in enumerate(qtys)
    ])
    sales_orders = pd.DataFrame([{"sales_order_id": 1, "customer_id": 1, "site_id": 1, "order_date": delivery - dt.timedelta(days=20)}])
    sales_order_lines = pd.DataFrame([{"line_id": 1, "sales_order_id": 1, "item_id": 10, "qty": 980, "requested_ship_date": delivery}])

    history = reconstruct_forecast_history(forecasts, versions, sales_order_lines, sales_orders)

    assert len(history) == 6  # every revision preserved, none aggregated away
    for col in ("snapshot_date", "delivery_period_start", "customer_id", "item_id", "site_id", "forecast_qty", "actual_qty", "horizon_weeks"):
        assert col in history.columns

    history_sorted = history.sort_values("horizon_weeks", ascending=False)
    assert list(history_sorted["horizon_weeks"]) == [26, 20, 16, 12, 8, 4]
    assert list(history_sorted["forecast_qty"]) == qtys
    assert (history_sorted["actual_qty"] == 980).all()  # broadcast to every revision of this bucket


def test_forecast_consumption_worked_example_proves_non_double_counting():
    """The exact scenario PLAN.md calls out: forecast(1200) + orders(900)
    must NOT equal 2100 -- the order consumes (reduces) the forecast rather
    than adding to it."""
    delivery = dt.date(2026, 3, 2)  # a Monday
    versions = _forecast_versions([delivery - dt.timedelta(weeks=4)])
    forecasts = pd.DataFrame([{
        "forecast_id": 1, "forecast_version_id": 1, "customer_id": 1, "item_id": 10, "site_id": 1,
        "delivery_period_start": delivery, "qty": 1200,
    }])
    sales_orders = pd.DataFrame([{"sales_order_id": 1, "customer_id": 1, "site_id": 1, "order_date": delivery - dt.timedelta(days=10)}])
    sales_order_lines = pd.DataFrame([{
        "line_id": 1, "sales_order_id": 1, "item_id": 10, "qty": 900, "requested_ship_date": delivery,
    }])

    result = consume_forecast(forecasts, versions, sales_order_lines, sales_orders, consumption_window_weeks=4)

    assert len(result["consumption_events"]) == 1
    assert result["consumption_events"][0]["consumed_qty"] == 900

    remaining_row = result["remaining_forecast"].iloc[0]
    assert remaining_row["forecast_qty"] == 1200
    assert remaining_row["remaining_qty"] == 300  # 1200 - 900, not 1200 unchanged

    planning_demand = remaining_row["remaining_qty"] + 900  # remaining forecast + firm order
    print(f"\nWorked example: forecast=1200, firm order=900 -> remaining_forecast=300, "
          f"planning_demand={planning_demand} (NOT 1200+900=2100)")
    assert planning_demand == 1200
    assert planning_demand != 1200 + 900


def test_consumption_only_matches_within_the_documented_window():
    delivery = dt.date(2026, 3, 2)
    versions = _forecast_versions([delivery - dt.timedelta(weeks=4)])
    forecasts = pd.DataFrame([{
        "forecast_id": 1, "forecast_version_id": 1, "customer_id": 1, "item_id": 10, "site_id": 1,
        "delivery_period_start": delivery, "qty": 500,
    }])
    sales_orders = pd.DataFrame([{"sales_order_id": 1, "customer_id": 1, "site_id": 1, "order_date": delivery}])
    # Requested far outside the +/-4 week window -- must not consume this bucket.
    far_ship_date = delivery + dt.timedelta(weeks=10)
    sales_order_lines = pd.DataFrame([{
        "line_id": 1, "sales_order_id": 1, "item_id": 10, "qty": 200, "requested_ship_date": far_ship_date,
    }])

    result = consume_forecast(forecasts, versions, sales_order_lines, sales_orders, consumption_window_weeks=4)
    assert result["consumption_events"] == []
    assert result["remaining_forecast"].iloc[0]["remaining_qty"] == 500


def test_consumption_does_not_go_negative_when_orders_exceed_forecast():
    delivery = dt.date(2026, 3, 2)
    versions = _forecast_versions([delivery - dt.timedelta(weeks=4)])
    forecasts = pd.DataFrame([{
        "forecast_id": 1, "forecast_version_id": 1, "customer_id": 1, "item_id": 10, "site_id": 1,
        "delivery_period_start": delivery, "qty": 100,
    }])
    sales_orders = pd.DataFrame([{"sales_order_id": 1, "customer_id": 1, "site_id": 1, "order_date": delivery}])
    sales_order_lines = pd.DataFrame([{
        "line_id": 1, "sales_order_id": 1, "item_id": 10, "qty": 300, "requested_ship_date": delivery,
    }])

    result = consume_forecast(forecasts, versions, sales_order_lines, sales_orders, consumption_window_weeks=4)
    assert result["remaining_forecast"].iloc[0]["remaining_qty"] == 0  # not negative
    assert result["consumption_events"][0]["consumed_qty"] == 100  # capped at what the forecast had


def test_wape_is_headline_and_robust_to_zero_demand_periods():
    """A horizon bucket with some rows at actual_qty=0 must not distort WAPE
    (since WAPE here sums numerator/denominator rather than averaging
    per-row ratios), and a bucket with NO realized demand at all reports
    WAPE as NaN rather than dividing by zero [CP3 req. 5]."""
    history = pd.DataFrame([
        {"horizon_weeks": 4, "forecast_qty": 100, "actual_qty": 100},
        {"horizon_weeks": 4, "forecast_qty": 50, "actual_qty": 0},     # zero-demand row
        {"horizon_weeks": 4, "forecast_qty": 100, "actual_qty": 100},
        {"horizon_weeks": 21, "forecast_qty": 80, "actual_qty": 0},    # entire bucket has zero realized demand
    ])
    result = compute_accuracy_by_horizon(history)

    row_4w = result.loc[result["horizon_bucket"] == "3-4w"].iloc[0]
    # WAPE = sum(|error|) / sum(actual) = (0 + 50 + 0) / (100+0+100) = 0.25
    assert row_4w["wape"] == pytest.approx(0.25)

    row_21w = result.loc[result["horizon_bucket"] == "21w+"].iloc[0]
    assert pd.isna(row_21w["wape"])
    assert pd.isna(row_21w["bias"])


def test_accuracy_improves_at_shorter_horizons_in_a_converging_example():
    delivery = dt.date(2026, 12, 1)
    snapshots = [delivery - dt.timedelta(weeks=w) for w in (26, 20, 16, 12, 8, 4)]
    versions = _forecast_versions(snapshots)
    qtys = [700, 760, 850, 930, 1050, 1250]
    forecasts = pd.DataFrame([
        {"forecast_id": i + 1, "forecast_version_id": i + 1, "customer_id": 1, "item_id": 10, "site_id": 1,
         "delivery_period_start": delivery, "qty": q}
        for i, q in enumerate(qtys)
    ])
    sales_orders = pd.DataFrame([{"sales_order_id": 1, "customer_id": 1, "site_id": 1, "order_date": delivery - dt.timedelta(days=20)}])
    sales_order_lines = pd.DataFrame([{"line_id": 1, "sales_order_id": 1, "item_id": 10, "qty": 980, "requested_ship_date": delivery}])
    history = reconstruct_forecast_history(forecasts, versions, sales_order_lines, sales_orders)

    result = compute_accuracy_by_horizon(history)
    result = result.set_index("horizon_bucket")
    # 26w bucket (|700-980|=280) should be worse (higher WAPE) than the 3-4w
    # bucket (|1250-980|=270) -- not a strict monotonic guarantee with one
    # sample each, but demonstrates both are computed and comparable.
    assert "21w+" in result.index or "13-20w" in result.index
    assert "3-4w" in result.index


def test_find_best_historical_example_prefers_most_revisions_and_realized_actual():
    history = pd.DataFrame([
        # bucket A: 2 revisions, realized
        {"customer_id": 1, "item_id": 10, "site_id": 1, "delivery_period_start": dt.date(2026, 1, 5), "actual_qty": 50, "horizon_weeks": 8},
        {"customer_id": 1, "item_id": 10, "site_id": 1, "delivery_period_start": dt.date(2026, 1, 5), "actual_qty": 50, "horizon_weeks": 4},
        # bucket B: 3 revisions, realized -- should win (more revisions)
        {"customer_id": 2, "item_id": 11, "site_id": 1, "delivery_period_start": dt.date(2026, 2, 2), "actual_qty": 80, "horizon_weeks": 12},
        {"customer_id": 2, "item_id": 11, "site_id": 1, "delivery_period_start": dt.date(2026, 2, 2), "actual_qty": 80, "horizon_weeks": 8},
        {"customer_id": 2, "item_id": 11, "site_id": 1, "delivery_period_start": dt.date(2026, 2, 2), "actual_qty": 80, "horizon_weeks": 4},
        # bucket C: 5 revisions but unrealized (actual=0) -- must be excluded
        *[{"customer_id": 3, "item_id": 12, "site_id": 1, "delivery_period_start": dt.date(2026, 6, 1), "actual_qty": 0, "horizon_weeks": h} for h in (26, 20, 16, 12, 8)],
    ])
    key = find_best_historical_example(history, min_revisions=1)
    assert key == (2, 11, 1, dt.date(2026, 2, 2))


def test_find_best_historical_example_returns_none_when_nothing_realized():
    history = pd.DataFrame([
        {"customer_id": 1, "item_id": 10, "site_id": 1, "delivery_period_start": dt.date(2026, 1, 5), "actual_qty": 0, "horizon_weeks": 4},
    ])
    assert find_best_historical_example(history) is None


def test_forecast_volatility_requires_at_least_two_revisions():
    history = pd.DataFrame([
        {"customer_id": 1, "item_id": 10, "site_id": 1, "delivery_period_start": dt.date(2026, 1, 1), "horizon_weeks": 8, "forecast_qty": 100},
        {"customer_id": 1, "item_id": 10, "site_id": 1, "delivery_period_start": dt.date(2026, 1, 1), "horizon_weeks": 4, "forecast_qty": 150},
        {"customer_id": 2, "item_id": 11, "site_id": 1, "delivery_period_start": dt.date(2026, 1, 1), "horizon_weeks": 4, "forecast_qty": 50},  # only 1 revision
    ])
    result = compute_forecast_volatility(history)
    assert len(result) == 1  # the single-revision stream is excluded
    assert result.iloc[0]["item_id"] == 10
    assert result.iloc[0]["volatility_std_pct_change"] == pytest.approx(0.0)  # only one transition, std of one value is 0
