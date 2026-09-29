"""Forecast reconstruction, scoped consumption [CORR-3], and accuracy
metrics. Pure functions over DataFrames — no DB access.

Consumption window: an order line may consume the forecast bucket whose
delivery_period_start is within `consumption_window_weeks` (default 4,
app.config.settings.forecast_consumption_window_weeks) of the order's
requested_ship_date, scoped by (customer_id, item_id, site_id). This is the
documented window from PLAN.md/CORR-3 and ASSUMPTIONS.md.
"""
from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd

CONSUMPTION_POLICY = (
    "Nearest eligible bucket: each order line consumes only the forecast bucket for the same customer, "
    "item and site whose delivery week is closest to the requested ship date, within ±{weeks} weeks "
    "(ties go to the earlier-listed bucket). No spillover: quantity above that bucket's remaining "
    "forecast is not taken from another bucket; it stays firm demand only. Orders consume in order-date "
    "sequence. Planning demand = firm orders + unconsumed forecast."
)

# ============================================================
# Reconstruction
# ============================================================


def reconstruct_forecast_history(
    customer_forecasts_df: pd.DataFrame,
    forecast_versions_df: pd.DataFrame,
    sales_order_lines_df: pd.DataFrame,
    sales_orders_df: pd.DataFrame,
) -> pd.DataFrame:
    """Every weekly forecast revision, preserved (not aggregated away), with
    the snapshot date, delivery bucket, customer/item/site, forecast qty,
    the eventual actual/firm order qty for that same bucket, and the horizon
    in weeks at which that revision was made [CP3 req. 3].
    """
    history = customer_forecasts_df.merge(
        forecast_versions_df[["forecast_version_id", "snapshot_date"]], on="forecast_version_id", how="left"
    )
    history["snapshot_date"] = pd.to_datetime(history["snapshot_date"])
    history["delivery_period_start"] = pd.to_datetime(history["delivery_period_start"])
    history["horizon_weeks"] = ((history["delivery_period_start"] - history["snapshot_date"]).dt.days / 7).round().astype(int)

    actual = _actual_qty_by_bucket(sales_order_lines_df, sales_orders_df)
    history = history.merge(actual, on=["customer_id", "item_id", "site_id", "delivery_period_start"], how="left")
    history["actual_qty"] = history["actual_qty"].fillna(0.0)

    return history[[
        "forecast_id", "snapshot_date", "delivery_period_start", "customer_id", "item_id", "site_id",
        "qty", "actual_qty", "horizon_weeks",
    ]].rename(columns={"qty": "forecast_qty"})


def _actual_qty_by_bucket(sales_order_lines_df: pd.DataFrame, sales_orders_df: pd.DataFrame) -> pd.DataFrame:
    lines = sales_order_lines_df.merge(
        sales_orders_df[["sales_order_id", "customer_id", "site_id"]], on="sales_order_id", how="left"
    )
    lines["delivery_period_start"] = pd.to_datetime(lines["requested_ship_date"])
    grouped = lines.groupby(["customer_id", "item_id", "site_id", "delivery_period_start"], as_index=False)["qty"].sum()
    return grouped.rename(columns={"qty": "actual_qty"})


# ============================================================
# Scoped consumption [CORR-3]
# ============================================================


def latest_forecast_per_bucket(
    customer_forecasts_df: pd.DataFrame, forecast_versions_df: pd.DataFrame
) -> pd.DataFrame:
    """The current (most recent snapshot) forecast for each
    (customer, item, site, delivery_period_start) — what planning should
    actually use "as of now", as opposed to the full revision history."""
    merged = customer_forecasts_df.merge(
        forecast_versions_df[["forecast_version_id", "snapshot_date"]], on="forecast_version_id", how="left"
    )
    merged["snapshot_date"] = pd.to_datetime(merged["snapshot_date"])
    merged = merged.sort_values("snapshot_date")
    latest = merged.groupby(["customer_id", "item_id", "site_id", "delivery_period_start"], as_index=False).last()
    return latest[["forecast_id", "customer_id", "item_id", "site_id", "delivery_period_start", "qty"]]


