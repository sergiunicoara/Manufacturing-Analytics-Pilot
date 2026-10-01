"""Central configuration and analytical constants.

Every "magic number" that affects a calculation (not just infrastructure
wiring) lives here, named, with a comment on where it's used — so
ANALYTICS_METHODS.md and the Evidence Drawer can cite a single source for
"why 4 weeks", "why 0.95", etc.
"""
from __future__ import annotations

import os
from urllib.parse import quote_plus
from dataclasses import dataclass


DEMO_DEFAULT_PASSWORD = "DevOnly_ChangeMe_123!"


@dataclass(frozen=True)
class Settings:
    # --- Infrastructure ---
    mssql_host: str = os.environ.get("MSSQL_HOST", "localhost")
    mssql_port: int = int(os.environ.get("MSSQL_PORT", "1433"))
    mssql_database: str = os.environ.get("MSSQL_DATABASE", "mfg_analytics_pilot")
    # APP_PROFILE=demo (local synthetic demo, permissive) or secured (see SECURITY.md and
    # app/security.py: explicit credentials, API keys, least-privilege SQL login, LLM opt-in).
    app_profile: str = os.environ.get("APP_PROFILE", "demo")
    mssql_user: str = os.environ.get("MSSQL_USER", "sa")
    # The dev default applies only to the demo profile; security.validate_settings rejects it when secured.
    mssql_password: str = (os.environ.get("MSSQL_PASSWORD") or os.environ.get("MSSQL_SA_PASSWORD")
                           or (DEMO_DEFAULT_PASSWORD if os.environ.get("APP_PROFILE", "demo") == "demo" else ""))
    anthropic_api_key: str | None = os.environ.get("ANTHROPIC_API_KEY") or None
    anthropic_model: str = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-5")
    allow_external_llm: bool = os.environ.get("ALLOW_EXTERNAL_LLM", "").lower() == "true"
    reader_api_keys: tuple[str, ...] = tuple(k for k in os.environ.get("PILOT_READER_API_KEYS", "").split(",") if k)
    operator_api_keys: tuple[str, ...] = tuple(k for k in os.environ.get("PILOT_OPERATOR_API_KEYS", "").split(",") if k)
    allowed_origins: tuple[str, ...] = tuple(o for o in os.environ.get("ALLOWED_ORIGINS", "").split(",") if o)

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

    # Open purchase-order lines whose expected date is at most this many days before the first modelled
    # week are treated as past due and available in week 1; older lines are excluded (and listed on the
    # engine output), because a long-overdue open line is more likely stale data than supply.
    past_due_receipt_max_days: int = 28

    # Reproducibility tolerance for computed floating-point outputs [CORR-11].
    reproducibility_relative_tolerance: float = 1e-9

    def odbc_url(self, user: str, password: str, database: str) -> str:
        """The one place the SQL Server connection URL (driver, TLS options) is built."""
        driver = "ODBC Driver 18 for SQL Server"
        return (
            f"mssql+pyodbc://{quote_plus(user)}:{quote_plus(password)}"
            f"@{self.mssql_host}:{self.mssql_port}/{database}"
            f"?driver={driver.replace(' ', '+')}&TrustServerCertificate=yes"
        )

    @property
    def mssql_odbc_url(self) -> str:
        return self.odbc_url(self.mssql_user, self.mssql_password, self.mssql_database)

    @property
    def mssql_odbc_url_master(self) -> str:
        """Connection to the `master` DB, used only to run the DDL bootstrap
        (which itself creates the target database)."""
        return self.odbc_url(self.mssql_user, self.mssql_password, "master")


settings = Settings()
