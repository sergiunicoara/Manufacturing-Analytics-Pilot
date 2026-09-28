"""Runs the full DQ rule engine against the live SQL Server database, writes
results to dq_findings, and prints a classification/origin summary plus a
cross-check against the seeded defect manifest.

Usage: python -m app.dq.run_dq_engine [--staging-dir staging]
"""
from __future__ import annotations

import argparse
from collections import Counter

from app.analytics.data_access import load_all_tables, write_findings
from app.db.connection import get_engine, reflect_metadata
from app.dq.engine import run_all
from app.dq.manifest import cross_check, load_manifest


def main(staging_dir: str = "staging") -> None:
    engine = get_engine()
    tables = load_all_tables(engine)

    findings = run_all(tables)

    metadata = reflect_metadata(engine)
    n_written = write_findings(engine, findings, metadata)

    print(f"DQ engine: {len(findings)} findings raised, {n_written} written to dq_findings.\n")

    by_classification = Counter(f.classification for f in findings)
    by_origin = Counter(f.origin for f in findings)
    print("By classification:")
    for k in ("BLOCKING", "ASSUMPTION_BASED", "TOLERABLE"):
        print(f"  {k:20s} {by_classification.get(k, 0):>5d}")
    print("\nBy origin:")
    for k in ("INJECTED", "ORGANIC"):
        print(f"  {k:20s} {by_origin.get(k, 0):>5d}")

    print("\nBy rule_id:")
    by_rule = Counter(f.rule_id for f in findings)
    for rule_id, count in sorted(by_rule.items()):
        print(f"  {rule_id:40s} {count:>5d}")

    manifest = load_manifest(staging_dir)
    found_by_key = Counter(f.manifest_key for f in findings if f.manifest_key)
    print("\nCross-check vs. seeded defect manifest (planted -> found):")
    report = cross_check(dict(found_by_key), manifest)
    for key, counts in report.items():
        flag = "" if counts["found"] >= counts["planted"] else "  <-- FEWER FOUND THAN PLANTED"
        print(f"  {key:40s} planted={counts['planted']:>5d}  found={counts['found']:>5d}{flag}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--staging-dir", default="staging")
    args = parser.parse_args()
    main(args.staging_dir)
