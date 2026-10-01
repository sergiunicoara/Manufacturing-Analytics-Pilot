"""Check a delivered extract against the data-request contract: PASS / WARN / BLOCK.

Row counts alone do not establish completeness, so this also checks keys,
relationships, required fields, date ordering, history length and cadence,
forecast revision depth and BOM depth. It reports what is missing; it never
fills anything in.

Usage:
  python -m app.integration.completeness --source sql [--out report.json]
  python -m app.integration.completeness --source csv --path staging [--out report.json]
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

import pandas as pd

PASS, WARN, BLOCK = "PASS", "WARN", "BLOCK"
DEFAULT_CONTRACT = Path(__file__).with_name("data_request_contract.json")


@dataclass
class CheckResult:
    check: str
    dataset: str
    status: str
    detail: str
    observed: object = None


def load_contract(path: str | Path = DEFAULT_CONTRACT) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _dates(series: pd.Series) -> pd.Series:
    return pd.to_datetime(series, errors="coerce")


def check_dataset(spec: dict, frame: pd.DataFrame | None) -> list[CheckResult]:
    name = spec["name"]
    if frame is None:
        return [CheckResult("dataset_present", name, BLOCK if spec["required"] else WARN,
                            f"Table {spec['table']} was not delivered.")]
    results = [CheckResult("dataset_present", name, PASS, f"{len(frame)} rows", len(frame))]
    if frame.empty:
        results.append(CheckResult("rows_present", name, BLOCK if spec["required"] else WARN, "Table is empty.", 0))
        return results
    missing = [c for c in spec["columns"] if c not in frame.columns]
    results.append(CheckResult("required_columns", name, BLOCK if missing else PASS,
                               f"Missing required columns: {missing}" if missing else "All required columns present.",
                               missing))
    optional_missing = [c for c in spec.get("optional_columns", []) if c not in frame.columns]
    if optional_missing:
        results.append(CheckResult("optional_columns", name, WARN, f"Missing optional columns: {optional_missing}",
                                   optional_missing))
    key = [c for c in spec["key"] if c in frame.columns]
    if key == spec["key"]:
        dup = int(frame.duplicated(key).sum())
        results.append(CheckResult("key_unique", name, BLOCK if dup else PASS, f"{dup} duplicate keys on {key}", dup))
    for column in spec.get("business_key", []):
        if column in frame.columns:
            dup = int(frame[column].duplicated().sum())
            results.append(CheckResult(f"business_key_unique:{column}", name, WARN if dup else PASS,
                                       f"{dup} duplicate {column} values", dup))
    for column, limit in spec.get("max_null_pct", {}).items():
        if column in frame.columns:
            pct = round(100.0 * frame[column].isna().mean(), 2)
            results.append(CheckResult(f"null_pct:{column}", name, WARN if pct > limit else PASS,
                                       f"{pct}% null (limit {limit}%)", pct))
    for column, allowed in spec.get("allowed_values", {}).items():
        if column in frame.columns:
            bad = sorted(set(frame[column].dropna().astype(str)) - set(allowed))
            results.append(CheckResult(f"allowed_values:{column}", name, WARN if bad else PASS,
                                       f"Unexpected values: {bad}" if bad else "All values recognised.", bad))
    for column in spec.get("positive", []):
        if column in frame.columns:
            bad = int((pd.to_numeric(frame[column], errors="coerce") <= 0).sum())
            results.append(CheckResult(f"positive:{column}", name, WARN if bad else PASS, f"{bad} non-positive values", bad))
    for start, end in spec.get("date_order", []):
        if start in frame.columns and end in frame.columns:
            s, e = _dates(frame[start]), _dates(frame[end])
            bad = int((s.notna() & e.notna() & (e < s)).sum())
            results.append(CheckResult(f"date_order:{start}<={end}", name, WARN if bad else PASS,
                                       f"{bad} rows with {end} before {start}", bad))
    history = spec.get("history")
    if history and history["date_column"] in frame.columns:
        dates = _dates(frame[history["date_column"]]).dropna()
        weeks = 0 if dates.empty else round((dates.max() - dates.min()).days / 7, 1)
        results.append(CheckResult("history_length", name, BLOCK if weeks < history["min_weeks"] else PASS,
                                   f"{weeks} weeks from {dates.min().date() if len(dates) else None} to "
                                   f"{dates.max().date() if len(dates) else None} (need {history['min_weeks']})", weeks))
        cadence = history.get("cadence_days")
        if cadence and len(dates) > 1:
            gaps = pd.Series(sorted(dates.dt.normalize().unique())).diff().dt.days.dropna()
            irregular = int((gaps != cadence).sum())
            results.append(CheckResult("history_cadence", name, WARN if irregular else PASS,
                                       f"{irregular} intervals differ from {cadence} days", irregular))
    return results


def check_relationship(rule: dict, tables: dict[str, pd.DataFrame]) -> CheckResult:
    child_table, child_col = rule["child"].split(".")
    parent_table, parent_col = rule["parent"].split(".")
    label = f"{rule['child']} -> {rule['parent']}"
    child, parent = tables.get(child_table), tables.get(parent_table)
    if child is None or parent is None or child_col not in child or parent_col not in parent:
        return CheckResult(f"relationship:{label}", child_table, BLOCK, "Relationship cannot be checked: table or column missing.")
    values = child[child_col].dropna()
    orphans = int((~values.isin(set(parent[parent_col]))).sum())
    status = PASS if orphans == 0 else rule["on_orphan"]
    return CheckResult(f"relationship:{label}", child_table, status,
                       f"{orphans} of {len(values)} non-null references have no parent", orphans)


def bom_depth(tables: dict[str, pd.DataFrame]) -> int:
    headers, components = tables["bom_headers"], tables["bom_components"]
    parent_of_bom = headers.set_index("bom_id")["parent_item_id"]
    edges = components.assign(parent=components["bom_id"].map(parent_of_bom)).dropna(subset=["parent"])
    children: dict[int, set[int]] = {}
    for parent, child in zip(edges["parent"].astype(int), edges["component_item_id"].astype(int)):
        children.setdefault(parent, set()).add(child)
    memo: dict[int, int] = {}

    def depth(item: int, stack: frozenset) -> int:
        if item in memo:
            return memo[item]
        kids = [c for c in children.get(item, ()) if c not in stack]
        value = 1 + max((depth(c, stack | {item}) for c in kids), default=0)
        memo[item] = value
        return value

    roots = set(children) - {c for kids in children.values() for c in kids}
    return max((depth(r, frozenset()) for r in roots or children), default=0)


def run_checks(tables: dict[str, pd.DataFrame], contract: dict) -> dict:
    results: list[CheckResult] = []
    for spec in contract["datasets"]:
        results += check_dataset(spec, tables.get(spec["table"]))
    results += [check_relationship(rule, tables) for rule in contract["relationships"]]
    if {"bom_headers", "bom_components"}.issubset(tables):
        levels = bom_depth(tables)
        rule = contract["bom_depth"]
        ok = rule["min_levels"] <= levels <= rule["max_levels"]
        results.append(CheckResult("bom_depth", "bom_components", PASS if ok else WARN,
                                   f"Deepest BOM has {levels} levels (requested {rule['min_levels']}–{rule['max_levels']})",
                                   levels))
    if {"customer_forecasts"}.issubset(tables):
        forecasts = tables["customer_forecasts"]
        bucket = ["customer_id", "item_id", "site_id", "delivery_period_start"]
        if set(bucket + ["forecast_version_id"]).issubset(forecasts.columns):
            revisions = forecasts.groupby(bucket, dropna=False)["forecast_version_id"].nunique()
            median = float(revisions.median()) if len(revisions) else 0.0
            need = contract["forecast_revisions"]["min_median_revisions_per_bucket"]
            results.append(CheckResult("forecast_revision_depth", "customer_forecasts", PASS if median >= need else BLOCK,
                                       f"Median {median} revisions per customer/item/site/week bucket (need {need})",
                                       median))
    counts = {status: sum(r.status == status for r in results) for status in (PASS, WARN, BLOCK)}
    overall = BLOCK if counts[BLOCK] else WARN if counts[WARN] else PASS
    return {"contract_version": contract["contract_version"], "overall": overall, "counts": counts,
            "results": [asdict(r) for r in results]}


def tables_from_csv(directory: str | Path, contract: dict) -> dict[str, pd.DataFrame]:
    tables = {}
    for spec in contract["datasets"]:
        path = Path(directory) / f"{spec['table']}.csv"
        if path.exists():
            tables[spec["table"]] = pd.read_csv(path)
    return tables


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", choices=["sql", "csv"], default="sql")
    parser.add_argument("--path", default="staging")
    parser.add_argument("--contract", default=str(DEFAULT_CONTRACT))
    parser.add_argument("--out")
    args = parser.parse_args()
    contract = load_contract(args.contract)
    if args.source == "csv":
        tables = tables_from_csv(args.path, contract)
    else:
        from app.analytics.data_access import load_all_tables
        from app.db.connection import get_engine
        tables = load_all_tables(get_engine())
    report = run_checks(tables, contract)
    if args.out:
        Path(args.out).write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(f"Overall: {report['overall']}  {report['counts']}")
    for r in report["results"]:
        if r["status"] != PASS:
            print(f"  {r['status']:5s} {r['dataset']:28s} {r['check']:52s} {r['detail']}")
    if report["overall"] == BLOCK:
        sys.exit(1)   # automation must stop on a blocking gap: exit status 1 (WARN and PASS exit 0)


if __name__ == "__main__":
    main()
