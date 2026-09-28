"""Loads staging/dq_issue_manifest.json (written by
app.synthetic.dq_injection.inject_all) so INJECTED findings can be
cross-checked against the exact counts the generator planted — this is what
makes an INJECTED finding "reproducible and traceable back to the seeded
synthetic defect manifest" [CP2 req. 1]: the manifest is itself a
deterministic function of SYNTHETIC_DATA_SEED, and every INJECTED Finding's
manifest_key points at one of its entries.
"""
from __future__ import annotations

import json
import os


def load_manifest(staging_dir: str = "staging") -> dict[str, int]:
    path = os.path.join(staging_dir, "dq_issue_manifest.json")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def cross_check(findings_by_manifest_key: dict[str, int], manifest: dict[str, int]) -> dict[str, dict]:
    """Compares the number of INJECTED findings actually raised per
    manifest_key against the number the generator planted. A mismatch is
    expected in one direction only: some rules raise more findings than
    planted defects when a single planted defect cascades into multiple
    affected records counted individually elsewhere — callers should treat
    large or unexplained mismatches as a bug, not silently ignore them.
    """
    report = {}
    keys = set(findings_by_manifest_key) | set(manifest)
    for key in sorted(keys):
        report[key] = {
            "planted": manifest.get(key, 0),
            "found": findings_by_manifest_key.get(key, 0),
        }
    return report
