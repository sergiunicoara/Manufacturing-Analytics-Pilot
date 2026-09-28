"""Sites, warehouses, customers, suppliers, work centres, and the shared pool
of raw/purchased items (RAW + PACKAGING item_type). Finished-goods and
subassembly items are generated in bom_and_routing.py alongside their BOM
trees, since each variant's subassembly items are created as part of
building that variant's BOM.
"""
from __future__ import annotations

import pandas as pd

from app.config import settings
from app.synthetic.context import EUROPEAN_COUNTRIES, GenContext, VALID_UOM

# Work centre process types, in a plausible flow order (used later by the
# BOM/routing generator to build realistic operation sequences, and by the
# analytics engine's routing-sequence tie-break for PRIMARY_CONSTRAINT).
PROCESS_TYPES_IN_FLOW_ORDER = [
    "LASER_CUTTING",
    "PUNCHING",
    "DEBURRING",
    "BENDING",
    "WELDING",
    "GRINDING",
    "POWDER_COATING",
    "PAINTING",
    "ASSEMBLY",
    "ELECTRICAL_ASSEMBLY",
    "INSPECTION",
    "PACKAGING",
]

# Work centre codes generated (>=1 per process type, 2 for the ones that are
# realistically higher-volume / most likely to bottleneck).
_WC_MULTIPLICITY = {
    "LASER_CUTTING": 2,
    "PUNCHING": 1,
    "DEBURRING": 1,
    "BENDING": 2,
    "WELDING": 2,
    "GRINDING": 1,
    "POWDER_COATING": 1,
    "PAINTING": 1,
    "ASSEMBLY": 2,
    "ELECTRICAL_ASSEMBLY": 1,
    "INSPECTION": 1,
    "PACKAGING": 1,
}


def generate_sites(ctx: GenContext) -> pd.DataFrame:
    seq = ctx.id_seq("sites")
    df = pd.DataFrame([
        {"site_id": seq.next(), "site_code": "SITE-NL01", "name": "Tilburg Plant", "country": "NL"},
    ])
    ctx.add_table("sites", df)
    return df


def generate_warehouses(ctx: GenContext) -> pd.DataFrame:
    seq = ctx.id_seq("warehouses")
    site_id = ctx.tables["sites"].iloc[0]["site_id"]
    rows = [
        {"warehouse_code": "WH-RAW", "name": "Raw Materials Warehouse", "warehouse_type": "RAW"},
        {"warehouse_code": "WH-WIP", "name": "WIP Staging Warehouse", "warehouse_type": "WIP"},
        {"warehouse_code": "WH-FG", "name": "Finished Goods Warehouse", "warehouse_type": "FG"},
    ]
    for r in rows:
        r["warehouse_id"] = seq.next()
        r["site_id"] = site_id
    df = pd.DataFrame(rows)
    ctx.add_table("warehouses", df)
    return df


def generate_customers(ctx: GenContext) -> pd.DataFrame:
    seq = ctx.id_seq("customers")
    n = settings.n_customers
    rows = []
    for i in range(n):
        country = ctx.rng.choice(EUROPEAN_COUNTRIES)
        credit_status = ctx.rng.choice(["OK", "OK", "OK", "OK", "WATCH", "HOLD"])
        rows.append({
            "customer_id": seq.next(),
            "customer_code": f"CUST-{i + 1:04d}",
            "name": ctx.faker.company(),
            "country": country,
            "region": "Europe",
            "credit_status": credit_status,
        })
    df = pd.DataFrame(rows)
    ctx.add_table("customers", df)
    return df


def generate_suppliers(ctx: GenContext) -> pd.DataFrame:
    seq = ctx.id_seq("suppliers")
    n = settings.n_suppliers
    rows = []
    for i in range(n):
        country = ctx.rng.choice(EUROPEAN_COUNTRIES)
        rows.append({
            "supplier_id": seq.next(),
            "supplier_code": f"SUPL-{i + 1:03d}",
            "name": ctx.faker.company() + " " + ctx.rng.choice(["Steel", "Metals", "Components", "Hardware", "Coatings"]),
            "country": country,
            "default_lead_time_days": int(ctx.rng.integers(5, 45)),
        })
    df = pd.DataFrame(rows)
    ctx.add_table("suppliers", df)
    return df


