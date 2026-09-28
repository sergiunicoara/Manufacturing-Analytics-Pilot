"""Orchestrates the full synthetic ERP data generation pipeline and writes
each table to staging/ as CSV — the "ERP extract" files a real integration
would produce. Deterministic given SYNTHETIC_DATA_SEED.

Usage: python -m app.synthetic.run_generator [--staging-dir staging]
"""
from __future__ import annotations

import argparse
import os

from app.config import settings
from app.synthetic import (
    bom_and_routing,
    capacity_and_cost,
    demand,
    dq_injection,
    execution,
    master_data,
    state_snapshots,
)
from app.synthetic.context import GenContext

# Load order matters: later modules depend on tables built by earlier ones.
TABLE_ORDER = [
    "sites", "warehouses", "customers", "suppliers", "work_centres", "items",
    "bom_headers", "bom_components", "routing_headers", "routing_operations",
    "forecast_versions", "customer_forecasts", "sales_orders", "sales_order_lines",
    "production_orders", "production_order_operations",
    "purchase_orders", "purchase_order_lines",
    "capacity_calendar", "standard_costs",
    "inventory", "wip",
]


def generate(staging_dir: str = "staging") -> GenContext:
    ctx = GenContext(seed=settings.synthetic_seed)

    master_data.generate_all(ctx)
    bom_and_routing.generate_all(ctx)
    demand.generate_all(ctx)
    execution.generate_all(ctx)
    capacity_and_cost.generate_all(ctx)
    state_snapshots.generate_all(ctx)
    manifest = dq_injection.inject_all(ctx, staging_dir)

    os.makedirs(staging_dir, exist_ok=True)
    for table in TABLE_ORDER:
        df = ctx.tables[table]
        df.to_csv(os.path.join(staging_dir, f"{table}.csv"), index=False)

    print("Synthetic data generation complete. Row counts:")
    for table in TABLE_ORDER:
        print(f"  {table:35s} {len(ctx.tables[table]):>7d}")
    print("\nInjected data-quality issues (see staging/dq_issue_manifest.json):")
    for k, v in manifest.items():
        print(f"  {k:35s} {v:>7d}")

    return ctx


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--staging-dir", default="staging")
    args = parser.parse_args()
    generate(args.staging_dir)
