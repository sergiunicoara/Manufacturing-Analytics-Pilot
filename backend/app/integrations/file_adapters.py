"""SIMULATED file adapters implementing the integration protocols. No external system is contacted."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import uuid
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

    Two files cannot be replaced in one atomic step, so an export keeps the last verified pair as a fallback:
      1. the new CSV and sidecar are written to unique temporary files (the sidecar records the CSV's SHA-256);
      2. if the live pair verifies, it is copied to `<name>.previous.csv` / `<name>.previous.meta.json`
         (again through temporary files) while the live pair is still intact;
      3. only then are the live files replaced.
    At every moment at least one complete, verified pair exists (live, or previous during step 3), except during
    the very first export of a name, when there is nothing to fall back to. Readers use `latest_valid(name)`."""

    def __init__(self, directory: str | Path):
        self.directory = Path(directory)

    def _paths(self, name: str, previous: bool = False) -> tuple[Path, Path]:
        stem = f"{name}.previous" if previous else name
        return self.directory / f"{stem}.csv", self.directory / f"{stem}.meta.json"

    @staticmethod
    def _pair_is_valid(csv_path: Path, meta_path: Path) -> bool:
        if not csv_path.exists() or not meta_path.exists():
            return False
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return False
        return isinstance(meta, dict) and meta.get("csv_sha256") == hashlib.sha256(csv_path.read_bytes()).hexdigest()

    @staticmethod
    def _replace_pair(sources: tuple[Path, Path], targets: tuple[Path, Path]) -> None:
        os.replace(sources[0], targets[0])
        os.replace(sources[1], targets[1])

    def export(self, name: str, frame: pd.DataFrame, metadata: dict) -> str:
        problems = []
        if frame.empty:
            problems.append("refusing to export an empty frame")
        problems += [f"metadata missing '{key}'" for key in BI_REQUIRED_METADATA if key not in metadata]
        if problems:
            raise AdapterError(problems)
        self.directory.mkdir(parents=True, exist_ok=True)
        live, previous = self._paths(name), self._paths(name, previous=True)
        # Unique per export, so two concurrent exports of the same name never share or delete each other's files.
        token = uuid.uuid4().hex
        new_tmp = tuple(path.with_name(f"{path.name}.{token}.tmp") for path in live)
        old_tmp = tuple(path.with_name(f"{path.name}.{token}.tmp") for path in previous)
        try:
            frame.to_csv(new_tmp[0], index=False)
            digest = hashlib.sha256(new_tmp[0].read_bytes()).hexdigest()
            new_tmp[1].write_text(json.dumps({**metadata, "csv_sha256": digest}, indent=2, default=str),
                                  encoding="utf-8")
            if self._pair_is_valid(*live):
                shutil.copyfile(live[0], old_tmp[0])
                shutil.copyfile(live[1], old_tmp[1])
                self._replace_pair(old_tmp, previous)       # live is untouched until this pair is complete
            self._replace_pair(new_tmp, live)               # if interrupted here, `previous` is the valid pair
        finally:
            for leftover in new_tmp + old_tmp:
                leftover.unlink(missing_ok=True)
        return str(live[0])

    def verify(self, name: str) -> bool:
        """True only when both live files exist and the sidecar describes exactly this CSV."""
        return self._pair_is_valid(*self._paths(name))

    def latest_valid(self, name: str) -> tuple[Path, Path] | None:
        """The live pair if it verifies, otherwise the previous verified pair, otherwise None."""
        for pair in (self._paths(name), self._paths(name, previous=True)):
            if self._pair_is_valid(*pair):
                return pair
        return None


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
