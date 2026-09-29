# Infor M3 to canonical mapping

**Status: every M3 name below is an unverified hypothesis.** It comes from general familiarity with M3, not from vendor documentation or a client schema, and none of it may be relied on. No authoritative Infor source was consulted in this session. Field-level mappings are left blank on purpose: naming a field that hasn't been verified would be inventing it.

To verify a row, get one of two sources:
- the client's M3 data dictionary, or the schema of an actual extract
- Infor's official documentation for the client's M3 version

Then change the row's status to `VERIFIED` and cite the source in the last column.

The pilot code never reads M3 names. The canonical model (`db/ddl/001_schema.sql`, `backend/app/integration/data_request_contract.json`) is the contract.

**Status values:**
- `HYPOTHESIS`: the table name is plausible but unchecked.
- `PENDING_CLIENT`: needs the client's schema or configuration.
- `VERIFIED`: checked, with the source cited.

| Canonical table | Concept | Candidate M3 source | Business key | Join to | Unit / date conversion to confirm | Version / status field | Status | Source |
|---|---|---|---|---|---|---|---|---|
| `items` | Item master | Item master table, commonly referred to as MITMAS | item number | — | Base vs. alternative UoM | item status | HYPOTHESIS | — |
| `bom_headers`, `bom_components` | Product structure | Product structure tables, commonly referred to as MPDHED / MPDMAT | product + structure type + facility | `items` | Quantity basis and scrap % | revision, status, effective dates | HYPOTHESIS | — |
| `routing_headers`, `routing_operations` | Routing operations | Operation table, commonly referred to as MPDOPE | product + operation number | `work_centres` | Time unit (per piece, per 100, hours or minutes) | effective dates | HYPOTHESIS | — |
| `work_centres` | Work centre / resource | Work-centre table, commonly referred to as MPDWCT | facility + work centre | — | Shift and calendar source | — | HYPOTHESIS | — |
| `capacity_calendar` | Available capacity | Work-centre calendar or capacity tables | work centre + date | `work_centres` | Hours net or gross of breaks; downtime definition | — | PENDING_CLIENT | — |
| `customer_forecasts`, `forecast_versions` | Forecast snapshots | Forecast tables. **Dated snapshot history may not exist in M3**; it may have to be built by scheduled exports | customer + item + period + snapshot | `items`, `customers` | Period bucket definition | snapshot date | PENDING_CLIENT | — |
| `sales_orders`, `sales_order_lines` | Customer orders | Customer order tables, commonly referred to as OOHEAD / OOLINE | order number + line | `customers`, `items` | Requested vs. confirmed delivery date | line status | HYPOTHESIS | — |
| `production_orders`, `production_order_operations` | Manufacturing orders and operations | MO tables, commonly referred to as MWOHED / MWOOPE | MO number (+ operation) | `items`, `work_centres` | Actual timestamps: date or time, and which time zone | order and operation status | HYPOTHESIS | — |
| `inventory` | Stock balance | Item/warehouse balance, commonly referred to as MITBAL | item + warehouse | `items` | Snapshot cadence | — | HYPOTHESIS | — |
| `purchase_order_lines` | Scheduled receipts | Purchase order line table, commonly referred to as MPLINE | PO number + line | `items` | Expected receipt date | line status | HYPOTHESIS | — |
| `standard_costs` | Standard cost | Item cost tables | item + cost type + date | `items` | Currency; rolled-up vs. single-level | effective date | PENDING_CLIENT | — |
| `wip` | WIP snapshot | Derived from MO operation status; probably no single source table | MO + operation | `production_orders` | Snapshot definition | — | PENDING_CLIENT | — |

## Rules for completing this table

1. A row changes to `VERIFIED` only with a citable source: document name, version and page, or the client's dictionary export.
2. Client-specific customisation (user-defined fields, custom statuses, custom facility setup) is recorded as `PENDING_CLIENT` and never assumed.
3. Date conversion: confirm the date and time format against a real extract before writing a converter. Do not assume a format from memory.
4. A canonical column with no confirmed source stays empty. The completeness checker then reports it; it is never defaulted.
5. No mapping implies write-back. The parameter package (`/api/parameters/package.json`) marks every M3 target field `UNMAPPED` until this table is verified.
