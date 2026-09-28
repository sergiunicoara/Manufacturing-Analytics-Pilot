"""Forecast versions/snapshots, customer forecasts (versioned, revised as
delivery approaches), sales orders and sales order lines.

Demand streams: each customer is assigned a handful of FG items it regularly
buys (realistic B2B repeat-purchase pattern) rather than every customer
forecasting every item. "Actual demand" for a (customer, item, delivery week)
is defined as the total sales-order-line quantity realized for it; forecast
snapshots are noisy predictions of that same actual value, with the noise
band shrinking as the delivery week approaches — reproducing the worked
example in PLAN.md (26 weeks before: 700 ... actual: 980).
"""
from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd

from app.synthetic.context import GenContext
from app.synthetic.timeline import ALL_DELIVERY_WEEKS, FORECAST_SNAPSHOT_WEEKS, REFERENCE_DATE

HORIZON_WEEKS_BEFORE_DELIVERY = [26, 20, 16, 12, 8, 4, 2, 1]

# Only delivery weeks up to ~4 weeks past the reference date have "actual"
# orders realized; further-future weeks are forecast-only (nothing to
# compare against yet), which is realistic.
LAST_DELIVERY_WEEK_WITH_ACTUALS = REFERENCE_DATE + dt.timedelta(weeks=4)


def _snapshot_version_lookup(ctx: GenContext) -> tuple[pd.DataFrame, dict[dt.date, int]]:
    seq = ctx.id_seq("forecast_versions")
    rows = []
    lookup: dict[dt.date, int] = {}
    for wk in FORECAST_SNAPSHOT_WEEKS:
        vid = seq.next()
        rows.append({
            "forecast_version_id": vid,
            "snapshot_date": wk,
            "description": f"Weekly forecast snapshot {wk.isoformat()}",
        })
        lookup[wk] = vid
    df = pd.DataFrame(rows)
    ctx.add_table("forecast_versions", df)
    return df, lookup


def _assign_demand_streams(ctx: GenContext) -> list[tuple[int, int]]:
    """Each customer regularly buys 2-5 specific FG items. Returns a list of
    (customer_id, item_id) demand streams."""
    customers = ctx.tables["customers"]
    fg_items = ctx.tables["items"]
    fg_items = fg_items.loc[fg_items["item_type"] == "FG"]
    fg_item_ids = fg_items["item_id"].tolist()

    streams: list[tuple[int, int]] = []
    for customer_id in customers["customer_id"]:
        n_items = int(ctx.rng.integers(2, 6))
        chosen = ctx.rng.choice(fg_item_ids, size=n_items, replace=False)
        for item_id in chosen:
            streams.append((int(customer_id), int(item_id)))
    return streams


def generate_all(ctx: GenContext) -> None:
    _, version_lookup = _snapshot_version_lookup(ctx)
    site_id = int(ctx.tables["sites"].iloc[0]["site_id"])
    streams = _assign_demand_streams(ctx)

    forecast_seq = ctx.id_seq("customer_forecasts")
    order_seq = ctx.id_seq("sales_orders")
    line_seq = ctx.id_seq("sales_order_lines")

    forecast_rows: list[dict] = []
    order_rows: list[dict] = []
    line_rows: list[dict] = []

    rng = ctx.rng

    for customer_id, item_id in streams:
        # Per-stream baseline level and mild trend/seasonality so different
        # streams look distinct rather than pure noise around one constant.
        # Calibrated (CP3) so that combined weekly demand across the ~28
        # variants sharing a welding line lands baseline utilization in a
        # credible 60-100% band, with room for a +40% demand scenario to
        # push it into overload -- the original (20, 220) range produced a
        # 3-12x welding overload even at baseline, which read as a broken
        # generator rather than a "currently healthy plant" per the demo
        # story's premise.
        base_level = float(rng.uniform(5, 55))
        trend_per_week = float(rng.normal(0, base_level * 0.01))
        phase = float(rng.uniform(0, 2 * np.pi))

        for week_idx, delivery_week in enumerate(ALL_DELIVERY_WEEKS):
            # Not every stream orders every single week — sparsify.
            if rng.random() > 0.35:
                continue

            seasonal = 1.0 + 0.15 * np.sin(phase + week_idx / 6.0)
            level = max(5.0, base_level + trend_per_week * week_idx)
            actual_qty = None
            if delivery_week <= LAST_DELIVERY_WEEK_WITH_ACTUALS:
                actual_qty = max(1, round(level * seasonal * float(rng.uniform(0.85, 1.15))))

            # --- forecast revisions for this delivery week ---
            reference_for_noise = actual_qty if actual_qty is not None else level * seasonal
            for h in HORIZON_WEEKS_BEFORE_DELIVERY:
                snapshot_date = delivery_week - dt.timedelta(weeks=h)
                if snapshot_date not in version_lookup:
                    continue
                noise_scale = 0.40 * (h / 26.0) + 0.03
                forecast_qty = max(0.0, reference_for_noise * (1 + float(rng.normal(0, noise_scale))))
                forecast_rows.append({
                    "forecast_id": forecast_seq.next(),
                    "forecast_version_id": version_lookup[snapshot_date],
                    "customer_id": customer_id,
                    "item_id": item_id,
                    "site_id": site_id,
                    "delivery_period_start": delivery_week,
                    "qty": round(forecast_qty, 2),
                })

            # --- realized sales order(s) for this delivery week ---
            if actual_qty is None:
                continue
            n_lines = int(rng.integers(1, 3))
            remaining = actual_qty
            order_id = order_seq.next()
            order_date = delivery_week - dt.timedelta(days=int(rng.integers(10, 35)))
            order_rows.append({
                "sales_order_id": order_id,
                "order_number": f"SO-{order_id:06d}",
                "customer_id": customer_id,
                "site_id": site_id,
                "order_date": order_date,
                "status": "CLOSED" if delivery_week < REFERENCE_DATE else "OPEN",
            })
            for i in range(n_lines):
                qty = remaining if i == n_lines - 1 else max(1, round(remaining * rng.uniform(0.3, 0.6)))
                qty = min(qty, remaining)
                remaining -= qty
                promised = delivery_week + dt.timedelta(days=int(rng.integers(-3, 4)))
                line_rows.append({
                    "line_id": line_seq.next(),
                    "sales_order_id": order_id,
                    "item_id": item_id,
                    "qty": qty,
                    "unit_price": round(float(rng.uniform(150, 4500)), 2),
                    "requested_ship_date": delivery_week,
                    "promised_ship_date": promised,
                })
                if remaining <= 0:
                    break

    ctx.add_table("customer_forecasts", pd.DataFrame(forecast_rows))
    ctx.add_table("sales_orders", pd.DataFrame(order_rows))
    ctx.add_table("sales_order_lines", pd.DataFrame(line_rows))
