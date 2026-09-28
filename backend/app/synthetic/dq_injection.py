"""Deliberately injects realistic ERP-extract data-quality problems into the
otherwise-clean generated dataset (spec §7). This runs as an explicit,
separate mutation pass over the in-memory tables so the "clean" generation
logic in the other modules stays easy to reason about, and so this file is
the single place that documents exactly what was made dirty and why.

The Data Quality Engine (CP2) is what *detects* these; this module's job is
only to *create* them. A manifest of what was injected (counts per issue) is
written to staging/dq_issue_manifest.json for developer/test reference — it
is not loaded into SQL Server and the DQ engine does not read it; it exists
purely so a human (or a test) can sanity-check "did the DQ engine roughly
find what we planted."
"""
from __future__ import annotations

import json
import os

from app.synthetic.context import GenContext, INVALID_UOM_VARIANTS


def inject_missing_routing_times(ctx: GenContext, rate: float = 0.03) -> int:
    df = ctx.tables["routing_operations"]
    n = max(1, int(len(df) * rate))
    idx = ctx.rng.choice(df.index, size=n, replace=False)
    half = len(idx) // 2
    df.loc[idx[:half], "setup_time_minutes"] = None
    df.loc[idx[half:], "run_time_minutes_per_unit"] = None
    return n


def inject_orphan_bom_components(ctx: GenContext, rate: float = 0.01) -> int:
    df = ctx.tables["bom_components"]
    max_item_id = int(ctx.tables["items"]["item_id"].max())
    n = max(1, int(len(df) * rate))
    idx = ctx.rng.choice(df.index, size=n, replace=False)
    fake_ids = [max_item_id + 1000 + i for i in range(n)]
    df.loc[idx, "component_item_id"] = fake_ids
    return n


def inject_duplicate_item_codes(ctx: GenContext, count: int = 4) -> int:
    items = ctx.tables["items"]
    seq = ctx.id_seq("items")
    # Restricted to RAW/PACKAGING: those items have no BOM/routing of their
    # own, so duplicating just the item row is a clean, self-contained
    # defect. Duplicating a FG/SUBASSY item's code would also need to decide
    # whether the duplicate shares or lacks the original's BOM/routing —
    # a separate, messier defect this generator doesn't model.
    candidates = items.loc[items["item_type"].isin(["RAW", "PACKAGING"])]
    sample = candidates.sample(n=count, random_state=int(ctx.rng.integers(0, 1_000_000)))
    dupes = sample.copy()
    dupes["item_id"] = [seq.next() for _ in range(len(dupes))]
    ctx.add_table("items", __import__("pandas").concat([items, dupes], ignore_index=True))
    return count


def inject_invalid_uom(ctx: GenContext, rate: float = 0.02) -> int:
    items = ctx.tables["items"]
    n = max(1, int(len(items) * rate))
    idx = ctx.rng.choice(items.index, size=n, replace=False)
    items.loc[idx, "uom"] = [ctx.rng.choice(INVALID_UOM_VARIANTS) for _ in range(n)]
    return n


def inject_negative_inventory(ctx: GenContext, rate: float = 0.01) -> int:
    df = ctx.tables["inventory"]
    n = max(1, int(len(df) * rate))
    idx = ctx.rng.choice(df.index, size=n, replace=False)
    df.loc[idx, "on_hand_qty"] = -1.0 * df.loc[idx, "on_hand_qty"].abs()
    return n


def inject_overlapping_bom_revisions(ctx: GenContext, count: int = 6) -> int:
    import pandas as pd

    bom_headers = ctx.tables["bom_headers"]
    seq = ctx.id_seq("bom_headers")
    sample = bom_headers.sample(n=count, random_state=int(ctx.rng.integers(0, 1_000_000)))
    new_rows = []
    for _, row in sample.iterrows():
        new_rows.append({
            "bom_id": seq.next(),
            "parent_item_id": row["parent_item_id"],
            "revision": "B",
            "status": "ACTIVE",
            # Overlaps the original's effective_from..effective_to instead of
            # starting after it — the intentional error.
            "effective_from": row["effective_from"],
            "effective_to": None,
        })
    ctx.add_table("bom_headers", pd.concat([bom_headers, pd.DataFrame(new_rows)], ignore_index=True))
    return count


