"""Product families, variants, multi-level BOMs (3-9 levels), and routings.

Each of the 5 product families is defined by a template controlling how many
intermediate fabrication levels sit between the finished good and raw steel
(`frame_chain_depth`), which optional branches it has (door, electrical kit,
mounting plate, canopy), and its coating process. Variants within a family
are systematic width x height combinations, which is how real sheet-metal
catalogs are usually structured.

BOM depth achieved: BOX-500 ~= 3 levels, CAB-100 ~= 6, CAB-200 ~= 9 (deepest,
per the spec's "3 to 9 levels" requirement).
"""
from __future__ import annotations

import pandas as pd

from app.synthetic.context import GenContext, RAL_POWDER_COAT_COLORS
from app.synthetic.master_data import PROCESS_TYPES_IN_FLOW_ORDER

EFFECTIVE_FROM = pd.Timestamp("2024-01-01").date()

# Fabrication chain stage templates: (role_suffix, [process_types])
CHAIN_STAGES = [
    ("CUT-BLANK", ["LASER_CUTTING"]),
    ("DEBURRED-BLANK", ["DEBURRING"]),
    ("WELDED-FRAME", ["BENDING", "WELDING"]),
    ("SEALED-FRAME", ["GRINDING"]),
    ("COATED-FRAME", []),  # coating process filled in per-family (powder vs wet paint)
]

FAMILY_SPECS = [
    {
        "prefix": "CAB-100", "name": "Industrial Control Cabinet",
        "frame_chain_depth": 3, "has_door": True, "has_electrical_kit": True,
        "has_mounting_plate": True, "has_canopy": False, "coating": "POWDER_COATING",
        "widths": [300, 400, 500, 600], "heights": [400, 600, 800, 1000, 1200, 1600, 2000],
        "raw_gauge": "STEEL-SHEET-2MM",
    },
    {
        "prefix": "CAB-200", "name": "Outdoor Control Cabinet",
        "frame_chain_depth": 5, "has_door": True, "has_electrical_kit": True,
        "has_mounting_plate": True, "has_canopy": True, "coating": "PAINTING",
        "widths": [300, 400, 500, 600], "heights": [400, 600, 800, 1000, 1200, 1600, 2000],
        "raw_gauge": "STEEL-SHEET-3MM",
    },
    {
        "prefix": "ENC-300", "name": "Electrical Enclosure",
        "frame_chain_depth": 2, "has_door": True, "has_electrical_kit": False,
        "has_mounting_plate": True, "has_canopy": False, "coating": "POWDER_COATING",
        "widths": [300, 400, 500, 600], "heights": [400, 600, 800, 1000, 1200, 1600, 2000],
        "raw_gauge": "STEEL-SHEET-1.5MM",
    },
    {
        "prefix": "RACK-400", "name": "Industrial Equipment Rack",
        "frame_chain_depth": 1, "has_door": False, "has_electrical_kit": False,
        "has_mounting_plate": True, "has_canopy": False, "coating": "POWDER_COATING",
        "widths": [300, 400, 500, 600], "heights": [400, 600, 800, 1000, 1200, 1600, 2000],
        "raw_gauge": "STEEL-SHEET-2MM",
    },
    {
        "prefix": "BOX-500", "name": "Small Junction Box",
        "frame_chain_depth": 0, "has_door": False, "has_electrical_kit": False,
        "has_mounting_plate": False, "has_canopy": False, "coating": "POWDER_COATING",
        "widths": [100, 150, 200, 250], "heights": [100, 150, 200, 300, 400, 500, 600],
        "raw_gauge": "STEEL-SHEET-1.5MM",
    },
]

PROCESS_TIME_DEFAULTS = {
    "LASER_CUTTING": {"setup": (20, 40), "run": (0.8, 2.5), "batch": (10, 30)},
    "PUNCHING": {"setup": (15, 30), "run": (0.5, 1.5), "batch": (10, 30)},
    "DEBURRING": {"setup": (5, 15), "run": (0.3, 1.0), "batch": (10, 30)},
    "BENDING": {"setup": (20, 45), "run": (1.0, 3.0), "batch": (5, 20)},
    # [CP3.1 calibration] run time raised and batch size lowered relative to
    # the other fabrication steps so welding carries the least headroom at
    # baseline capacity (see master_data.py's _SHIFT_WEIGHTS_BY_PROCESS
    # comment) -- a deliberate per-process time/batch assumption, not a
    # capacity-side special case, and still just one input the constraint
    # classifier has to discover on its own merits every run.
    "WELDING": {"setup": (40, 70), "run": (26.0, 38.0), "batch": (2, 4)},
    "GRINDING": {"setup": (10, 20), "run": (0.5, 1.5), "batch": (5, 20)},
    "POWDER_COATING": {"setup": (30, 50), "run": (2.0, 4.0), "batch": (20, 50)},
    "PAINTING": {"setup": (25, 45), "run": (2.5, 5.0), "batch": (10, 30)},
    "ASSEMBLY": {"setup": (15, 30), "run": (5.0, 12.0), "batch": (1, 10)},
    "ELECTRICAL_ASSEMBLY": {"setup": (10, 20), "run": (8.0, 18.0), "batch": (1, 10)},
    "INSPECTION": {"setup": (5, 10), "run": (2.0, 5.0), "batch": (1, 10)},
    "PACKAGING": {"setup": (5, 10), "run": (1.5, 3.5), "batch": (1, 10)},
}


