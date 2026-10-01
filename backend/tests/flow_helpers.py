"""Hand-built plants for the flow simulator tests: small enough to check every number by hand."""
from __future__ import annotations

from app.analytics.flow.model import FlowParameters, FlowPlant, OpTemplate, WorkCentre

FG, SUB, STEEL = 1, 2, 3


def wc(wc_id, process, cost=60.0, hours=8.0, availability=1.0):
    return WorkCentre(wc_id, f"{process}-{wc_id}", process, hours, cost, availability)


def op(wc_id, name="op", setup=0.0, run=0.0, batch=1000.0, transfer=0.0, yield_pct=1.0):
    return OpTemplate(wc_id, name, setup, run, yield_pct, batch, transfer)


def plant(work_centres, routings, bom=None, stock=None, receipts=None, demand=None, cost=None):
    return FlowPlant(
        work_centres={w.wc_id: w for w in work_centres},
        item_codes={FG: "FG", SUB: "SUB", STEEL: "STEEL"}, item_families={FG: "F", SUB: "F", STEEL: "F"},
        routings=routings, bom=bom or {}, stock=stock or {}, receipts=receipts or {},
        material_cost=cost or {STEEL: 10.0}, demand=demand or [(0, FG, 10.0)])


def params(**overrides):
    base = dict(lead_in_weeks=1, horizon_weeks=2, level_offset_days=1.0, max_lot_qty=1000.0, colours=(("RED", 1.0),),
                transfer_between_ops=False, defect_rate_default=0.0)
    base.update(overrides)
    return FlowParameters(**base)
