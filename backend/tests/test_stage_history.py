import datetime as dt

import pandas as pd
import pytest

from app.analytics import stage_history as sh
from app.analytics.blocking import BlockingIndex
from app.analytics.data_access import FINDING_COLUMNS, finding_record
from app.dq.engine import Finding


def _op(op_id, po, seq, wc, start, finish, status):
    return {"po_operation_id": op_id, "production_order_id": po, "seq_no": seq, "work_centre_id": wc,
            "routing_operation_id": op_id, "actual_start": start, "actual_finish": finish, "status": status}


@pytest.fixture
def fixture_ops():
    d = pd.Timestamp
    return pd.DataFrame([
        _op(1, 10, 1, 9, d("2026-05-04"), d("2026-05-06"), "COMPLETED"),   # 48 h
        _op(2, 10, 2, 9, d("2026-05-05"), d("2026-05-07"), "COMPLETED"),   # 48 h, overlaps op 1
        _op(3, 10, 3, 5, d("2026-05-09"), d("2026-05-12"), "COMPLETED"),   # 72 h, gap 48 h after op 2
        _op(4, 11, 1, 9, None, d("2026-05-06"), "COMPLETED"),              # missing start
        _op(5, 11, 2, 9, d("2026-05-06"), None, "COMPLETED"),              # missing finish
        _op(6, 11, 3, 9, d("2026-05-08"), d("2026-05-07"), "COMPLETED"),   # finish < start
        _op(7, 12, 1, 9, d("2026-05-11"), None, "PENDING"),                # open, started
        _op(8, 12, 2, 9, None, None, "PENDING"),                           # not started
        _op(9, 12, 3, 9, d("2026-05-11"), d("2026-05-12"), "PENDING"),     # finish without completion
        _op(10, 10, 3, 5, d("2026-05-09"), d("2026-05-12"), "COMPLETED"),  # duplicate (po 10, seq 3)
        _op(11, 13, 1, 9, d("2026-05-04"), d("2026-05-08"), "COMPLETED"),  # order DQ-blocked
        _op(12, 14, 1, 9, d("2026-05-13"), d("2026-05-13"), "COMPLETED"),  # zero elapsed at day resolution
        _op(13, 15, 1, 9, d("2026-01-05"), d("2026-01-09"), "COMPLETED"),  # outside window
    ])


@pytest.fixture
def fixture_orders():
    return pd.DataFrame({"production_order_id": [10, 11, 12, 13, 14, 15],
                         "item_id": [73, 73, 82, 73, 73, 73], "site_id": [1] * 6})


@pytest.fixture
def blocking():
    index = BlockingIndex()
    index.entity_blocked[("production_order", "13")] = ["production order has no valid routing"]
    return index


@pytest.fixture
def classified(fixture_ops, fixture_orders, blocking):
    window = sh.ObservationWindow(start=dt.date(2026, 4, 1), end=dt.date(2026, 6, 1))
    return sh.classify_operations(fixture_ops, fixture_orders, blocking, window)


def test_every_operation_gets_exactly_one_record_class(classified):
    by_id = dict(zip(classified["po_operation_id"], classified["record_class"]))
    assert by_id == {1: sh.COMPLETED_VALID, 2: sh.COMPLETED_VALID, 3: sh.COMPLETED_VALID,
                     4: sh.COMPLETED_MISSING_START, 5: sh.COMPLETED_MISSING_FINISH,
                     6: sh.INVALID_ORDERING, 7: sh.OPEN_STARTED, 8: sh.NOT_STARTED,
                     9: sh.STATUS_CONFLICT, 10: sh.DUPLICATE, 11: sh.DQ_EXCLUDED,
                     12: sh.COMPLETED_VALID, 13: sh.OUTSIDE_WINDOW}
    assert sum(sh.record_class_counts(classified).values()) == len(classified)


def test_unfinished_and_invalid_operations_never_become_zero_duration(classified):
    not_valid = classified.loc[classified["record_class"] != sh.COMPLETED_VALID]
    assert not_valid["elapsed_hours"].isna().all()