class BomRoutingBuilder:
    def __init__(self, ctx: GenContext) -> None:
        self.ctx = ctx
        self.item_seq = ctx.id_seq("items")
        self.bom_header_seq = ctx.id_seq("bom_headers")
        self.bom_component_seq = ctx.id_seq("bom_components")
        self.routing_header_seq = ctx.id_seq("routing_headers")
        self.routing_op_seq = ctx.id_seq("routing_operations")

        self.item_rows: list[dict] = []
        self.bom_header_rows: list[dict] = []
        self.bom_component_rows: list[dict] = []
        self.routing_header_rows: list[dict] = []
        self.routing_op_rows: list[dict] = []

        self.item_code_to_id: dict[str, int] = dict(
            zip(ctx.tables["items"]["item_code"], ctx.tables["items"]["item_id"])
        )
        wc = ctx.tables["work_centres"]
        self.wc_by_process: dict[str, list[int]] = {
            p: wc.loc[wc["process_type"] == p, "work_centre_id"].tolist()
            for p in PROCESS_TYPES_IN_FLOW_ORDER
        }
        # [CP3.1 calibration] Round-robin rather than random choice: a real
        # plant load-balances parallel lines rather than randomly clustering
        # work onto one of them. Random assignment was producing lopsided
        # baseline utilization between two identical-capacity lines of the
        # same process purely by chance, which read as a data artifact
        # rather than a real constraint.
        self._wc_round_robin_index: dict[str, int] = {p: 0 for p in PROCESS_TYPES_IN_FLOW_ORDER}

    # -- helpers ---------------------------------------------------

    def _raw_id(self, code: str) -> int:
        return self.item_code_to_id[code]

    def _pick_wc(self, process_type: str) -> int:
        options = self.wc_by_process[process_type]
        idx = self._wc_round_robin_index[process_type]
        self._wc_round_robin_index[process_type] = (idx + 1) % len(options)
        return int(options[idx])

    def add_item(self, code: str, description: str, item_type: str, uom: str, family: str) -> int:
        item_id = self.item_seq.next()
        self.item_rows.append({
            "item_id": item_id, "item_code": code, "description": description,
            "item_type": item_type, "uom": uom, "product_family": family, "is_active": True,
        })
        self.item_code_to_id[code] = item_id
        return item_id

    def add_bom(self, parent_item_id: int, components: list[tuple[int, float, float]]) -> int:
        """components: list of (component_item_id, quantity_per, scrap_pct)."""
        bom_id = self.bom_header_seq.next()
        self.bom_header_rows.append({
            "bom_id": bom_id, "parent_item_id": parent_item_id, "revision": "A",
            "status": "ACTIVE", "effective_from": EFFECTIVE_FROM, "effective_to": None,
        })
        for comp_item_id, qty_per, scrap_pct in components:
            self.bom_component_rows.append({
                "bom_component_id": self.bom_component_seq.next(), "bom_id": bom_id,
                "component_item_id": comp_item_id, "quantity_per": qty_per,
                "scrap_pct": scrap_pct, "effective_from": EFFECTIVE_FROM, "effective_to": None,
            })
        return bom_id

    def add_routing(self, item_id: int, ops: list[str]) -> int:
        routing_id = self.routing_header_seq.next()
        self.routing_header_rows.append({
            "routing_id": routing_id, "item_id": item_id, "revision": "A",
            "effective_from": EFFECTIVE_FROM, "effective_to": None,
        })
        for seq_no, process_type in enumerate(ops, start=1):
            defaults = PROCESS_TIME_DEFAULTS[process_type]
            rng = self.ctx.rng
            self.routing_op_rows.append({
                "routing_operation_id": self.routing_op_seq.next(), "routing_id": routing_id,
                "seq_no": seq_no, "work_centre_id": self._pick_wc(process_type),
                "operation_name": process_type.replace("_", " ").title(),
                "setup_time_minutes": float(rng.uniform(*defaults["setup"])),
                "run_time_minutes_per_unit": float(rng.uniform(*defaults["run"])),
                "queue_time_minutes": float(rng.uniform(60, 240)),
                "transfer_time_minutes": float(rng.uniform(10, 30)),
                "yield_pct": float(rng.uniform(0.96, 0.998)),
                "batch_size": int(rng.integers(*defaults["batch"])),
            })
        return routing_id

    # -- family/variant construction --------------------------------

    def build_frame_chain(self, spec: dict, prefix: str) -> int:
        """Returns the item_id of the topmost frame-chain item (what the FG
        BOM references). If frame_chain_depth == 0, returns the raw material
        item id directly (no intermediate items)."""
        depth = spec["frame_chain_depth"]
        raw_id = self._raw_id(spec["raw_gauge"])
        if depth == 0:
            return raw_id

        stages = CHAIN_STAGES[:depth]
        prev_item_id = raw_id
        prev_qty_source_is_raw = True
        for i, (role, ops) in enumerate(stages):
            code = f"{prefix}-{role}"
            item_id = self.add_item(code, f"{role.replace('-', ' ').title()} for {prefix}", "SUBASSY", "PC", spec["prefix"])
            if role == "COATED-FRAME":
                coat_ops = [spec["coating"]]
                self.add_routing(item_id, coat_ops + ["INSPECTION"])
                components = [(prev_item_id, 1.0, 0.02)]
            else:
                self.add_routing(item_id, ops)
                if prev_qty_source_is_raw:
                    # raw steel consumption in kg per blank, not 1:1
                    components = [(prev_item_id, float(self.ctx.rng.uniform(1.5, 6.0)), 0.05)]
                else:
                    components = [(prev_item_id, 1.0, 0.02)]
            # extra hardware at the welding stage
            if role == "WELDED-FRAME":
                components.append((self._raw_id("WELD-NUT-M6"), float(self.ctx.rng.integers(4, 12)), 0.01))
                components.append((self._raw_id("BRACKET-A"), float(self.ctx.rng.integers(2, 6)), 0.01))
            self.add_bom(item_id, components)
            prev_item_id = item_id
            prev_qty_source_is_raw = False
        return prev_item_id

    def build_door(self, spec: dict, prefix: str, family_code: str) -> int:
        blank_id = self.add_item(f"{prefix}-DOOR-BLANK", f"Door blank for {prefix}", "SUBASSY", "PC", family_code)
        self.add_routing(blank_id, ["LASER_CUTTING", "BENDING"])
        self.add_bom(blank_id, [(self._raw_id(spec["raw_gauge"]), float(self.ctx.rng.uniform(1.0, 3.0)), 0.05)])

        door_id = self.add_item(f"{prefix}-DOOR", f"Door assembly for {prefix}", "SUBASSY", "PC", family_code)
        self.add_routing(door_id, [spec["coating"], "INSPECTION"])
        hinge_code = "HINGE-B" if spec["prefix"] == "CAB-200" else "HINGE-A"
        lock_code = "LOCK-C" if spec["prefix"] == "CAB-200" else "LOCK-A"
        components = [
            (blank_id, 1.0, 0.02),
            (self._raw_id(hinge_code), 2.0, 0.01),
            (self._raw_id(lock_code), 1.0, 0.01),
        ]
        if spec.get("ip65") or spec["prefix"] == "CAB-200":
            components.append((self._raw_id("GASKET-IP65"), float(self.ctx.rng.uniform(1.2, 2.5)), 0.03))
        self.add_bom(door_id, components)
        return door_id

    def build_back_panel(self, spec: dict, prefix: str, family_code: str) -> int:
        bp_id = self.add_item(f"{prefix}-BACK-PANEL", f"Back panel for {prefix}", "SUBASSY", "PC", family_code)
        self.add_routing(bp_id, ["LASER_CUTTING", "BENDING", spec["coating"]])
        self.add_bom(bp_id, [
            (self._raw_id(spec["raw_gauge"]), float(self.ctx.rng.uniform(0.8, 2.5)), 0.04),
            (self._raw_id("WELD-NUT-M6"), float(self.ctx.rng.integers(4, 10)), 0.01),
        ])
        return bp_id

    def build_mounting_plate(self, spec: dict, prefix: str, family_code: str) -> int:
        mp_id = self.add_item(f"{prefix}-MOUNTING-PLATE", f"Mounting plate for {prefix}", "SUBASSY", "PC", family_code)
        self.add_routing(mp_id, ["LASER_CUTTING", "POWDER_COATING"])
        self.add_bom(mp_id, [(self._raw_id("STEEL-SHEET-2MM"), float(self.ctx.rng.uniform(0.6, 1.8)), 0.04)])
        return mp_id

    def build_canopy(self, spec: dict, prefix: str, family_code: str) -> int:
        canopy_id = self.add_item(f"{prefix}-CANOPY", f"Rain canopy for {prefix}", "SUBASSY", "PC", family_code)
        self.add_routing(canopy_id, ["LASER_CUTTING", "BENDING", "PAINTING"])
        self.add_bom(canopy_id, [(self._raw_id("STEEL-SHEET-1.5MM"), float(self.ctx.rng.uniform(1.0, 2.5)), 0.05)])
        return canopy_id

    def build_electrical_kit(self, spec: dict, prefix: str, family_code: str) -> int:
        ek_id = self.add_item(f"{prefix}-ELEC-KIT", f"Electrical kit for {prefix}", "SUBASSY", "SET", family_code)
        self.add_routing(ek_id, ["ELECTRICAL_ASSEMBLY"])
        pcb_code = "PCB-CONTROLLER-ADV" if spec["prefix"] in ("CAB-200",) else "PCB-CONTROLLER-BASIC"
        harness_code = self.ctx.rng.choice(["WIRING-HARNESS-S", "WIRING-HARNESS-M", "WIRING-HARNESS-L"])
        self.add_bom(ek_id, [
            (self._raw_id(pcb_code), 1.0, 0.01),
            (self._raw_id(str(harness_code)), 1.0, 0.01),
            (self._raw_id("TERMINAL-BLOCK-STD"), float(self.ctx.rng.integers(4, 12)), 0.0),
            (self._raw_id("DIN-RAIL-TS35"), float(self.ctx.rng.uniform(0.3, 1.0)), 0.02),
        ])
        return ek_id

    def build_variant(self, spec: dict, width: int, height: int) -> None:
        prefix = f"{spec['prefix']}-{width}x{height}"
        family_code = spec["prefix"]
        fg_id = self.add_item(prefix, f"{spec['name']} {width}x{height}mm", "FG", "PC", family_code)

        components: list[tuple[int, float, float]] = []

        frame_top_id = self.build_frame_chain(spec, prefix)
        components.append((frame_top_id, 1.0, 0.01))

        if spec["has_door"]:
            door_id = self.build_door(spec, prefix, family_code)
            n_doors = 2.0 if width >= 500 else 1.0
            components.append((door_id, n_doors, 0.0))

        if spec["frame_chain_depth"] > 0:
            bp_id = self.build_back_panel(spec, prefix, family_code)
            components.append((bp_id, 1.0, 0.0))

        if spec["has_mounting_plate"]:
            mp_id = self.build_mounting_plate(spec, prefix, family_code)
            components.append((mp_id, 1.0, 0.0))

        if spec["has_canopy"]:
            canopy_id = self.build_canopy(spec, prefix, family_code)
            components.append((canopy_id, 1.0, 0.0))

        if spec["has_electrical_kit"]:
            ek_id = self.build_electrical_kit(spec, prefix, family_code)
            components.append((ek_id, 1.0, 0.0))

        # packaging / fastener kit, direct raw/packaging BOM components
        components.append((self._raw_id("FASTENER-KIT-STD"), 1.0, 0.0))
        components.append((self._raw_id("LABEL-KIT-STD"), 1.0, 0.0))
        box_size = "S" if max(width, height) < 500 else ("M" if max(width, height) < 1000 else "L")
        components.append((self._raw_id(f"CARDBOARD-BOX-{box_size}"), 1.0, 0.0))
        components.append((self._raw_id(f"FOAM-PACKAGING-{box_size}"), 1.0, 0.0))
        if box_size == "L":
            components.append((self._raw_id("PALLET-EURO"), 1.0, 0.0))

        self.add_bom(fg_id, components)

        fg_ops = ["ASSEMBLY"]
        if spec["has_electrical_kit"]:
            fg_ops.append("ELECTRICAL_ASSEMBLY")
        fg_ops += ["INSPECTION", "PACKAGING"]
        self.add_routing(fg_id, fg_ops)

    def build_family(self, spec: dict) -> None:
        for width in spec["widths"]:
            for height in spec["heights"]:
                self.build_variant(spec, width, height)

    def finalize(self) -> None:
        new_items = pd.DataFrame(self.item_rows)
        combined_items = pd.concat([self.ctx.tables["items"], new_items], ignore_index=True)
        self.ctx.add_table("items", combined_items)
        self.ctx.add_table("bom_headers", pd.DataFrame(self.bom_header_rows))
        self.ctx.add_table("bom_components", pd.DataFrame(self.bom_component_rows))
        self.ctx.add_table("routing_headers", pd.DataFrame(self.routing_header_rows))
        self.ctx.add_table("routing_operations", pd.DataFrame(self.routing_op_rows))


def generate_all(ctx: GenContext) -> None:
    builder = BomRoutingBuilder(ctx)
    for spec in FAMILY_SPECS:
        builder.build_family(spec)
    builder.finalize()
