"""Plain data for the flow simulator: assumptions (FlowParameters), the policy under test (Policy), a hand-buildable
plant (FlowPlant) and the run record. Every number here that is not read from the plant's tables is an ASSUMPTION and
is reported as such in the evidence."""
from __future__ import annotations

from dataclasses import dataclass, field

HOURS_PER_WEEK = 168.0

COATING_PROCESSES = ("POWDER_COATING", "PAINTING")
INSPECTION = "INSPECTION"
ASSEMBLY_PROCESSES = ("ASSEMBLY", "ELECTRICAL_ASSEMBLY")

# RAL colours named in the synthetic generator (backend/app/synthetic/context.py) with an assumed order mix.
DEFAULT_COLOURS = (("RAL7035", 0.35), ("RAL7016", 0.25), ("RAL9005", 0.20), ("RAL9010", 0.12), ("RAL5010", 0.08))


@dataclass(frozen=True)
class FlowParameters:
    horizon_weeks: int = 8                 # weeks of finished-good demand simulated
    lead_in_weeks: int = 2                 # the clock starts this long before the first demand week
    demand_multiplier: float = 1.0
    demand_multiplier_families: tuple = ("CAB-100",)
    max_lot_qty: float = 60.0              # finished-good demand is released in lots no larger than this
    level_offset_days: float = 7.0         # each BOM level below the finished good is released this much earlier
    due_slack_days: float = 0.0            # a lot is late when it finishes after (demand week end + slack)
    shift_start_hour: float = 6.0
    working_days: int = 5
    changeover_minutes: float = 40.0       # colour change on a coating work centre (the user's figure)
    same_colour_setup_minutes: float = 5.0  # ASSUMED: re-load between jobs of the same colour
    colours: tuple = DEFAULT_COLOURS       # ASSUMED order mix (no colour exists in the data)
    unplanned_purchase_lead_days: float = 0.0    # ASSUMED: a purchased part with no stock or receipt is available this
    #                                              long after the job is released (0 isolates the shop floor; shortages are counted)
    transfer_between_ops: bool = True
    defect_rate_default: float = 0.02      # ASSUMED unit defect probability when the BOM line has no scrap_pct
    component_inspection_minutes: float = 0.5    # ASSUMED, per unit, at an inspection work centre
    rework_minutes: float = 15.0           # ASSUMED, per replaced unit
    reinspection_minutes: float = 1.0      # ASSUMED, per replaced unit
    max_rework_rounds: int = 3
    strand_days: float = 3.0               # overtime output waiting longer than this for its consumer is "stranded"
    overtime_premium: float = 1.5          # ASSUMED multiple of the work centre's hourly cost
    seed: int = 20261001


@dataclass(frozen=True)
class Policy:
    name: str = "Today"
    colour_window_days: float | None = None   # None: earliest due date, colour ignored
    scrap_mode: str = "final"                 # final | component | replace
    defects: bool = False                     # draw defects at all
    kit_priority: bool = False
    kit_max_missing: int = 1                  # a component is boosted when its parent lacks at most this many
    overtime: str = "none"                    # none | unconditional | gated
    overtime_work_centres: tuple = ()
    overtime_hours: float = 8.0


@dataclass
class OpTemplate:
    wc: int
    name: str
    setup_min: float
    run_min: float
    yield_pct: float
    batch: float
    transfer_min: float = 0.0
    kind: str = "ROUTING"      # ROUTING | COMP_INSPECT | REWORK | REINSPECT


@dataclass
class WorkCentre:
    wc_id: int
    name: str
    process: str
    hours_per_day: float = 8.0
    cost_per_hour: float = 0.0
    availability: float = 1.0


@dataclass
class FlowPlant:
    work_centres: dict                      # wc_id -> WorkCentre
    item_codes: dict                        # item_id -> code
    item_families: dict                     # item_id -> family
    routings: dict                          # item_id -> [OpTemplate]
    bom: dict                               # item_id -> [(component_item_id, quantity_per, scrap_pct)]
    stock: dict                             # item_id -> on-hand quantity at the start of the first demand week
    receipts: dict                          # item_id -> [(hours_from_clock_zero, qty)]
    material_cost: dict                     # purchased item_id -> standard material cost per unit
    demand: list                            # [(week_index, finished_good_item_id, qty)]
    clock_zero_offset_weeks: int = 0
    problems: list = field(default_factory=list)   # [(item_id, reason)] excluded from the simulation
    excluded_demand: float = 0.0
    skipped_operations: dict = field(default_factory=dict)   # item_id -> routing operations left out (missing data)


@dataclass
class Job:
    jid: int
    item_id: int
    qty: float
    release_h: float
    due_h: float
    colour: str | None
    level: int
    parent: int | None
    root: int
    kind: str = "PLAN"                      # PLAN | REMAKE
    children: list = field(default_factory=list)
    fixed_ready_h: float = 0.0              # latest stock/receipt/shortage availability among its non-job components
    kit_lines: list = field(default_factory=list)   # [(item_id, qty, source, available_h)]
    ops: list = field(default_factory=list)
    pending_children: int = 0
    waiting_for: str = "kit"
    kit_ready_h: float | None = None
    op_log: list = field(default_factory=list)      # [(op_index, start_h, end_h, work_h, wc, setup_h, colour_change)]
    done_h: float | None = None
    rounds: int = 0
    scrapped_units: float = 0.0
    final_inspection_index: int | None = None
    replaced: dict = field(default_factory=dict)
    rework_after: int = 0
    rework_units: float = 0.0


@dataclass
class FlowResult:
    jobs: dict
    wc_stats: dict
    fg_lots: list
    metrics: dict
    overtime: list
    events: list
    plant: FlowPlant
    params: FlowParameters
    policy: Policy
