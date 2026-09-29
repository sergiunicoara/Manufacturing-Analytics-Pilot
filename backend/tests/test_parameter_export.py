import datetime as dt
import json

import pytest
from pydantic import ValidationError

from app.analytics import parameter_export as px
from app.analytics.policy import PolicyRecommendation

GENERATED = dt.datetime(2026, 9, 29, 12, 0, tzinfo=dt.timezone.utc)


@pytest.fixture
def package():
    policies = [PolicyRecommendation(1, "CAB-100-A", "MAKE_TO_STOCK", "LOW", 3.0, 4.0, 10.0, 0.9, 0.2, None,
                                     ["Own route 4.0 d > customer tolerance 3.0 d."], [], []),
                PolicyRecommendation(2, "CAB-200-B", "INSUFFICIENT_EVIDENCE", "NONE", None, None, None, None, None,
                                     None, [], ["Fewer than 5 sales order lines."], [])]
    buffers = [{"item_code": "CAB-100-WELDED-FRAME", "recommended_min": 195.97, "recommended_max": 270.73,
                "confidence": "LOW", "reason": "Feeds work centre 9."}]
    candidates = [{"item_code": "CAB-100-WELDED-FRAME", "reasons": ["Common to 3 finished goods."]}]
    return px.build_package(policies, buffers, candidates, "PLANT-01", dt.date(2026, 6, 1), 42,
                            "DEMAND_SHOCK_ONLY", GENERATED)


def test_package_rows_and_no_write_back(package):
    assert package.erp_write_back is False
    assert [r.parameter for r in package.rows] == ["planning_policy", "planning_policy", "analytical_buffer_min_qty",
                                                  "analytical_buffer_max_qty", "decoupling_candidate"]
    assert all(r.mapping_status == "UNMAPPED" and r.m3_field is None for r in package.rows)
    assert package.rows[1].blockers == ["Fewer than 5 sales order lines."]
    assert package.package_id == "planning-parameters-demand_shock_only-20260929T120000Z"


def test_json_round_trip_is_lossless(package):
    assert px.from_json(px.to_json(package)) == package


def test_csv_round_trip_is_lossless(package):
    assert px.rows_from_csv(px.to_csv(package)) == package.rows


def test_schema_validation_rejects_bad_rows(package):
    data = json.loads(px.to_json(package))
    data["rows"][0]["parameter"] = "safety_stock_m3"
    with pytest.raises(ValidationError):
        px.ParameterPackage.model_validate(data)
    data = json.loads(px.to_json(package))
    data["erp_write_back"] = True
    with pytest.raises(ValidationError):
        px.ParameterPackage.model_validate(data)
    data = json.loads(px.to_json(package))
    data["rows"][0]["unexpected"] = 1
    with pytest.raises(ValidationError):
        px.ParameterPackage.model_validate(data)


def test_published_json_schema_matches_the_model():
    schema = json.loads(px.json_schema())
    assert schema["properties"]["schema_version"]["const"] == px.PACKAGE_SCHEMA_VERSION
    assert "rows" in schema["required"]
