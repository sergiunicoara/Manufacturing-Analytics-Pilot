"""Data-quality rules. Each rule is a pure function `(tables) -> list[Finding]`
where `tables` is a dict of DataFrames keyed by table name (see
app.synthetic.run_generator.TABLE_ORDER for the table set).

Rules fall into two groups, distinguished by `origin` on every Finding:

- INJECTED: the rule targets exactly one defect category the synthetic
  generator's dq_injection.py deliberately plants, and carries the matching
  `manifest_key` so a finding can be cross-checked against
  staging/dq_issue_manifest.json's count for that key [CP2 req. 1].
- ORGANIC: the rule is a general relational/logical integrity check not tied
  to any specific planted defect (e.g. BOM cycle detection — nothing in the
  generator deliberately creates a cycle). These rules are expected to find
  zero instances against the clean generator output; they exist to catch
  defects that *could* arise from relational interactions, not ones that
  were planted [CP2 req. 2].
"""
from __future__ import annotations

import pandas as pd

from app.analytics.bom import find_bom_cycles, find_orphan_bom_components, find_overlapping_bom_revisions
from app.dq.engine import Finding
from app.synthetic.context import VALID_UOM

# ============================================================
# INJECTED-defect rules
# ============================================================


def rule_missing_routing_times(tables) -> list[Finding]:
    df = tables["routing_operations"]
    mask = df["setup_time_minutes"].isna() | df["run_time_minutes_per_unit"].isna()
    findings = []
    for _, row in df.loc[mask].iterrows():
        missing = []
        if pd.isna(row["setup_time_minutes"]):
            missing.append("setup_time_minutes")
        if pd.isna(row["run_time_minutes_per_unit"]):
            missing.append("run_time_minutes_per_unit")
        findings.append(Finding(
            rule_id="missing_routing_times", entity="routing_operations",
            record_id=str(row["routing_operation_id"]), severity="HIGH",
            classification="BLOCKING", origin="INJECTED", manifest_key="missing_routing_times",
            description=f"Routing operation is missing {', '.join(missing)}, so capacity/lead-time "
                        "load cannot be computed for it without assuming a value.",
            detected_value="NULL", expected_constraint="setup_time_minutes AND run_time_minutes_per_unit NOT NULL",
            recommended_action="Obtain the missing time(s) from engineering standards before including "
                                "this operation in a capacity or lead-time calculation.",
        ))
    return findings


def rule_orphan_bom_components(tables) -> list[Finding]:
    orphans = find_orphan_bom_components(tables["items"], tables["bom_components"])
    return [
        Finding(
            rule_id="orphan_bom_components", entity="bom_components",
            record_id=str(row["bom_component_id"]), severity="HIGH",
            classification="BLOCKING", origin="INJECTED", manifest_key="orphan_bom_components",
            description=f"BOM component references component_item_id={row['component_item_id']}, "
                        "which does not exist in items.",
            detected_value=str(row["component_item_id"]), expected_constraint="component_item_id IN items.item_id",
            recommended_action="Resolve the correct component item or remove the stale BOM line; "
                                "BOM explosion treats this branch as blocked until resolved.",
        )
        for _, row in orphans.iterrows()
    ]


def rule_duplicate_item_codes(tables) -> list[Finding]:
    items = tables["items"]
    counts = items.groupby("item_code")["item_id"].apply(list)
    dupes = counts.loc[counts.apply(len) > 1]
    findings = []
    for item_code, item_ids in dupes.items():
        findings.append(Finding(
            rule_id="duplicate_item_codes", entity="items", record_id=",".join(str(i) for i in item_ids),
            severity="LOW", classification="TOLERABLE", origin="INJECTED", manifest_key="duplicate_item_codes",
            description=f"item_code '{item_code}' is shared by {len(item_ids)} distinct item_id values.",
            detected_value=item_code, expected_constraint="item_code unique",
            recommended_action="Confirm with the source ERP whether these are true duplicates or a "
                                "legitimate re-use across plants/eras; all internal joins use item_id "
                                "so this does not block calculation.",
        ))
    return findings


