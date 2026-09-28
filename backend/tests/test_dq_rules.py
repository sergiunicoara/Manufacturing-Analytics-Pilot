"""DQ rule engine tests: injected-defect detection cross-checked against the
seeded manifest, origin tagging (INJECTED vs ORGANIC), and classification
correctness.
"""
from __future__ import annotations

from collections import Counter

from app.dq.engine import run_all
from app.dq.manifest import load_manifest
from app.dq.rules import ALL_RULES


def test_every_rule_tags_origin_and_classification_from_a_valid_set(tables):
    findings = run_all(tables)
    assert findings, "expected at least some findings against the generated dataset"
    for f in findings:
        assert f.origin in ("INJECTED", "ORGANIC")
        assert f.classification in ("TOLERABLE", "ASSUMPTION_BASED", "BLOCKING")
        if f.origin == "INJECTED":
            assert f.manifest_key is not None
        else:
            assert f.manifest_key is None


def test_injected_findings_meet_or_exceed_the_planted_manifest_count(tables, staging_dir):
    findings = run_all(tables)
    manifest = load_manifest(staging_dir)

    found_by_key = Counter(f.manifest_key for f in findings if f.origin == "INJECTED")
    for key, planted_count in manifest.items():
        found_count = found_by_key.get(key, 0)
        # "meet or exceed" rather than exact equality: a rule may legitimately
        # surface more findings than the number of rows the generator
        # mutated for that specific defect type, when an injected defect
        # elsewhere coincidentally produces the same symptom (documented
        # case: items added by the duplicate_item_codes injection never got
        # a standard_costs row either, since costing ran before that
        # injection step — the missing_cost_records rule correctly finds
        # both sources of the same symptom).
        assert found_count >= planted_count, (
            f"rule for manifest_key={key!r} found fewer findings ({found_count}) than "
            f"the generator planted ({planted_count})"
        )


def test_orphan_bom_components_rule_matches_manifest_exactly(tables, staging_dir):
    findings = [f for f in run_all(tables) if f.rule_id == "orphan_bom_components"]
    manifest = load_manifest(staging_dir)
    assert len(findings) == manifest["orphan_bom_components"]


def test_negative_inventory_rule_matches_manifest_exactly(tables, staging_dir):
    findings = [f for f in run_all(tables) if f.rule_id == "negative_inventory"]
    manifest = load_manifest(staging_dir)
    assert len(findings) == manifest["negative_inventory"]


def test_organic_rules_are_never_tagged_as_injected(tables):
    organic_rule_ids = {
        "bom_cycle_detected", "non_positive_bom_quantity", "invalid_scrap_pct",
        "invalid_batch_size", "invalid_yield_pct", "over_received_purchase_order_line",
        "invalid_date_ordering",
    }
    findings = run_all(tables)
    for f in findings:
        if f.rule_id in organic_rule_ids:
            assert f.origin == "ORGANIC"
            assert f.manifest_key is None


def test_bom_cycle_rule_finds_nothing_in_the_clean_generator_output(tables):
    """The generator never deliberately creates a cycle — this rule exists
    to catch one *if* it occurred, demonstrating an organic-capable check
    that is currently clean, per CP2 req. 2."""
    findings = [f for f in run_all(tables) if f.rule_id == "bom_cycle_detected"]
    assert findings == []


def test_routing_validation_rules_cover_missing_and_invalid_values(tables):
    findings = run_all(tables)
    by_rule = {}
    for f in findings:
        by_rule.setdefault(f.rule_id, []).append(f)

    assert len(by_rule.get("missing_work_centre_mappings", [])) > 0
    assert len(by_rule.get("missing_routing_times", [])) > 0
    assert all(f.entity == "routing_operations" for f in by_rule["missing_work_centre_mappings"])
    assert all(f.classification == "BLOCKING" for f in by_rule["missing_work_centre_mappings"])

    # Organic routing-validation rules run cleanly (no invalid batch sizes or
    # yields in the generated data) but must not error and must be BLOCKING
    # by definition if they ever do fire.
    for rule_id in ("invalid_batch_size", "invalid_yield_pct"):
        for f in by_rule.get(rule_id, []):
            assert f.classification == "BLOCKING"
            assert f.origin == "ORGANIC"


def test_all_registered_rules_run_without_error(tables):
    for rule_fn in ALL_RULES:
        result = rule_fn(tables)
        assert isinstance(result, list)