def generate_work_centres(ctx: GenContext) -> pd.DataFrame:
    seq = ctx.id_seq("work_centres")
    site_id = ctx.tables["sites"].iloc[0]["site_id"]
    rows = []
    counters: dict[str, int] = {}
    cost_by_process = {
        "LASER_CUTTING": 85, "PUNCHING": 70, "DEBURRING": 45, "BENDING": 65,
        "WELDING": 75, "GRINDING": 50, "POWDER_COATING": 60, "PAINTING": 65,
        "ASSEMBLY": 40, "ELECTRICAL_ASSEMBLY": 55, "INSPECTION": 40, "PACKAGING": 35,
    }
    for process in PROCESS_TYPES_IN_FLOW_ORDER:
        mult = _WC_MULTIPLICITY[process]
        for _ in range(mult):
            counters[process] = counters.get(process, 0) + 1
            idx = counters[process]
            code_short = {
                "LASER_CUTTING": "LASER", "PUNCHING": "PUNCH", "DEBURRING": "DEBURR",
                "BENDING": "BEND", "WELDING": "WELD", "GRINDING": "GRIND",
                "POWDER_COATING": "PWDCOAT", "PAINTING": "PAINT", "ASSEMBLY": "ASSY",
                "ELECTRICAL_ASSEMBLY": "ELECASSY", "INSPECTION": "INSPECT", "PACKAGING": "PACK",
            }[process]
            rows.append({
                "work_centre_id": seq.next(),
                "work_centre_code": f"WC-{code_short}-{idx:02d}",
                "name": f"{process.replace('_', ' ').title()} {idx}",
                "site_id": site_id,
                "process_type": process,
                "shifts_per_day": int(ctx.rng.choice([1, 2, 2, 3])),
                "hours_per_shift": 8.0,
                "days_per_week": 5,
                "cost_per_hour": float(cost_by_process[process] + ctx.rng.integers(-5, 5)),
            })
    df = pd.DataFrame(rows)
    ctx.add_table("work_centres", df)
    return df


# --- Shared pool of raw / purchased items -----------------------------