def rule_invalid_uom(tables) -> list[Finding]:
    items = tables["items"]
    mask = ~items["uom"].isin(VALID_UOM)
    return [
        Finding(
            rule_id="invalid_uom", entity="items", record_id=str(row["item_id"]),
            severity="LOW", classification="TOLERABLE", origin="INJECTED", manifest_key="invalid_uom",
            description=f"Item uom '{row['uom']}' is not one of the standard unit codes.",
            detected_value=str(row["uom"]), expected_constraint=f"uom IN {VALID_UOM}",
            recommended_action="Normalize to a standard UOM code during ERP extract cleansing.",
        )
        for _, row in items.loc[mask].iterrows()
    ]


def rule_negative_inventory(tables) -> list[Finding]:
    inv = tables["inventory"]
    mask = inv["on_hand_qty"] < 0
    return [
        Finding(
            rule_id="negative_inventory", entity="inventory", record_id=str(row["inventory_id"]),
            severity="HIGH", classification="BLOCKING", origin="INJECTED", manifest_key="negative_inventory",
            description=f"Inventory snapshot has negative on_hand_qty={row['on_hand_qty']}.",
            detected_value=str(row["on_hand_qty"]), expected_constraint="on_hand_qty >= 0",
            recommended_action="Investigate the source transaction; excluded from usable inventory in "
                                "material netting until corrected.",
        )
        for _, row in inv.loc[mask].iterrows()
    ]


def rule_overlapping_bom_revisions(tables) -> list[Finding]:
    overlaps = find_overlapping_bom_revisions(tables["bom_headers"])
    return [
        Finding(
            rule_id="overlapping_bom_revisions", entity="bom_headers", record_id=f"{bom_a},{bom_b}",
            severity="HIGH", classification="BLOCKING", origin="INJECTED", manifest_key="overlapping_bom_revisions",
            description=f"Parent item {parent_item_id} has two BOM revisions (bom_id {bom_a} and "
                        f"{bom_b}) with overlapping effective-date windows.",
            detected_value=f"bom_id {bom_a}, {bom_b}", expected_constraint="non-overlapping effective windows per parent item",
            recommended_action="Determine which revision is authoritative; BOM explosion treats this "
                                "item's branch as blocked (ambiguous_bom_revision) rather than guessing.",
        )
        for parent_item_id, bom_a, bom_b in overlaps
    ]


def rule_missing_work_centre_mappings(tables) -> list[Finding]:
    df = tables["routing_operations"]
    mask = df["work_centre_id"].isna()
    return [
        Finding(
            rule_id="missing_work_centre_mappings", entity="routing_operations",
            record_id=str(row["routing_operation_id"]), severity="HIGH",
            classification="BLOCKING", origin="INJECTED", manifest_key="missing_work_centre_mappings",
            description="Routing operation has no work_centre_id mapping, so it cannot be included "
                        "in capacity/utilization calculations.",
            detected_value="NULL", expected_constraint="work_centre_id NOT NULL",
            recommended_action="Assign the correct work centre before this operation can contribute "
                                "to capacity or bottleneck analysis.",
        )
        for _, row in df.loc[mask].iterrows()
    ]


def rule_forecasts_without_customers(tables) -> list[Finding]:
    forecasts = tables["customer_forecasts"]
    valid_customers = set(tables["customers"]["customer_id"])
    mask = ~forecasts["customer_id"].isin(valid_customers)
    return [
        Finding(
            rule_id="forecasts_without_customers", entity="customer_forecasts",
            record_id=str(row["forecast_id"]), severity="MEDIUM",
            classification="BLOCKING", origin="INJECTED", manifest_key="forecasts_without_customers",
            description=f"Forecast references customer_id={row['customer_id']}, which does not exist.",
            detected_value=str(row["customer_id"]), expected_constraint="customer_id IN customers.customer_id",
            recommended_action="Cannot attribute this forecast to a customer for consumption/accuracy "
                                "analysis until resolved.",
        )
        for _, row in forecasts.loc[mask].iterrows()
    ]


