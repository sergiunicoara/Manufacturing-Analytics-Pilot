# Backup inspection and restore runbook

Tool: [`backend/app/db/restore.py`](../backend/app/db/restore.py). Run it inside the API container, which has the ODBC driver:

```bash
docker exec -w /app mfg_pilot_api python -m app.db.restore <command> ...
```

## Safety rules (enforced in code, covered by `tests/test_restore_safety.py`)

- **Database names:** only `^[A-Za-z][A-Za-z0-9_]{0,63}$`.
- **Backup paths:** absolute `.bak` paths under `/var/opt/mssql/data` or `/var/opt/mssql/backup`, containing only `[A-Za-z0-9_./-]` and no `..`. Nothing else is interpolated into T-SQL. An injection attempt such as `x';DROP DATABASE y;--.bak` is refused before any SQL runs.
- **No overwrites:** restoring onto an existing database is refused. `WITH REPLACE` and `DROP` never appear in any statement. To restore again, choose a new target name.
- **Copy-only backups to a new file only:** backups are `COPY_ONLY, NOINIT, CHECKSUM`, so they never break an existing backup chain. If the target `.bak` path already exists (for example a client backup) the backup is refused, so an existing file is never overwritten or appended to.
- **One physical file per backup file:** restored files are named by `FileId` (`<target>.mdf` for the primary data file, `<target>_<id>.ndf` for other data files, `<target>_log<id>.ldf` for logs), so multiple log or data files cannot collide. Full-text or FILESTREAM files are refused with a message to restore them manually.
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

## Demonstrated round trip (local synthetic stack, 2026-09-30, freshly reloaded database)

```bash
docker exec -w /app mfg_pilot_api python -m app.db.restore roundtrip --database mfg_analytics_pilot --target pilot_restore_check_20260930
```

| Check | Result |
|---|---|
| Copy-only backup | `/var/opt/mssql/data/pilot_restore_check_20260930.bak` |
| RESTORE with MOVE | data and log files moved to `pilot_restore_check_20260930.mdf` / `_log.ldf` (naming before the FileId scheme; see the safety rules) |
| Tables compared | 30 (identical set) |
| Rows compared | 74,844, with no table mismatched |
| Views | 6 = 6 |
| Foreign keys | 40 = 40 |
| Restoring onto an existing database | refused ("already exists; refusing to overwrite"), also covered by `tests/test_restore_safety.py` |

The 2026-09-29 round trip gave the same result (30 tables, 87,205 rows) on the previous database, which was lost when Docker Desktop was reset.

**Cleanup.** The disposable database and its `.bak` were removed by hand after validation; this tool never deletes anything. To remove another one (a one-off, manual step):

```bash
docker exec -i -w /app mfg_pilot_api python - <<'EOF'
import sqlalchemy as sa
from app.config import settings
from app.db.connection import get_engine
name = "pilot_restore_check_YYYYMMDD"      # must be a disposable name you created
with get_engine(settings.mssql_odbc_url_master).connect().execution_options(isolation_level="AUTOCOMMIT") as c:
    c.execute(sa.text(f"DROP DATABASE [{name}]"))
EOF
docker exec mfg_pilot_sqlserver rm -f /var/opt/mssql/data/<name>.bak
```

## Not demonstrated here

- A restore of a real client M3 extract. None is available.
- A backup from a different SQL Server version, or with encryption (TDE or backup certificates). Both need the source's certificate and a compatibility check.
- Restore on the client-approved EU host. That is an infrastructure approval (see [SECURITY.md](SECURITY.md)).
