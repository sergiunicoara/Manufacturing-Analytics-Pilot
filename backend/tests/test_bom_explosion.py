"""BOM explosion tests, including two golden end-to-end cases (CAB-100,
CAB-200) with exact expected quantities.

Since BOM quantities in the generated dataset are seeded-random floats (not
hand-authored constants), "exact expected quantity" is verified by
differential testing: `independent_gross_requirement` below is a small,
obviously-correct recursive walk written completely independently of
app.analytics.bom.explode(), reading the same source DataFrames. If the two
disagree, one of them has a bug — hardcoding a literal magic number here
would be equally derived from a one-off print-and-paste and strictly less
robust to a seed/generator change.
"""
from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

from app.analytics.bom import explode, find_bom_cycles

AS_OF = dt.date(2026, 6, 1)


def independent_gross_requirement(
    top_item_id: int, top_qty: float, as_of_date: dt.date,
    items_df: pd.DataFrame, bom_headers_df: pd.DataFrame, bom_components_df: pd.DataFrame,
    target_item_id: int,
) -> float:
    total = 0.0

    def walk(current_id: int, cumulative: float) -> None:
        nonlocal total
        if current_id == target_item_id:
            total += cumulative * top_qty
        headers = bom_headers_df.loc[
            (bom_headers_df["parent_item_id"] == current_id)
            & (bom_headers_df["effective_from"] <= as_of_date)
            & (bom_headers_df["effective_to"].isna() | (bom_headers_df["effective_to"] >= as_of_date))
        ]
        if len(headers) != 1:
            return
        comps = bom_components_df.loc[bom_components_df["bom_id"] == headers.iloc[0]["bom_id"]]
        for _, c in comps.iterrows():
            if c["component_item_id"] not in set(items_df["item_id"]):
                continue
            mult = c["quantity_per"] / (1 - c["scrap_pct"])
            walk(int(c["component_item_id"]), cumulative * mult)

    walk(top_item_id, 1.0)
    return total


def _first_variant(tables, family: str):
    items = tables["items"]
    fg = items.loc[(items["item_type"] == "FG") & (items["product_family"] == family)]
    return fg.sort_values("item_code").iloc[0]


def test_cab100_golden_explosion_matches_independent_calculation(tables):
    fg = _first_variant(tables, "CAB-100")
    qty = 25.0
    result = explode(
        item_id=int(fg["item_id"]), qty=qty, as_of_date=AS_OF,
        items_df=tables["items"], bom_headers_df=tables["bom_headers"], bom_components_df=tables["bom_components"],
    )
    assert result.lines, "expected a non-trivial BOM explosion"

    # Check every component the production explosion found agrees with the
    # independent walk, for every non-blocked component it aggregated.
    checked = 0
    for component_item_id, gross in result.aggregated_requirements.items():
        expected = independent_gross_requirement(
            int(fg["item_id"]), qty, AS_OF, tables["items"], tables["bom_headers"], tables["bom_components"],
            component_item_id,
        )
        assert gross == pytest.approx(expected, rel=1e-9), f"component {component_item_id} mismatch"
        checked += 1
    assert checked >= 8  # CAB-100 has frame/door/back-panel/mounting-plate/elec-kit/packaging components


def test_cab200_deep_explosion_matches_independent_calculation_and_reaches_depth(tables):
    fg = _first_variant(tables, "CAB-200")
    qty = 10.0
    result = explode(
        item_id=int(fg["item_id"]), qty=qty, as_of_date=AS_OF,
        items_df=tables["items"], bom_headers_df=tables["bom_headers"], bom_components_df=tables["bom_components"],
    )

    max_depth = max(len(line.path) for line in result.lines)
    assert max_depth >= 6, f"expected CAB-200's frame chain to reach depth >=6, got {max_depth}"

    for component_item_id, gross in result.aggregated_requirements.items():
        expected = independent_gross_requirement(
            int(fg["item_id"]), qty, AS_OF, tables["items"], tables["bom_headers"], tables["bom_components"],
            component_item_id,
        )
        assert gross == pytest.approx(expected, rel=1e-9), f"component {component_item_id} mismatch"


def test_scrap_and_yield_propagate_multiplicatively_along_the_path(tables):
    fg = _first_variant(tables, "CAB-100")
    result = explode(
        item_id=int(fg["item_id"]), qty=1.0, as_of_date=AS_OF,
        items_df=tables["items"], bom_headers_df=tables["bom_headers"], bom_components_df=tables["bom_components"],
    )
    for line in result.lines:
        if line.incomplete or not line.path:
            continue
        expected_cumulative = 1.0
        for step in line.path:
            expected_cumulative *= step.quantity_per / (1 - step.scrap_pct)
        assert line.cumulative_quantity_per_unit == pytest.approx(expected_cumulative, rel=1e-9)


# --- Structural defect handling: cycles, orphans, revisions ----------------