def rule_production_orders_without_routing(tables) -> list[Finding]:
    po = tables["production_orders"]
    valid_routing = set(tables["routing_headers"]["routing_id"])
    missing_mask = po["routing_id"].isna()
    invalid_mask = po["routing_id"].notna() & (~po["routing_id"].isin(valid_routing))
    findings = []
    for _, row in po.loc[missing_mask].iterrows():
        findings.append(Finding(
            rule_id="production_orders_without_routing", entity="production_orders",
            record_id=str(row["production_order_id"]), severity="HIGH",
            classification="BLOCKING", origin="INJECTED", manifest_key="production_orders_without_routing",
            description="Production order has no routing_id, so lead-time/capacity load cannot be "
                        "computed for it.",
            detected_value="NULL", expected_constraint="routing_id NOT NULL and valid",
            recommended_action="Assign a valid routing before including this order in capacity/lead-time analysis.",
        ))
    for _, row in po.loc[invalid_mask].iterrows():
        findings.append(Finding(
            rule_id="production_orders_without_routing", entity="production_orders",
            record_id=str(row["production_order_id"]), severity="HIGH",
            classification="BLOCKING", origin="INJECTED", manifest_key="production_orders_without_routing",
            description=f"Production order references routing_id={row['routing_id']}, which does not exist.",
            detected_value=str(row["routing_id"]), expected_constraint="routing_id IN routing_headers.routing_id",
            recommended_action="Resolve the correct routing; treated as blocked until corrected.",
        ))
    return findings


def rule_implausible_lead_times(tables) -> list[Finding]:
    df = tables["routing_operations"]
    run_time = df["run_time_minutes_per_unit"]
    mask = run_time.notna() & ((run_time < 0.01) | (run_time > 120))
    return [
        Finding(
            rule_id="implausible_lead_times", entity="routing_operations",
            record_id=str(row["routing_operation_id"]), severity="MEDIUM",
            classification="ASSUMPTION_BASED", origin="INJECTED", manifest_key="implausible_lead_times",
            description=f"run_time_minutes_per_unit={row['run_time_minutes_per_unit']} is outside a "
                        "plausible range for this process.",
            detected_value=str(row["run_time_minutes_per_unit"]), expected_constraint="0.01 <= run_time_minutes_per_unit <= 120",
            recommended_action="Review against engineering standards; downstream calculations using "
                                "this value should be flagged ASSUMED until reviewed.",
        )
        for _, row in df.loc[mask].iterrows()
    ]


def rule_zero_capacity_weeks(tables) -> list[Finding]:
    df = tables["capacity_calendar"]
    mask = df["effective_hours"] <= 0
    return [
        Finding(
            rule_id="zero_capacity_weeks", entity="capacity_calendar",
            record_id=str(row["capacity_calendar_id"]), severity="HIGH",
            classification="BLOCKING", origin="INJECTED", manifest_key="zero_capacity_weeks",
            description=f"Work centre {row['work_centre_id']} shows zero effective capacity for week "
                        f"{row['week_start_date']}.",
            detected_value="0", expected_constraint="effective_hours > 0 (unless a genuine planned shutdown)",
            recommended_action="Confirm whether this is a real planned shutdown or a data error before "
                                "using it in utilization calculations — a real shutdown should be "
                                "recorded via planned_downtime_hours, not a zeroed-out effective_hours.",
        )
        for _, row in df.loc[mask].iterrows()
    ]


def rule_duplicate_sales_orders(tables) -> list[Finding]:
    orders = tables["sales_orders"]
    counts = orders.groupby("order_number")["sales_order_id"].apply(list)
    dupes = counts.loc[counts.apply(len) > 1]
    return [
        Finding(
            rule_id="duplicate_sales_orders", entity="sales_orders", record_id=",".join(str(i) for i in ids),
            severity="LOW", classification="TOLERABLE", origin="INJECTED", manifest_key="duplicate_sales_orders",
            description=f"order_number '{order_number}' appears on {len(ids)} distinct sales_order_id rows.",
            detected_value=order_number, expected_constraint="order_number unique",
            recommended_action="Verify order_number uniqueness in the source ERP export; joins in this "
                                "system use sales_order_id so this does not block calculation.",
        )
        for order_number, ids in dupes.items()
    ]


