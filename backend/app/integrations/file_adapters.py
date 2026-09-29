"""SIMULATED file adapters implementing the integration protocols. No external system is contacted."""
from __future__ import annotations

import json
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
    """Writes a CSV plus a sidecar JSON with provenance, so the BI tool never receives a bare number."""

    def __init__(self, directory: str | Path):
        self.directory = Path(directory)

    def export(self, name: str, frame: pd.DataFrame, metadata: dict) -> str:
        problems = []
        if frame.empty:
            problems.append("refusing to export an empty frame")
        problems += [f"metadata missing '{key}'" for key in BI_REQUIRED_METADATA if key not in metadata]
        if problems:
            raise AdapterError(problems)
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self.directory / f"{name}.csv"
        frame.to_csv(path, index=False)
        (self.directory / f"{name}.meta.json").write_text(json.dumps(metadata, indent=2, default=str),
                                                         encoding="utf-8")
        return str(path)


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