def inject_missing_work_centre_mappings(ctx: GenContext, rate: float = 0.02) -> int:
    df = ctx.tables["routing_operations"]
    n = max(1, int(len(df) * rate))
    idx = ctx.rng.choice(df.index, size=n, replace=False)
    df.loc[idx, "work_centre_id"] = None
    return n


def inject_forecasts_without_customers(ctx: GenContext, rate: float = 0.01) -> int:
    df = ctx.tables["customer_forecasts"]
    max_customer_id = int(ctx.tables["customers"]["customer_id"].max())
    n = max(1, int(len(df) * rate))
    idx = ctx.rng.choice(df.index, size=n, replace=False)
    df.loc[idx, "customer_id"] = max_customer_id + 500
    return n


def inject_production_orders_without_routing(ctx: GenContext, rate: float = 0.02) -> int:
    df = ctx.tables["production_orders"]
    n = max(1, int(len(df) * rate))
    idx = ctx.rng.choice(df.index, size=n, replace=False)
    half = len(idx) // 2
    df.loc[idx[:half], "routing_id"] = None
    max_routing_id = int(ctx.tables["routing_headers"]["routing_id"].max())
    df.loc[idx[half:], "routing_id"] = max_routing_id + 500
    return n


def inject_implausible_lead_times(ctx: GenContext, rate: float = 0.01) -> int:
    df = ctx.tables["routing_operations"]
    n = max(1, int(len(df) * rate))
    idx = ctx.rng.choice(df.index, size=n, replace=False)
    # Either near-zero or wildly large run time per unit — both implausible.
    for i in idx:
        df.loc[i, "run_time_minutes_per_unit"] = ctx.rng.choice([0.0001, 500.0])
    return n


def inject_zero_capacity_weeks(ctx: GenContext, count: int = 5) -> int:
    df = ctx.tables["capacity_calendar"]
    idx = ctx.rng.choice(df.index, size=count, replace=False)
    df.loc[idx, "available_hours"] = 0.0
    df.loc[idx, "effective_hours"] = 0.0
    return count


def inject_duplicate_sales_orders(ctx: GenContext, count: int = 3) -> int:
    import pandas as pd

    orders = ctx.tables["sales_orders"]
    seq = ctx.id_seq("sales_orders")
    sample = orders.sample(n=count, random_state=int(ctx.rng.integers(0, 1_000_000)))
    dupes = sample.copy()
    dupes["sales_order_id"] = [seq.next() for _ in range(len(dupes))]
    # order_number intentionally kept identical to the original row — the
    # injected defect — everything else about the duplicate is a fresh row.
    ctx.add_table("sales_orders", pd.concat([orders, dupes], ignore_index=True))
    return count


def inject_missing_cost_records(ctx: GenContext, rate: float = 0.03) -> int:
    df = ctx.tables["standard_costs"]
    n = max(1, int(len(df) * rate))
    idx = ctx.rng.choice(df.index, size=n, replace=False)
    ctx.add_table("standard_costs", df.drop(index=idx).reset_index(drop=True))
    return n


def inject_all(ctx: GenContext, staging_dir: str) -> dict:
    manifest = {
        "missing_routing_times": inject_missing_routing_times(ctx),
        "orphan_bom_components": inject_orphan_bom_components(ctx),
        "duplicate_item_codes": inject_duplicate_item_codes(ctx),
        "invalid_uom": inject_invalid_uom(ctx),
        "negative_inventory": inject_negative_inventory(ctx),
        "overlapping_bom_revisions": inject_overlapping_bom_revisions(ctx),
        "missing_work_centre_mappings": inject_missing_work_centre_mappings(ctx),
        "forecasts_without_customers": inject_forecasts_without_customers(ctx),
        "production_orders_without_routing": inject_production_orders_without_routing(ctx),
        "implausible_lead_times": inject_implausible_lead_times(ctx),
        "zero_capacity_weeks": inject_zero_capacity_weeks(ctx),
        "duplicate_sales_orders": inject_duplicate_sales_orders(ctx),
        "missing_cost_records": inject_missing_cost_records(ctx),
    }
    os.makedirs(staging_dir, exist_ok=True)
    with open(os.path.join(staging_dir, "dq_issue_manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)
    return manifest
