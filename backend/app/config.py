"""Central configuration and analytical constants.

Every "magic number" that affects a calculation (not just infrastructure
wiring) lives here, named, with a comment on where it's used — so
ANALYTICS_METHODS.md and the Evidence Drawer can cite a single source for
"why 4 weeks", "why 0.95", etc.
"""
from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    # --- Infrastructure ---
    mssql_host: str = os.environ.get("MSSQL_HOST", "localhost")
    mssql_port: int = int(os.environ.get("MSSQL_PORT", "1433"))
    mssql_database: str = os.environ.get("MSSQL_DATABASE", "mfg_analytics_pilot")
    mssql_user: str = os.environ.get("MSSQL_USER", "sa")
    mssql_password: str = os.environ.get("MSSQL_SA_PASSWORD", "DevOnly_ChangeMe_123!")
    anthropic_api_key: str | None = os.environ.get("ANTHROPIC_API_KEY") or None
    anthropic_model: str = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-5")

    # --- Synthetic data generation ---
    synthetic_seed: int = int(os.environ.get("SYNTHETIC_DATA_SEED", "42"))

    # Target scale (Flag #8 in PLAN.md — low end of the spec's ranges, kept
    # laptop-friendly while still populated-looking).
    n_customers: int = 60
    n_suppliers: int = 15
    n_work_centres: int = 16
    history_weeks: int = 39          # ~9 months of history
    forecast_snapshot_weeks: int = 30  # weekly forecast revisions, >= 26 required
    future_horizon_weeks: int = 26   # planning horizon for scenario/period engine

    # --- Analytical constants (used from CP2 onward; declared now so the
    # generator and the engine agree on the same horizon/config) ---

    # [CORR-3] Forecast consumption window: an order line may consume a
    # forecast row whose delivery bucket is within this many weeks of the
    # order's requested delivery date. Documented in ASSUMPTIONS.md.
    forecast_consumption_window_weeks: int = 4

    # [CORR-1] Utilization threshold above which the queue-time model
    # switches from the bounded delay curve to explicit backlog accumulation.
    utilization_threshold_backlog_switch: float = 0.95

    # Cap on the bounded delay curve below the threshold, in days, to avoid
    # an unrealistic (though not infinite) queue estimate as utilization
    # approaches the threshold from below.
    max_bounded_queue_time_days: float = 10.0

    # [CORR-6] Number of consecutive overloaded periods required before an
    # OVERLOADED work centre is reclassified as a CANDIDATE_CONSTRAINT.
    candidate_constraint_min_consecutive_periods: int = 3

    # Reproducibility tolerance for computed floating-point outputs [CORR-11].
    reproducibility_relative_tolerance: float = 1e-9

    @property
    def mssql_odbc_url(self) -> str:
        driver = "ODBC Driver 18 for SQL Server"
        return (
            f"mssql+pyodbc://{self.mssql_user}:{self.mssql_password}"
            f"@{self.mssql_host}:{self.mssql_port}/{self.mssql_database}"
            f"?driver={driver.replace(' ', '+')}&TrustServerCertificate=yes"
        )

    @property
    def mssql_odbc_url_master(self) -> str:
        """Connection to the `master` DB, used only to run the DDL bootstrap
        (which itself creates the target database)."""
        driver = "ODBC Driver 18 for SQL Server"
        return (
            f"mssql+pyodbc://{self.mssql_user}:{self.mssql_password}"
            f"@{self.mssql_host}:{self.mssql_port}/master"
            f"?driver={driver.replace(' ', '+')}&TrustServerCertificate=yes"
        )


settings = Settings()