def consume_forecast(
    customer_forecasts_df: pd.DataFrame,
    forecast_versions_df: pd.DataFrame,
    sales_order_lines_df: pd.DataFrame,
    sales_orders_df: pd.DataFrame,
    consumption_window_weeks: int = 4,
) -> dict:
    """Consumes each order line against the current forecast bucket for its
    (customer, item, site) within the documented consumption window, in
    order-date sequence (earlier orders consume first). Returns:
      - consumption_events: list of dicts (forecast_id, sales_order_line_id, consumed_qty, consumption_date)
      - remaining_forecast: DataFrame [customer_id, item_id, site_id, delivery_period_start, forecast_qty, remaining_qty]
      - planning_demand: remaining_forecast + firm orders, the non-double-counted total demand signal
    """
    latest = latest_forecast_per_bucket(customer_forecasts_df, forecast_versions_df).copy()
    latest["delivery_period_start"] = pd.to_datetime(latest["delivery_period_start"])
    latest["remaining_qty"] = latest["qty"]
    remaining_by_forecast_id = dict(zip(latest["forecast_id"], latest["remaining_qty"]))

    lines = sales_order_lines_df.merge(
        sales_orders_df[["sales_order_id", "customer_id", "site_id", "order_date"]], on="sales_order_id", how="left"
    ).sort_values("order_date")

    # Index candidate forecast buckets per (customer, item, site) for window matching.
    by_key: dict[tuple, pd.DataFrame] = {
        key: grp for key, grp in latest.groupby(["customer_id", "item_id", "site_id"])
    }

    consumption_events = []
    window = dt.timedelta(weeks=consumption_window_weeks)

    for _, line in lines.iterrows():
        key = (line["customer_id"], line["item_id"], line["site_id"])
        candidates = by_key.get(key)
        if candidates is None or candidates.empty:
            continue
        requested = pd.to_datetime(line["requested_ship_date"])
        diffs = (candidates["delivery_period_start"] - requested).abs()
        within_window = diffs <= window
        if not within_window.any():
            continue
        best_idx = diffs.loc[within_window].idxmin()
        forecast_id = candidates.loc[best_idx, "forecast_id"]

        remaining = remaining_by_forecast_id.get(forecast_id, 0.0)
        consumed = min(remaining, line["qty"])
        if consumed <= 0:
            continue
        remaining_by_forecast_id[forecast_id] = remaining - consumed
        consumption_events.append({
            "forecast_id": forecast_id,
            "sales_order_line_id": line["line_id"],
            "consumed_qty": consumed,
            "consumption_date": line["order_date"],
        })

    latest["remaining_qty"] = latest["forecast_id"].map(remaining_by_forecast_id)
    remaining_forecast = latest.rename(columns={"qty": "forecast_qty"})[
        ["customer_id", "item_id", "site_id", "delivery_period_start", "forecast_qty", "remaining_qty"]
    ]

    firm_orders = lines.groupby(["customer_id", "item_id", "site_id"], as_index=False)["qty"].sum().rename(
        columns={"qty": "firm_order_qty"}
    )

    return {
        "consumption_events": consumption_events,
        "remaining_forecast": remaining_forecast,
        "firm_orders": firm_orders,
    }


# ============================================================
# Accuracy metrics [CP3 req. 5]
# ============================================================

HORIZON_BUCKETS = [(0, 2), (3, 4), (5, 8), (9, 12), (13, 20), (21, 999)]


def _horizon_bucket_label(weeks: int) -> str:
    for lo, hi in HORIZON_BUCKETS:
        if lo <= weeks <= hi:
            return f"{lo}-{hi}w" if hi < 999 else f"{lo}w+"
    return "unknown"


def compute_accuracy_by_horizon(history_df: pd.DataFrame) -> pd.DataFrame:
    """WAPE is the headline metric (robust to near-zero actuals since it's a
    sum-of-errors / sum-of-actuals ratio, not a per-row ratio). MAE and bias
    are also reported; MAPE is deliberately not computed as a headline
    metric per PLAN.md Flag #3 (unstable near zero actual demand).

    Horizon buckets whose total realized (summed) actual demand is zero
    report WAPE/bias as NaN rather than a divide-by-zero or a misleadingly
    large number — a handful of individual zero-actual rows inside an
    otherwise-active bucket do NOT distort the bucket's WAPE, since WAPE
    here is computed from summed numerator/denominator, not a per-row
    average [CP3 req. 5].
    """
    df = history_df.copy()
    df["horizon_bucket"] = df["horizon_weeks"].apply(_horizon_bucket_label)
    df["abs_error"] = (df["forecast_qty"] - df["actual_qty"]).abs()
    df["error"] = df["forecast_qty"] - df["actual_qty"]

    rows = []
    bucket_order = [_horizon_bucket_label(lo) for lo, _ in HORIZON_BUCKETS]
    for bucket in bucket_order:
        grp = df.loc[df["horizon_bucket"] == bucket]
        if grp.empty:
            continue
        sum_actual = grp["actual_qty"].sum()
        n = len(grp)
        mae = grp["abs_error"].mean()
        if sum_actual > 0:
            wape = grp["abs_error"].sum() / sum_actual
            bias = grp["error"].sum() / sum_actual
        else:
            wape = np.nan
            bias = np.nan
        rows.append({
            "horizon_bucket": bucket, "n_observations": n, "mae": mae,
            "wape": wape, "bias": bias, "sum_actual_qty": sum_actual,
        })
    return pd.DataFrame(rows)


