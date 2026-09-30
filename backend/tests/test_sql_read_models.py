"""SQL/Python parity for the T-SQL read models. Requires a reachable SQL Server with the pilot
database loaded; skipped otherwise (for example on a host without the ODBC driver)."""
from pathlib import Path

import pandas as pd
import pytest

from tests.db_gate import unavailable

MIGRATIONS = Path(__file__).resolve().parents[1] / "db" / "migrations"
if not MIGRATIONS.exists():
    MIGRATIONS = Path(__file__).resolve().parents[2] / "db" / "migrations"


@pytest.fixture(scope="module")
def engine():
    try:
        import sqlalchemy as sa
        from app.db.connection import get_engine
        engine = get_engine()
        with engine.connect() as conn:
            conn.execute(sa.text("SELECT 1 FROM dbo.items WHERE 1 = 0"))
    except Exception as exc:  # noqa: BLE001 - any connectivity failure means "not available here"
        unavailable(f"SQL Server not available: {exc}")
    from app.db.migrate import apply_migrations
    apply_migrations(str(MIGRATIONS), engine)
    return engine


@pytest.fixture(scope="module")
def db_tables(engine):
    from app.analytics.data_access import load_all_tables
    return load_all_tables(engine)


def test_forecast_accuracy_view_matches_python(engine, db_tables):
    from app.analytics.forecast import compute_accuracy_by_horizon, reconstruct_forecast_history
    history = reconstruct_forecast_history(db_tables["customer_forecasts"], db_tables["forecast_versions"],
                                           db_tables["sales_order_lines"], db_tables["sales_orders"])
    python = compute_accuracy_by_horizon(history).set_index("horizon_bucket")
    sql = pd.read_sql("SELECT * FROM dbo.vw_forecast_accuracy_by_horizon ORDER BY bucket_order", engine).set_index("horizon_bucket")
    assert list(sql.index) == list(python.index)
    for bucket in python.index:
        assert sql.loc[bucket, "n_observations"] == python.loc[bucket, "n_observations"]
        assert float(sql.loc[bucket, "wape"]) == pytest.approx(python.loc[bucket, "wape"], abs=1e-5)
        assert float(sql.loc[bucket, "mae"]) == pytest.approx(python.loc[bucket, "mae"], abs=1e-5)
        assert float(sql.loc[bucket, "bias"]) == pytest.approx(python.loc[bucket, "bias"], abs=1e-5)


def test_stage_elapsed_view_matches_python_including_persisted_dq_scope(engine, db_tables):
    from app.analytics import stage_history as sh
    from app.analytics.blocking import build_blocking_index
    persisted = pd.read_sql("SELECT * FROM dbo.dq_findings", engine)
    blocking = build_blocking_index(persisted)
    classified = sh.classify_operations(db_tables["production_order_operations"], db_tables["production_orders"], blocking)
    python = sh.summarize_elapsed(classified, ["work_centre_id"]).set_index("work_centre_id")
    python = python.loc[python["n_observations"] > 0]
    sql = pd.read_sql("SELECT * FROM dbo.vw_stage_elapsed_by_work_centre", engine).set_index("work_centre_id")
    assert sorted(sql.index) == sorted(python.index)
    for wc in python.index:
        assert sql.loc[wc, "n_observations"] == python.loc[wc, "n_observations"]
        for column in ("p50_hours", "p85_hours", "p95_hours"):
            assert float(sql.loc[wc, column]) == pytest.approx(python.loc[wc, column], abs=1e-3)
    valid = pd.read_sql("SELECT COUNT(*) AS n FROM dbo.vw_stage_elapsed", engine)["n"].iloc[0]
    assert valid == sh.record_class_counts(classified)[sh.COMPLETED_VALID]


def test_weekly_load_view_preserves_persisted_results_and_nulls(engine):
    view = pd.read_sql("SELECT COUNT(*) AS n, SUM(CASE WHEN effective_hours IS NULL THEN 1 ELSE 0 END) AS nulls "
                       "FROM dbo.vw_weekly_load", engine).iloc[0]
    table = pd.read_sql("SELECT COUNT(*) AS n, SUM(CASE WHEN effective_hours IS NULL THEN 1 ELSE 0 END) AS nulls "
                        "FROM dbo.period_engine_results", engine).iloc[0]
    assert view["n"] == table["n"] and view["nulls"] == table["nulls"]


def test_migrated_database_passes_the_schema_guard(engine):
    from app.db.migrate import schema_problems
    assert schema_problems(engine) == []
