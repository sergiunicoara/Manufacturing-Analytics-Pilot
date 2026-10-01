"""SIMULATED file adapters implementing the integration protocols. No external system is contacted."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pandas as pd

from app.integrations.protocols import AdapterError

MES_COLUMNS = ("po_operation_id", "event_type", "event_time")
MES_EVENT_TYPES = frozenset({"START", "FINISH"})
BI_REQUIRED_METADATA = ("source", "data_origin", "generated_at")


class CsvERPAdapter:
    """Reads <table>.csv files from the agreed extract directory."""

    def __init__(self, directory: str | Path, required: tuple[str, ...] = ("items", "sales_orders")):
        self.directory, self.required = Path(directory), required

    def extract(self) -> dict[str, pd.DataFrame]:
        problems = [f"missing file {t}.csv" for t in self.required if not (self.directory / f"{t}.csv").exists()]
        if problems:
            raise AdapterError(problems)
        return {p.stem: pd.read_csv(p) for p in sorted(self.directory.glob("*.csv"))}


class FileBIExportAdapter:
    """Writes a CSV plus a sidecar JSON with provenance, so the BI tool never receives a bare number.

    Two files cannot be replaced in one atomic step, so the pairing is made verifiable instead: the sidecar
    records the SHA-256 of the exact CSV bytes it describes, and `verify(name)` checks it. Everything that can
    fail (writing either file, serialising the metadata) happens on temporary files before the live files are
    touched; a crash between the two final renames leaves a pair that `verify` reports as mismatched."""

    def __init__(self, directory: str | Path):
        self.directory = Path(directory)

    def _paths(self, name: str) -> tuple[Path, Path]:
        return self.directory / f"{name}.csv", self.directory / f"{name}.meta.json"

    def export(self, name: str, frame: pd.DataFrame, metadata: dict) -> str:
        problems = []
        if frame.empty:
            problems.append("refusing to export an empty frame")
        problems += [f"metadata missing '{key}'" for key in BI_REQUIRED_METADATA if key not in metadata]
        if problems:
            raise AdapterError(problems)
        self.directory.mkdir(parents=True, exist_ok=True)
        path, meta_path = self._paths(name)
        temp_csv, temp_meta = path.with_name(path.name + ".tmp"), meta_path.with_name(meta_path.name + ".tmp")
        try:
            frame.to_csv(temp_csv, index=False)
            digest = hashlib.sha256(temp_csv.read_bytes()).hexdigest()
            temp_meta.write_text(json.dumps({**metadata, "csv_sha256": digest}, indent=2, default=str),
                                 encoding="utf-8")
            os.replace(temp_csv, path)
            os.replace(temp_meta, meta_path)
        finally:
            for leftover in (temp_csv, temp_meta):
                leftover.unlink(missing_ok=True)
        return str(path)

    def verify(self, name: str) -> bool:
        """True only when both files exist and the sidecar describes exactly this CSV."""
        path, meta_path = self._paths(name)
        if not path.exists() or not meta_path.exists():
            return False
        try:
            recorded = json.loads(meta_path.read_text(encoding="utf-8")).get("csv_sha256")
        except json.JSONDecodeError:
            return False
        return recorded == hashlib.sha256(path.read_bytes()).hexdigest()


class JsonlMESAdapter:
    """Reads operation start/finish events from a JSON-lines file; rejects the file if any line is invalid."""

    def __init__(self, path: str | Path):
        self.path = Path(path)

    def read_operation_events(self) -> pd.DataFrame:
        if not self.path.exists():
            raise AdapterError([f"missing file {self.path.name}"])
        rows, problems = [], []
        for number, line in enumerate(self.path.read_text(encoding="utf-8").splitlines(), start=1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                problems.append(f"line {number}: not valid JSON")
                continue
            missing = [c for c in MES_COLUMNS if c not in record]
            if missing:
                problems.append(f"line {number}: missing {missing}")
            elif record["event_type"] not in MES_EVENT_TYPES:
                problems.append(f"line {number}: unknown event_type {record['event_type']!r}")
            elif pd.isna(pd.to_datetime(record["event_time"], errors="coerce")):
                problems.append(f"line {number}: invalid event_time")
            else:
                rows.append(record)
        if problems:
            raise AdapterError(problems)
        return pd.DataFrame(rows, columns=list(MES_COLUMNS))