def derive_planning_demand(
    consumption_result: dict, sales_order_lines_df: pd.DataFrame, sales_orders_df: pd.DataFrame
) -> pd.DataFrame:
    """Planning demand = firm orders already placed + remaining unconsumed
    forecast, aggregated across customers per (item, delivery week) — what
    the period engine's production plan should actually target. Firm orders
    are counted at full value once; remaining_forecast is already net of
    consumption, so summing the two never double-counts [CORR-3]."""
    lines = sales_order_lines_df.merge(
        sales_orders_df[["sales_order_id", "customer_id", "site_id"]], on="sales_order_id", how="left"
    )
    lines["delivery_period_start"] = pd.to_datetime(lines["requested_ship_date"])
    firm = lines.groupby(["item_id", "delivery_period_start"], as_index=False)["qty"].sum().rename(
        columns={"qty": "firm_order_qty"}
    )

    remaining = consumption_result["remaining_forecast"].copy()
    remaining["delivery_period_start"] = pd.to_datetime(remaining["delivery_period_start"])
    remaining_agg = remaining.groupby(["item_id", "delivery_period_start"], as_index=False)["remaining_qty"].sum()

    merged = firm.merge(remaining_agg, on=["item_id", "delivery_period_start"], how="outer").fillna(0.0)
    merged["planning_qty"] = merged["firm_order_qty"] + merged["remaining_qty"]
    return merged[["item_id", "delivery_period_start", "firm_order_qty", "remaining_qty", "planning_qty"]]


def to_top_level_demand(planning_demand_df: pd.DataFrame) -> dict:
    """Reshapes derive_planning_demand's output into the
    {period_start_date: {item_id: qty}} form run_period_engine expects."""
    result: dict = {}
    for _, row in planning_demand_df.iterrows():
        period = row["delivery_period_start"].date() if hasattr(row["delivery_period_start"], "date") else row["delivery_period_start"]
        result.setdefault(period, {})[int(row["item_id"])] = float(row["planning_qty"])
    return result


def find_best_historical_example(history_df: pd.DataFrame, min_revisions: int = 4) -> tuple | None:
    """[CP3.1 req. F] Picks a (customer, item, site, delivery_period_start)
    bucket that is fully realized (actual_qty > 0) and has the most
    preserved weekly revisions, so the full weekly-forecast-revisions ->
    firm-order -> horizon-specific-error chain can be shown end-to-end for
    a real, already-happened delivery, not just the future/unrealized
    example. Prefers more revisions, then an earlier (more clearly
    "historical") delivery bucket. Returns None if nothing qualifies.
    """
    realized = history_df.loc[history_df["actual_qty"] > 0]
    if realized.empty:
        return None
    counts = realized.groupby(["customer_id", "item_id", "site_id", "delivery_period_start"]).size()
    counts = counts.loc[counts >= min(min_revisions, counts.max())]
    if counts.empty:
        return None
    best = counts.sort_values(ascending=False)
    top_count = best.iloc[0]
    tied = best.loc[best == top_count]
    # Among the most-revised buckets, prefer the earliest delivery date.
    tied_keys = list(tied.index)
    tied_keys.sort(key=lambda k: k[3])
    return tied_keys[0]


def compute_forecast_volatility(history_df: pd.DataFrame) -> pd.DataFrame:
    """Revision-to-revision volatility per (customer, item, site, delivery
    bucket): std dev of successive % changes in forecast_qty as the horizon
    shrinks. One row per demand stream/bucket that had >=2 revisions."""
    rows = []
    grouped = history_df.sort_values("horizon_weeks", ascending=False).groupby(
        ["customer_id", "item_id", "site_id", "delivery_period_start"]
    )
    for key, grp in grouped:
        if len(grp) < 2:
            continue
        qty = grp["forecast_qty"].to_numpy()
        prev = qty[:-1]
        nxt = qty[1:]
        with np.errstate(divide="ignore", invalid="ignore"):
            pct_change = np.where(prev != 0, (nxt - prev) / prev, np.nan)
        pct_change = pct_change[~np.isnan(pct_change)]
        if len(pct_change) == 0:
            continue
        customer_id, item_id, site_id, delivery_period_start = key
        rows.append({
            "customer_id": customer_id, "item_id": item_id, "site_id": site_id,
            "delivery_period_start": delivery_period_start,
            "n_revisions": len(grp), "volatility_std_pct_change": float(np.std(pct_change)),
        })
    return pd.DataFrame(rows)


def realized_wape_by_item(tables: dict[str, pd.DataFrame]) -> dict[int, float]:
    """Per-item WAPE over forecast revisions whose delivery bucket has realized orders.
    Items with no realized demand are absent (unknown), not zero."""
    needed = {"customer_forecasts", "forecast_versions", "sales_order_lines", "sales_orders"}
    if not needed.issubset(tables):
        return {}
    history = reconstruct_forecast_history(tables["customer_forecasts"], tables["forecast_versions"],
                                           tables["sales_order_lines"], tables["sales_orders"])
    realized = history.loc[history["actual_qty"] > 0].copy()
    if realized.empty:
        return {}
    realized["absolute_error"] = (realized["forecast_qty"] - realized["actual_qty"]).abs()
    grouped = realized.groupby("item_id")[["absolute_error", "actual_qty"]].sum()
    return {int(item): float(row["absolute_error"] / row["actual_qty"])
            for item, row in grouped.iterrows() if row["actual_qty"] > 0}
