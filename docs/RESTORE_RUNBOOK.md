# Backup inspection and restore runbook

Tool: [`backend/app/db/restore.py`](../backend/app/db/restore.py). Run it inside the API container, which has the ODBC driver:

```bash
docker exec -w /app mfg_pilot_api python -m app.db.restore <command> ...
```

## Safety rules (enforced in code, covered by `tests/test_restore_safety.py`)

- **Database names:** only `^[A-Za-z][A-Za-z0-9_]{0,63}$`.
- **Backup paths:** absolute `.bak` paths under `/var/opt/mssql/data` or `/var/opt/mssql/backup`, containing only `[A-Za-z0-9_./-]` and no `..`. Nothing else is interpolated into T-SQL. An injection attempt such as `x';DROP DATABASE y;--.bak` is refused before any SQL runs.
- **No overwrites:** restoring onto an existing database is refused. `WITH REPLACE` and `DROP` never appear in any statement. To restore again, choose a new target name.
- **Copy-only backups:** backups are `COPY_ONLY, CHECKSUM`, so they never break an existing backup chain.
- **VERIFYONLY is not enough:** it only proves the media is readable and the checksums are valid. `roundtrip` restores and then compares tables, row counts, views and foreign keys.

## Receiving a client backup

1. Copy the `.bak` into the SQL Server volume under `/var/opt/mssql/backup/` on the approved host (see [SECURITY.md](SECURITY.md)).
2. Inspect it. This runs HEADERONLY, FILELISTONLY and VERIFYONLY:
   ```bash
   docker exec -w /app mfg_pilot_api python -m app.db.restore inspect --path /var/opt/mssql/backup/client_extract.bak
   ```
3. Restore into a new name. Each logical file is moved to `/var/opt/mssql/data/<target>*`:
   ```bash
   docker exec -w /app mfg_pilot_api python -m app.db.restore restore --path /var/opt/mssql/backup/client_extract.bak --target client_extract_20261001
   ```
4. Check completeness against the data request. Point the API at the restored database with `MSSQL_DATABASE`, then:
   ```bash
   docker exec -w /app mfg_pilot_api python -m app.integration.completeness --source sql --out /tmp/completeness.json
   ```
5. Apply the pilot's read models. Migrations only create what is missing, or `CREATE OR ALTER` views:
   ```bash
   docker exec -w /app mfg_pilot_api python -m app.db.migrate
   ```

## Demonstrated round trip (local synthetic stack, 2026-09-29)

```bash
docker exec -w /app mfg_pilot_api python -m app.db.restore roundtrip --database mfg_analytics_pilot --target pilot_restore_check_20260929
```

| Check | Result |
|---|---|
| Copy-only backup | `/var/opt/mssql/data/pilot_restore_check_20260929.bak` |
| RESTORE with MOVE | data and log files moved to `pilot_restore_check_20260929.mdf` / `_log.ldf` |
| Tables compared | 30 (identical set) |
| Rows compared | 87,205, with no table mismatched |
| Views | 6 = 6 |
| Foreign keys | 40 = 40 |
| Re-running into `mfg_analytics_pilot` | refused: "already exists; refusing to overwrite" |

The disposable database `pilot_restore_check_20260929` and its `.bak` were left in place as evidence. They hold the same synthetic data as the source. Deleting them is a separate, manual decision; this tool never deletes anything.

## Not demonstrated here

- A restore of a real client M3 extract. None is available.
- A backup from a different SQL Server version, or with encryption (TDE or backup certificates). Both need the source's certificate and a compatibility check.
- Restore on the client-approved EU host. That is an infrastructure approval (see [SECURITY.md](SECURITY.md)).