def rule_missing_cost_records(tables) -> list[Finding]:
    items = tables["items"]
    costed = set(tables["standard_costs"]["item_id"])
    mask = ~items["item_id"].isin(costed)
    return [
        Finding(
            rule_id="missing_cost_records", entity="items", record_id=str(row["item_id"]),
            severity="MEDIUM", classification="ASSUMPTION_BASED", origin="INJECTED", manifest_key="missing_cost_records",
            description=f"Item {row['item_code']} has no standard_costs record.",
            detected_value="NULL", expected_constraint="item_id IN standard_costs.item_id",
            recommended_action="A family-average or category-default cost must be substituted and "
                                "flagged ASSUMED wherever this item's cost is used.",
        )
        for _, row in items.loc[mask].iterrows()
    ]


# ============================================================
# ORGANIC rules — not tied to a specific planted defect
# ============================================================


def rule_bom_cycle_detected(tables) -> list[Finding]:
    cycles = find_bom_cycles(tables["items"], tables["bom_headers"], tables["bom_components"])
    return [
        Finding(
            rule_id="bom_cycle_detected", entity="bom_components", record_id="->".join(str(i) for i in cycle),
            severity="CRITICAL", classification="BLOCKING", origin="ORGANIC",
            description=f"BOM structure contains a cycle: {' -> '.join(str(i) for i in cycle)}.",
            detected_value=str(cycle), expected_constraint="BOM graph must be acyclic",
            recommended_action="BOM explosion cannot terminate through this branch; break the cycle at "
                                "its source before relying on any requirement derived from it.",
        )
        for cycle in cycles
    ]


def rule_non_positive_bom_quantity(tables) -> list[Finding]:
    df = tables["bom_components"]
    mask = df["quantity_per"] <= 0
    return [
        Finding(
            rule_id="non_positive_bom_quantity", entity="bom_components", record_id=str(row["bom_component_id"]),
            severity="HIGH", classification="BLOCKING", origin="ORGANIC",
            description=f"quantity_per={row['quantity_per']} is not positive.",
            detected_value=str(row["quantity_per"]), expected_constraint="quantity_per > 0",
            recommended_action="Correct the BOM quantity; a non-positive quantity would corrupt "
                                "explosion arithmetic.",
        )
        for _, row in df.loc[mask].iterrows()
    ]


def rule_invalid_scrap_pct(tables) -> list[Finding]:
    df = tables["bom_components"]
    mask = (df["scrap_pct"] < 0) | (df["scrap_pct"] >= 1)
    return [
        Finding(
            rule_id="invalid_scrap_pct", entity="bom_components", record_id=str(row["bom_component_id"]),
            severity="HIGH", classification="BLOCKING", origin="ORGANIC",
            description=f"scrap_pct={row['scrap_pct']} is outside the valid [0, 1) range.",
            detected_value=str(row["scrap_pct"]), expected_constraint="0 <= scrap_pct < 1",
            recommended_action="Correct the scrap percentage; a value >= 1 makes the scrap-adjusted "
                                "quantity undefined (division by zero or negative).",
        )
        for _, row in df.loc[mask].iterrows()
    ]


def rule_invalid_batch_size(tables) -> list[Finding]:
    df = tables["routing_operations"]
    mask = df["batch_size"] <= 0
    return [
        Finding(
            rule_id="invalid_batch_size", entity="routing_operations", record_id=str(row["routing_operation_id"]),
            severity="HIGH", classification="BLOCKING", origin="ORGANIC",
            description=f"batch_size={row['batch_size']} is not positive.",
            detected_value=str(row["batch_size"]), expected_constraint="batch_size > 0",
            recommended_action="Correct the batch size; capacity math divides setup time by batch size.",
        )
        for _, row in df.loc[mask].iterrows()
    ]


