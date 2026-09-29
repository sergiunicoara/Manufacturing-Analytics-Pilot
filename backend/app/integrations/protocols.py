"""SIMULATED — not a real integration. Contracts for the three system boundaries."""
from __future__ import annotations

from typing import Protocol, runtime_checkable

import pandas as pd


class AdapterError(ValueError):
    """A payload that fails validation; carries every problem found, not just the first."""

    def __init__(self, problems: list[str]):
        super().__init__("; ".join(problems))
        self.problems = problems


@runtime_checkable
class ERPAdapter(Protocol):
    """Read-only extract from the ERP into canonical tables."""

    def extract(self) -> dict[str, pd.DataFrame]: ...


@runtime_checkable
class BIExportAdapter(Protocol):
    """One-way export of results, with provenance, to a BI tool (Qlik Cloud / Bright Analytics)."""

    def export(self, name: str, frame: pd.DataFrame, metadata: dict) -> str: ...


@runtime_checkable
class MESAdapter(Protocol):
    """Read-only shop-floor operation events at the MES boundary."""

    def read_operation_events(self) -> pd.DataFrame: ...