def test_elapsed_summary_by_work_centre(classified):
    stats = sh.summarize_elapsed(classified, ["work_centre_id"]).set_index("work_centre_id")
    wc9 = stats.loc[9]
    # valid wc-9 observations: 48, 48, 0 → median 48
    assert wc9["n_observations"] == 3
    assert wc9["p50_hours"] == pytest.approx(48.0)
    assert wc9["n_zero_elapsed"] == 1
    # eligible = completed or finished, excluding outside-window: ops 1,2,4,5,6,9,11,12
    assert wc9["n_eligible"] == 8
    assert wc9["coverage_pct"] == pytest.approx(37.5)
    wc5 = stats.loc[5]
    assert wc5["n_observations"] == 1 and wc5["p95_hours"] == pytest.approx(72.0)


def test_open_operations_do_not_count_in_coverage_population(classified):
    stats = sh.summarize_elapsed(classified, ["item_id"]).set_index("item_id")
    # item 82 = order 12: open (7), not started (8), finish-without-completion (9) → only 9 eligible
    assert stats.loc[82, "n_eligible"] == 1
    assert stats.loc[82, "n_observations"] == 0
    assert stats.loc[82, "coverage_pct"] == 0.0


def test_overlap_is_reported_not_clamped(classified):
    gaps = sh.inter_operation_gaps(classified).set_index("from_po_operation_id")
    assert gaps.loc[1, "gap_hours"] == pytest.approx(-24.0)
    assert gaps.loc[1, "gap_class"] == "OVERLAP"
    assert gaps.loc[2, "gap_hours"] == pytest.approx(48.0)
    assert gaps.loc[2, "gap_class"] == "NON_NEGATIVE_GAP"


def test_window_excludes_but_reports_out_of_window_completions(classified):
    counts = sh.record_class_counts(classified)
    assert counts[sh.OUTSIDE_WINDOW] == 1
    stats = sh.summarize_elapsed(classified, ["site_id"])
    assert int(stats["n_observations"].sum()) == 4


def test_timestamp_resolution_detects_day_granularity(fixture_ops):
    assert sh.timestamp_resolution_hours(fixture_ops) == 24.0
    fine = fixture_ops.copy()
    fine.loc[0, "actual_finish"] = pd.Timestamp("2026-05-06 13:00")
    assert sh.timestamp_resolution_hours(fine) == 1.0


def test_persisted_findings_keep_blocking_scope():
    finding = Finding(rule_id="invalid_date_ordering", entity="production_orders", record_id="13",
                      severity="HIGH", classification="BLOCKING", origin="ORGANIC", description="x",
                      impact_scope="ENTITY_BLOCKING", affected_entity_type="production_order",
                      affected_entity_id="13")
    record = finding_record(finding)
    assert set(FINDING_COLUMNS) == set(record)
    assert (record["impact_scope"], record["affected_entity_type"], record["affected_entity_id"]) == \
        ("ENTITY_BLOCKING", "production_order", "13")


def test_standard_processing_hours_is_nan_not_zero_when_routing_standard_missing(fixture_orders):
    classified = pd.DataFrame({"routing_operation_id": [1, 2], "production_order_id": [10, 10]})
    routing = pd.DataFrame({"routing_operation_id": [1, 2], "setup_time_minutes": [30.0, None],
                            "run_time_minutes_per_unit": [6.0, 6.0], "yield_pct": [1.0, 1.0], "batch_size": [10, 10]})
    orders = fixture_orders.assign(qty=[25.0, 1, 1, 1, 1, 1])
    hours = sh.standard_processing_hours(classified, routing, orders)
    # 30 min × ceil(25/10)=3 batches + 6 min × 25 = 240 min = 4 h
    assert hours.iloc[0] == pytest.approx(4.0)
    assert pd.isna(hours.iloc[1])


def test_wip_ageing_uses_latest_snapshot_and_flags_future_entry():
    wip = pd.DataFrame({"wip_id": [1, 2, 3, 4], "work_centre_id": [9, 9, 5, 9], "qty": [10, 5, 3, 7],
                        "stage_entered_at": ["2026-05-20", "2026-05-30", "2026-06-03", "2026-05-01"],
                        "snapshot_date": ["2026-06-01", "2026-06-01", "2026-06-01", "2026-05-25"]})
    snapshot, aged = sh.wip_ageing(wip)
    assert snapshot == dt.date(2026, 6, 1)
    assert list(aged["wip_id"]) == [1, 2, 3]
    assert list(aged["age_days"].iloc[:2]) == [12.0, 2.0]
    assert aged.iloc[2]["age_class"] == "INVALID_AGE" and pd.isna(aged.iloc[2]["age_days"])
