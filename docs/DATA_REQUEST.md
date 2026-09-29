# Data request for the manufacturing analytics pilot

This is the request we would send to the client's ERP director before the pilot starts. It uses the pilot's **canonical** table and column names. The corresponding Infor M3 tables and fields are listed separately in [M3_MAPPING.md](M3_MAPPING.md). All of those are unverified hypotheses until someone checks them against the client's actual M3 schema.

The executable version of this request is [`backend/app/integration/data_request_contract.json`](../backend/app/integration/data_request_contract.json). The checker runs every rule below and returns PASS, WARN or BLOCK:

```bash
docker exec -w /app mfg_pilot_api python -m app.integration.completeness --source sql --out /tmp/completeness.json
```

```bash
docker exec -w /app mfg_pilot_api python -m app.integration.completeness --source csv --path staging
```

Row counts alone do not establish completeness. The checker also covers:
- required and optional columns
- primary and business-key uniqueness
- null limits
- allowed codes
- positive quantities
- date ordering
- history length and weekly cadence
- 13 foreign-key relationships
- BOM depth
- forecast revision depth per customer/item/site/week bucket

It reports gaps and never fills them in. On the local synthetic database (2026-09-29) the result was **WARN: 89 pass, 5 warn, 0 block**. All five warnings are defects the generator planted on purpose.

## Delivery format

- **Preferred:** a SQL Server backup (`.bak`) of the agreed extract database. Restoring it follows [RESTORE_RUNBOOK.md](RESTORE_RUNBOOK.md).
- **Alternative:** one UTF-8 CSV file per table, named `<table>.csv`, with a header row.
- **Scope:** one plant (site), all customers and items active in the history window.
- **Access:** read-only. Nothing is written back to the ERP.
- **Timestamps:** plant-local time. Please tell us the time zone, and whether values are stored with or without daylight-saving adjustment.
- **Quantities:** in the item's base unit of measure. Please list any alternative UoM conversions that are used.

## Datasets

| Dataset | Canonical table | Required | Key / business key | History requested | Why the pilot needs it |
|---|---|---|---|---|---|
| Sites | `sites` | yes | `site_id` / `site_code` | current | Scope every other record |
| Customers | `customers` | yes | `customer_id` / `customer_code` | current | Forecast consumption is scoped by customer |
| Item master | `items` | yes | `item_id` / `item_code` | current, with inactive items | Item type, UoM, product family |
| BOM headers | `bom_headers` | yes | `bom_id` | every revision with effective dates | Effective-dated explosion, 3–9 levels |
| BOM components | `bom_components` | yes | `bom_component_id` | every revision | Quantity per and scrap % |
| Routings | `routing_headers` | yes | `routing_id` | every revision with effective dates | Route selection by date |
| Routing operations | `routing_operations` | yes | `routing_operation_id` | every revision | Setup, run, queue and transfer times; yield; batch size; work centre |
| Work centres | `work_centres` | yes | `work_centre_id` / `work_centre_code` | current | Shifts, hours, days, hourly rate |
| Capacity calendar | `capacity_calendar` | yes | `work_centre_id` + `week_start_date` | history + 26 weeks ahead | Calendar, downtime and availability tiers |
| Forecast versions | `forecast_versions` | yes | `forecast_version_id` | **≥ 26 weekly snapshots** | Revision history and accuracy per horizon |
| Customer forecasts | `customer_forecasts` | yes | `forecast_id` | every snapshot | Quantity per customer × item × site × delivery week |
| Sales orders | `sales_orders` | yes | `sales_order_id` / `order_number` | ≥ 26 weeks | Actual demand, forecast consumption, customer tolerance |
| Sales order lines | `sales_order_lines` | yes | `line_id` | ≥ 26 weeks | Requested and promised ship dates |
| Production orders | `production_orders` | yes | `production_order_id` / `order_number` | ≥ 26 weeks, all statuses | Planned and actual dates, routing used |
| Production order operations | `production_order_operations` | yes | `po_operation_id` | ≥ 26 weeks | Actual start and finish per operation (recorded stage times) |
| Inventory | `inventory` | yes | `inventory_id` | weekly snapshots | Usable stock for netting |
| Purchase order lines | `purchase_order_lines` | no | `po_line_id` | open + 26 weeks | Scheduled receipts |
| Standard costs | `standard_costs` | no | `standard_cost_id` | every effective revision | Unit cost, buffer capital, carrying cost |
| WIP snapshots | `wip` | no | `wip_id` | weekly snapshots | WIP ageing |

When an optional dataset is missing, the checker returns WARN and the analyses that depend on it report "unavailable". They are never computed on invented values.

## Units, dates and codes we need confirmed

- **Routing times:** setup and run times are in minutes, and run time is per base unit. If the source uses hours or time per batch, please say which.
- **Yield and scrap:** stored as fractions or percentages?
- **Capacity:** are hours per shift gross or net of breaks? Is the downtime field planned only, or planned and unplanned?
- **Costs:** currency, the valuation date, and whether material cost is rolled up through the BOM or single-level. Is overhead stored as money or as a rate?
- **Order status:** the full list of status codes and what each means, especially the codes for completed, closed and cancelled.

## Questions for the ERP director

1. Which M3 program or table is the system of record for each dataset above, and who owns it?
2. Is forecast history kept as dated snapshots? If not, can weekly exports be scheduled from now on so revision history builds up during the pilot?
3. Are operation actual start and finish reported by the MES and written back to M3? At what resolution: timestamp, date or shift?
4. How are BOM and routing revisions made effective (date or status)? Can overlapping revisions occur?
5. Which work centres share operators or equipment? Weekly rough-cut capacity treats them as independent.
6. Is there a customer-agreed order-to-delivery lead time per product family? We currently infer it from order and requested dates.
7. Which site, warehouse and customer identifiers must be pseudonymised before the data leaves the client environment?
8. What refresh cadence does the pilot need (weekly is the default), and is there a cut-off time for late transactions?
9. Can we have a read-only SQL login on a restored copy, rather than on production?
10. Where must the restored database live (EU region, client tenant or our tenant), and who approves that?

## Owners

| Topic | Owner (to be confirmed with the client) |
|---|---|
| Extract definition and delivery | Client ERP team, led by the ERP director |
| Hosting and access approval | Client IT and security |
| Validation and gap classification | Pilot data engineer |
| Business meaning of codes and statuses | Planning and production leads |
