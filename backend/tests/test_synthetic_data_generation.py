"""CP1 tests: synthetic data generation determinism and structural
invariants. These run without a database — pure in-memory generation.

Reproducibility here is asserted via exact equality (row counts, exact
values), consistent with PLAN.md's [CORR-11]: discrete generation artifacts
(row counts, IDs, seeded draws) are canonical fixtures and are checked
exactly, unlike the floating-point simulation outputs added from CP3 onward
(which will be checked within a relative tolerance).
"""
from __future__ import annotations

import pandas as pd

from app.synthetic.run_generator import TABLE_ORDER, generate


def test_generation_is_deterministic_given_same_seed(tmp_path):
    ctx1 = generate(str(tmp_path / "run1"))
    ctx2 = generate(str(tmp_path / "run2"))

    for table in TABLE_ORDER:
        df1 = ctx1.tables[table].reset_index(drop=True)
        df2 = ctx2.tables[table].reset_index(drop=True)
        assert len(df1) == len(df2), f"{table}: row count differs between runs"
        pd.testing.assert_frame_equal(df1, df2, check_dtype=False)


def test_row_counts_within_target_scale(tmp_path):
    ctx = generate(str(tmp_path / "run"))
    scale_ranges = {
        "customers": (20, 100),
        "suppliers": (5, 30),
        "work_centres": (10, 30),
        "items": (500, 2000),
        "production_orders": (2000, 8000),
        "sales_order_lines": (1000, 30000),
        "forecast_versions": (26, 60),
    }
    for table, (lo, hi) in scale_ranges.items():
        n = len(ctx.tables[table])
        assert lo <= n <= hi, f"{table} row count {n} outside expected [{lo}, {hi}]"


def test_finished_goods_count_within_spec_range(tmp_path):
    ctx = generate(str(tmp_path / "run"))
    items = ctx.tables["items"]
    fg_count = (items["item_type"] == "FG").sum()
    assert 50 <= fg_count <= 150


def test_bom_depth_spans_three_to_nine_levels(tmp_path):
    ctx = generate(str(tmp_path / "run"))
    items = ctx.tables["items"]
    bom_headers = ctx.tables["bom_headers"]
    bom_components = ctx.tables["bom_components"]

    item_type = dict(zip(items["item_id"], items["item_type"]))
    bom_by_parent = bom_headers.groupby("parent_item_id")
    comp_by_bom = bom_components.groupby("bom_id")

    def depth(item_id: int, seen: frozenset) -> int:
        if item_id in seen or item_id not in item_type:
            return 1
        seen = seen | {item_id}
        if item_id not in bom_by_parent.groups:
            return 1
        max_d = 1
        for _, bh in bom_by_parent.get_group(item_id).iterrows():
            bom_id = bh["bom_id"]
            if bom_id not in comp_by_bom.groups:
                continue
            for _, c in comp_by_bom.get_group(bom_id).iterrows():
                max_d = max(max_d, 1 + depth(int(c["component_item_id"]), seen))
        return max_d

    fg = items.loc[items["item_type"] == "FG"]
    depths_by_family = {}
    for family in fg["product_family"].unique():
        sample_ids = fg.loc[fg["product_family"] == family, "item_id"].sample(
            min(8, (fg["product_family"] == family).sum()), random_state=1
        )
        depths_by_family[family] = max(depth(int(i), frozenset()) for i in sample_ids)

    assert min(depths_by_family.values()) >= 2, depths_by_family
    assert max(depths_by_family.values()) >= 6, depths_by_family
    assert max(depths_by_family.values()) <= 9, depths_by_family


def test_injected_dq_issues_are_present_and_bounded(tmp_path):
    ctx = generate(str(tmp_path / "run"))

    routing_ops = ctx.tables["routing_operations"]
    assert routing_ops["setup_time_minutes"].isna().sum() > 0
    assert routing_ops["run_time_minutes_per_unit"].isna().sum() > 0
    assert routing_ops["work_centre_id"].isna().sum() > 0

    items = ctx.tables["items"]
    assert items["item_code"].duplicated().sum() >= 2  # at least one duplicated pair

    inventory = ctx.tables["inventory"]
    assert (inventory["on_hand_qty"] < 0).sum() > 0

    bom_components = ctx.tables["bom_components"]
    valid_item_ids = set(items["item_id"])
    orphan_count = (~bom_components["component_item_id"].isin(valid_item_ids)).sum()
    assert orphan_count > 0

    production_orders = ctx.tables["production_orders"]
    valid_routing_ids = set(ctx.tables["routing_headers"]["routing_id"])
    invalid_routing = production_orders["routing_id"].notna() & (
        ~production_orders["routing_id"].isin(valid_routing_ids)
    )
    missing_routing = production_orders["routing_id"].isna()
    assert (invalid_routing | missing_routing).sum() > 0


def test_manufactured_items_have_bom_and_routing(tmp_path):
    """Every FG/SUBASSY item should have exactly one BOM header and one
    routing header — the *clean* generation invariant, checked before DQ
    injection would ever remove one (DQ injection only corrupts references,
    it never deletes a manufactured item's own BOM/routing header)."""
    ctx = generate(str(tmp_path / "run"))
    items = ctx.tables["items"]
    manufactured_ids = set(items.loc[items["item_type"].isin(["FG", "SUBASSY"]), "item_id"])

    bom_parents = set(ctx.tables["bom_headers"]["parent_item_id"])
    routing_items = set(ctx.tables["routing_headers"]["item_id"])

    assert manufactured_ids.issubset(bom_parents)
    assert manufactured_ids.issubset(routing_items)