def rule_invalid_yield_pct(tables) -> list[Finding]:
    df = tables["routing_operations"]
    mask = (df["yield_pct"] <= 0) | (df["yield_pct"] > 1)
    return [
        Finding(
            rule_id="invalid_yield_pct", entity="routing_operations", record_id=str(row["routing_operation_id"]),
            severity="HIGH", classification="BLOCKING", origin="ORGANIC",
            description=f"yield_pct={row['yield_pct']} is outside the valid (0, 1] range.",
            detected_value=str(row["yield_pct"]), expected_constraint="0 < yield_pct <= 1",
            recommended_action="Correct the yield percentage; a non-positive yield makes required "
                                "input quantity undefined.",
        )
        for _, row in df.loc[mask].iterrows()
    ]


def rule_over_received_purchase_order_line(tables) -> list[Finding]:
    df = tables["purchase_order_lines"]
    mask = df["qty_received"] > df["qty_ordered"]
    return [
        Finding(
            rule_id="over_received_purchase_order_line", entity="purchase_order_lines", record_id=str(row["po_line_id"]),
            severity="LOW", classification="ASSUMPTION_BASED", origin="ORGANIC",
            description=f"qty_received={row['qty_received']} exceeds qty_ordered={row['qty_ordered']}.",
            detected_value=str(row["qty_received"]), expected_constraint="qty_received <= qty_ordered",
            recommended_action="Verify against goods-receipt records; scheduled-receipt calculations "
                                "clip remaining quantity at zero rather than going negative.",
        )
        for _, row in df.loc[mask].iterrows()
    ]


def rule_invalid_date_ordering(tables) -> list[Finding]:
    df = tables["production_orders"]
    findings = []
    planned_mask = df["planned_finish"] < df["planned_start"]
    for _, row in df.loc[planned_mask].iterrows():
        findings.append(Finding(
            rule_id="invalid_date_ordering", entity="production_orders", record_id=str(row["production_order_id"]),
            severity="HIGH", classification="BLOCKING", origin="ORGANIC",
            description="planned_finish precedes planned_start.",
            detected_value=f"{row['planned_start']} .. {row['planned_finish']}",
            expected_constraint="planned_finish >= planned_start",
            recommended_action="Correct the planning dates; lead-time calculations cannot use a "
                                "negative duration.",
        ))
    both_actual = df["actual_start"].notna() & df["actual_finish"].notna()
    actual_mask = both_actual & (df["actual_finish"] < df["actual_start"])
    for _, row in df.loc[actual_mask].iterrows():
        findings.append(Finding(
            rule_id="invalid_date_ordering", entity="production_orders", record_id=str(row["production_order_id"]),
            severity="HIGH", classification="BLOCKING", origin="ORGANIC",
            description="actual_finish precedes actual_start.",
            detected_value=f"{row['actual_start']} .. {row['actual_finish']}",
            expected_constraint="actual_finish >= actual_start",
            recommended_action="Correct the actuals; realized lead-time calculations cannot use a "
                                "negative duration.",
        ))
    return findings


ALL_RULES = [
    # injected
    rule_missing_routing_times,
    rule_orphan_bom_components,
    rule_duplicate_item_codes,
    rule_invalid_uom,
    rule_negative_inventory,
    rule_overlapping_bom_revisions,
    rule_missing_work_centre_mappings,
    rule_forecasts_without_customers,
    rule_production_orders_without_routing,
    rule_implausible_lead_times,
    rule_zero_capacity_weeks,
    rule_duplicate_sales_orders,
    rule_missing_cost_records,
    # organic
    rule_bom_cycle_detected,
    rule_non_positive_bom_quantity,
    rule_invalid_scrap_pct,
    rule_invalid_batch_size,
    rule_invalid_yield_pct,
    rule_over_received_purchase_order_line,
    rule_invalid_date_ordering,
]