RAW_MATERIAL_CATALOG: list[tuple[str, str, str]] = [
    # (item_code, description, uom)
    ("STEEL-SHEET-1.5MM", "Cold-rolled steel sheet 1.5mm", "KG"),
    ("STEEL-SHEET-2MM", "Cold-rolled steel sheet 2mm", "KG"),
    ("STEEL-SHEET-3MM", "Cold-rolled steel sheet 3mm", "KG"),
    ("STEEL-SHEET-4MM", "Cold-rolled steel sheet 4mm", "KG"),
    ("ALU-SHEET-2MM", "Aluminium sheet 2mm", "KG"),
    ("BRACKET-A", "Mounting bracket type A", "PC"),
    ("BRACKET-B", "Mounting bracket type B", "PC"),
    ("BRACKET-C", "Mounting bracket type C", "PC"),
    ("BRACKET-D", "Mounting bracket type D", "PC"),
    ("WELD-NUT-M4", "Weld nut M4", "PC"),
    ("WELD-NUT-M5", "Weld nut M5", "PC"),
    ("WELD-NUT-M6", "Weld nut M6", "PC"),
    ("WELD-NUT-M8", "Weld nut M8", "PC"),
    ("HINGE-A", "Concealed hinge type A", "PC"),
    ("HINGE-B", "Concealed hinge type B (heavy duty)", "PC"),
    ("HINGE-C", "Piano hinge, cut-to-length", "PC"),
    ("LOCK-A", "Cam lock standard", "PC"),
    ("LOCK-B", "Cam lock with key", "PC"),
    ("LOCK-C", "3-point locking handle", "PC"),
    ("GASKET-IP54", "Foam gasket, IP54 rated", "M"),
    ("GASKET-IP65", "EPDM gasket, IP65 rated", "M"),
    ("CABLE-GLAND-M16", "Cable gland M16", "PC"),
    ("CABLE-GLAND-M25", "Cable gland M25", "PC"),
    ("DIN-RAIL-TS35", "DIN rail TS35, cut-to-length", "M"),
    ("TERMINAL-BLOCK-STD", "Terminal block, standard", "PC"),
    ("PCB-CONTROLLER-BASIC", "Control PCB, basic I/O", "PC"),
    ("PCB-CONTROLLER-ADV", "Control PCB, advanced I/O", "PC"),
    ("WIRING-HARNESS-S", "Wiring harness, small", "PC"),
    ("WIRING-HARNESS-M", "Wiring harness, medium", "PC"),
    ("WIRING-HARNESS-L", "Wiring harness, large", "PC"),
    ("FAN-UNIT-STD", "Cooling fan unit, standard", "PC"),
    ("FILTER-VENT-STD", "Filtered vent panel", "PC"),
    ("SCREW-KIT-M3", "Screw/washer kit M3 (pack)", "SET"),
    ("SCREW-KIT-M4", "Screw/washer kit M4 (pack)", "SET"),
    ("SCREW-KIT-M6", "Screw/washer kit M6 (pack)", "SET"),
    ("PRIMER-EPOXY", "Epoxy primer", "L"),
    ("PAINT-WET-RAL", "Wet paint, RAL colour (per litre)", "L"),
    ("POWDER-PAINT-RAL", "Powder coat paint, RAL colour (per kg)", "KG"),
    ("LABEL-KIT-STD", "Nameplate / label kit", "SET"),
    ("FOAM-PACKAGING-S", "Foam packaging insert, small", "PC"),
    ("FOAM-PACKAGING-M", "Foam packaging insert, medium", "PC"),
    ("FOAM-PACKAGING-L", "Foam packaging insert, large", "PC"),
    ("CARDBOARD-BOX-S", "Cardboard shipping box, small", "PC"),
    ("CARDBOARD-BOX-M", "Cardboard shipping box, medium", "PC"),
    ("CARDBOARD-BOX-L", "Cardboard shipping box, large", "PC"),
    ("PALLET-EURO", "Euro pallet", "PC"),
    ("STRETCH-WRAP", "Stretch wrap film", "M"),
    ("FASTENER-KIT-STD", "Fastener kit, standard assembly", "SET"),
    ("EARTH-STRAP-STD", "Earthing strap", "PC"),
    ("BUSBAR-STD", "Copper busbar, standard", "PC"),
    ("VENTILATION-GRILLE", "Ventilation grille", "PC"),
]


def generate_raw_material_items(ctx: GenContext) -> pd.DataFrame:
    seq = ctx.id_seq("items")
    rows = []
    for code, desc, uom in RAW_MATERIAL_CATALOG:
        item_type = "PACKAGING" if any(
            k in code for k in ("PACKAGING", "CARDBOARD", "PALLET", "WRAP", "LABEL", "FOAM")
        ) else "RAW"
        rows.append({
            "item_id": seq.next(),
            "item_code": code,
            "description": desc,
            "item_type": item_type,
            "uom": uom,
            "product_family": None,
            "is_active": True,
        })
    df = pd.DataFrame(rows)
    # Merge into the shared items table (created fresh here; bom_and_routing
    # will append FG/SUBASSY rows to this same table object).
    ctx.add_table("items", df)
    return df


def generate_all(ctx: GenContext) -> None:
    generate_sites(ctx)
    generate_warehouses(ctx)
    generate_customers(ctx)
    generate_suppliers(ctx)
    generate_work_centres(ctx)
    generate_raw_material_items(ctx)