def _tiny_items(rows):
    return pd.DataFrame(rows, columns=["item_id", "item_code", "description", "item_type", "uom", "product_family", "is_active"])


def _tiny_bom_headers(rows):
    return pd.DataFrame(rows, columns=["bom_id", "parent_item_id", "revision", "status", "effective_from", "effective_to"])


def _tiny_bom_components(rows):
    return pd.DataFrame(rows, columns=["bom_component_id", "bom_id", "component_item_id", "quantity_per", "scrap_pct", "effective_from", "effective_to"])


def test_explode_detects_cycle_and_marks_branch_incomplete():
    # A -> B -> A (a two-node cycle)
    items = _tiny_items([
        (1, "A", "A", "SUBASSY", "PC", None, True),
        (2, "B", "B", "SUBASSY", "PC", None, True),
    ])
    boms = _tiny_bom_headers([
        (10, 1, "A", "ACTIVE", dt.date(2024, 1, 1), None),
        (11, 2, "A", "ACTIVE", dt.date(2024, 1, 1), None),
    ])
    comps = _tiny_bom_components([
        (100, 10, 2, 1.0, 0.0, dt.date(2024, 1, 1), None),  # A consumes B
        (101, 11, 1, 1.0, 0.0, dt.date(2024, 1, 1), None),  # B consumes A -- cycle
    ])
    result = explode(1, 1.0, AS_OF, items, boms, comps)
    blocked = [l for l in result.lines if l.blocked_reason == "cycle_detected"]
    assert len(blocked) == 1
    assert 1 not in result.aggregated_requirements or result.aggregated_requirements.get(1, 0) == 0

    # The standalone structural cycle finder should also find it.
    cycles = find_bom_cycles(items, boms, comps)
    assert len(cycles) == 1


def test_explode_detects_orphan_component_and_does_not_skip_silently():
    items = _tiny_items([(1, "A", "A", "SUBASSY", "PC", None, True)])
    boms = _tiny_bom_headers([(10, 1, "A", "ACTIVE", dt.date(2024, 1, 1), None)])
    comps = _tiny_bom_components([(100, 10, 999, 1.0, 0.0, dt.date(2024, 1, 1), None)])  # 999 doesn't exist

    result = explode(1, 5.0, AS_OF, items, boms, comps)
    assert len(result.lines) == 1
    assert result.lines[0].incomplete
    assert result.lines[0].blocked_reason == "orphan_component"
    assert result.incomplete is True
    assert 999 not in result.aggregated_requirements  # not silently included as if it were fine


def test_explode_blocks_on_overlapping_active_revisions_rather_than_guessing():
    items = _tiny_items([
        (1, "A", "A", "SUBASSY", "PC", None, True),
        (2, "B", "B", "RAW", "KG", None, True),
    ])
    boms = _tiny_bom_headers([
        (10, 1, "A", "ACTIVE", dt.date(2024, 1, 1), None),
        (11, 1, "B", "ACTIVE", dt.date(2024, 3, 1), None),  # overlaps with bom_id 10 (both open-ended)
    ])
    comps = _tiny_bom_components([(100, 10, 2, 1.0, 0.0, dt.date(2024, 1, 1), None)])

    result = explode(1, 1.0, dt.date(2024, 6, 1), items, boms, comps)
    assert len(result.lines) == 1
    assert result.lines[0].blocked_reason == "ambiguous_bom_revision"
    # The node's own required quantity (1.0, from the top-level order) is
    # still known and reported -- it came from the caller, not a guess. What
    # is blocked is knowledge of its own components, which is why this line
    # has no children and is excluded from aggregated_requirements.
    assert result.lines[0].gross_requirement == 1.0
    assert result.lines[0].component_item_id == 1
    assert 1 not in result.aggregated_requirements


def test_effective_date_selection_picks_the_correct_non_overlapping_revision():
    items = _tiny_items([
        (1, "A", "A", "SUBASSY", "PC", None, True),
        (2, "B", "B", "RAW", "KG", None, True),
        (3, "C", "C", "RAW", "KG", None, True),
    ])
    boms = _tiny_bom_headers([
        (10, 1, "A", "EXPIRED", dt.date(2024, 1, 1), dt.date(2024, 6, 30)),
        (11, 1, "B", "ACTIVE", dt.date(2024, 7, 1), None),
    ])
    comps = _tiny_bom_components([
        (100, 10, 2, 2.0, 0.0, dt.date(2024, 1, 1), dt.date(2024, 6, 30)),  # revision A: 2x item B
        (101, 11, 3, 3.0, 0.0, dt.date(2024, 7, 1), None),                 # revision B: 3x item C
    ])

    before = explode(1, 1.0, dt.date(2024, 3, 1), items, boms, comps)
    assert before.aggregated_requirements == {2: 2.0}

    after = explode(1, 1.0, dt.date(2024, 8, 1), items, boms, comps)
    assert after.aggregated_requirements == {3: 3.0}
