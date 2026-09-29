import datetime as dt

import numpy as np
import pandas as pd
import pytest

from app.synthetic import execution_history as eh

MON = dt.datetime(2026, 5, 4, 6, 0)   # a Monday, shift start


def test_single_shift_work_spills_to_next_day_and_skips_the_weekend():
    cal = eh.WorkCalendar(shifts_per_day=1, hours_per_shift=8, days_per_week=5)
    assert cal.advance(MON, 8) == dt.datetime(2026, 5, 4, 14, 0)
    assert cal.advance(MON, 10) == dt.datetime(2026, 5, 5, 8, 0)
    friday_noon = dt.datetime(2026, 5, 8, 12, 0)
    assert cal.advance(friday_noon, 4) == dt.datetime(2026, 5, 11, 8, 0)   # 2 h Fri + 2 h Mon
    assert cal.next_working(dt.datetime(2026, 5, 9, 10, 0)) == dt.datetime(2026, 5, 11, 6, 0)


def test_three_shift_window_runs_past_midnight():
    cal = eh.WorkCalendar(shifts_per_day=3, hours_per_shift=8, days_per_week=5)
    night = dt.datetime(2026, 5, 5, 3, 0)          # inside Monday's 06:00→06:00 window
    assert cal.next_working(night) == night
    assert cal.advance(night, 2) == dt.datetime(2026, 5, 5, 5, 0)
    saturday_early = dt.datetime(2026, 5, 9, 3, 0)  # still inside Friday's window
    assert cal.advance(saturday_early, 4) == dt.datetime(2026, 5, 11, 7, 0)


def _ops(setup=30.0, run=6.0, wc=9):
    return pd.DataFrame({"routing_operation_id": [1, 2], "seq_no": [10, 20], "work_centre_id": [wc, wc],
                         "setup_time_minutes": [setup, 12.0], "run_time_minutes_per_unit": [run, 3.0],
                         "yield_pct": [1.0, 1.0], "batch_size": [10, 10], "transfer_time_minutes": [30.0, None]})


def test_simulated_order_is_sequential_minute_resolution_and_standard_based():
    cal = {9: eh.WorkCalendar(2, 8, 5)}
    times = eh.simulate_order(_ops(), 25.0, MON, cal, np.random.default_rng(1))
    assert len(times) == 2
    for begin, end in times:
        assert begin.second == 0 and end.second == 0 and end > begin
    assert times[1][0] >= times[0][0]
    work = eh.standard_work_hours(_ops().iloc[0], 25.0)
    assert work == pytest.approx((30 * 3 + 6 * 25) / 60)


def test_missing_standard_or_work_centre_gives_none_not_a_guess():
    cal = {9: eh.WorkCalendar(1, 8, 5)}
    assert eh.simulate_order(_ops(setup=None), 25.0, MON, cal, np.random.default_rng(1)) is None
    assert eh.simulate_order(_ops(wc=None), 25.0, MON, cal, np.random.default_rng(1)) is None


def test_calibration_changes_only_execution_tables(tmp_path, monkeypatch):
    from app.analytics.data_version import table_fingerprint
    from app.synthetic import run_generator
    calibrated = run_generator.generate(str(tmp_path / "a")).tables
    monkeypatch.setattr(run_generator.execution_history, "generate_all", lambda ctx: {})
    original = run_generator.generate(str(tmp_path / "b")).tables
    changed = [t for t in run_generator.TABLE_ORDER
               if table_fingerprint(calibrated[t]) != table_fingerprint(original[t])]
    assert changed == ["production_orders", "production_order_operations"]
    added = calibrated["production_orders"].iloc[len(original["production_orders"]):]
    assert len(added) > 0 and set(added["status"]) == {"COMPLETED"}
    assert (pd.to_datetime(added["actual_finish"]).dt.date < dt.date(2026, 6, 1)).all()
    kept_cols = ["production_order_id", "order_number", "item_id", "qty", "status", "planned_start"]
    head = calibrated["production_orders"].iloc[:len(original["production_orders"])]
    pd.testing.assert_frame_equal(head[kept_cols].reset_index(drop=True), original["production_orders"][kept_cols])


def test_items_with_complete_routings_have_enough_completed_history(generated_ctx):
    t = generated_ctx.tables
    ops = t["routing_operations"]
    row_complete = ops[["setup_time_minutes", "run_time_minutes_per_unit", "work_centre_id"]].notna().all(axis=1)
    complete = row_complete.groupby(ops["routing_id"]).all()
    routing_by_item = t["routing_headers"].set_index("item_id")["routing_id"]
    items = t["items"].loc[t["items"]["item_type"].isin(["FG", "SUBASSY"]), "item_id"]
    eligible = [i for i in items if i in routing_by_item.index and complete.get(routing_by_item[i], False)]
    done = t["production_orders"].loc[t["production_orders"]["status"] == "COMPLETED"].groupby("item_id").size()
    share = np.mean([done.get(i, 0) >= 3 for i in eligible])
    assert share > 0.95
