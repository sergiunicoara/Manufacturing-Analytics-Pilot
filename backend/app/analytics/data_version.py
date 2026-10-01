"""Identity of the inputs behind a persisted run: source data content + analytical code.

A seed and parameter match alone must not reuse a stored result computed from different inputs, so
scenario_store.ensure_run also requires the same data version. Older runs stay in scenario_runs as
audit history; they are never rewritten.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import pandas as pd

AUDIT_COLUMNS = frozenset({"created_at", "updated_at"})
# Analytical constants read from app.config by the analytics/dq code; infrastructure settings are excluded.
ANALYTICAL_SETTINGS = ("forecast_consumption_window_weeks", "utilization_threshold_backlog_switch",
                       "max_bounded_queue_time_days", "candidate_constraint_min_consecutive_periods",
                       "past_due_receipt_max_days", "reproducibility_relative_tolerance", "synthetic_seed")
CODE_PACKAGES = ("analytics", "dq")


def table_fingerprint(frame: pd.DataFrame) -> str:
    """Content hash independent of row order and load timestamps: rows are hashed individually and the
    row hashes are sorted, so ties in any column cannot make the result depend on SQL return order."""
    columns = sorted(c for c in frame.columns if c not in AUDIT_COLUMNS)
    digest = hashlib.sha256(",".join(columns).encode())
    row_hashes = pd.util.hash_pandas_object(frame[columns].astype(str), index=False).to_numpy()
    row_hashes.sort()
    digest.update(row_hashes.tobytes())
    return digest.hexdigest()


def source_data_version(tables: dict[str, pd.DataFrame], table_names: list[str]) -> str:
    digest = hashlib.sha256()
    for name in table_names:
        digest.update(name.encode())
        digest.update(table_fingerprint(tables[name]).encode() if name in tables else b"<missing>")
    return digest.hexdigest()[:16]


def code_version(root: Path | None = None) -> str:
    root = root or Path(__file__).resolve().parents[1]
    digest = hashlib.sha256()
    for package in CODE_PACKAGES:
        for path in sorted((root / package).glob("*.py")):
            digest.update(path.name.encode())
            digest.update(path.read_bytes().replace(b"\r\n", b"\n"))
    return digest.hexdigest()[:16]


def settings_version(source=None) -> str:
    from app.config import settings
    source = source or settings
    digest = hashlib.sha256(",".join(f"{name}={getattr(source, name)!r}" for name in ANALYTICAL_SETTINGS).encode())
    return digest.hexdigest()[:16]


def data_version(tables: dict[str, pd.DataFrame], table_names: list[str]) -> str:
    return f"data:{source_data_version(tables, table_names)}|code:{code_version()}|cfg:{settings_version()}"
