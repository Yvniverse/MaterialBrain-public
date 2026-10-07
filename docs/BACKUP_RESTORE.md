# Backup and restore

Back up the database and managed files from the same installation. Default storage is `./storage`; `MATERIALBRAIN_STORAGE_ROOT` can select another installation-local directory. Keep the Compose project, database name, and storage root explicit when invoking operational tools.

## Backups

The backup service creates a PostgreSQL custom dump and separate attachment/evidence archives. A manifest records filenames, timestamps, and SHA-256 hashes. It runs once at startup and then uses `BACKUP_SCHEDULE`; `BACKUP_RETENTION_DAYS` controls retention of matching backup files.

Start or request a backup in the default public project:

```bash
docker compose -p materialbrain_public_v02 up -d backup
docker compose -p materialbrain_public_v02 exec -T backup /usr/local/bin/backup.sh
```

The default output is `storage/backups/`. Robot topic/event records, generated datasets, and model outputs have their own storage paths; include them separately when needed for reproducibility.

Copy backups to another device or off-host location. Verify the manifest hashes and confirm the database archive can be listed with `pg_restore --list`. A successful archive creation alone does not verify restoration.

## Restore into a separate runtime

Create an independent Compose project with a new database volume, storage directory, and browser port. Use that runtime to restore and inspect a backup before changing an active installation.

The scripts under `deploy/restore/` restore a database dump plus same-timestamp attachment and evidence archives. They stop the target backend, create a protective backup, restore the dump, and restart the backend. Read their path requirements and set the target project/environment explicitly before use.

Check these behaviors after restoration:

- Alembic version and PostGIS extension;
- login and role permissions;
- material, inventory, reservation, and recent movement records;
- warehouse/spatial map identity and revision;
- managed attachment and evidence retrieval;
- creation and verification of a new backup.

Do not start robot execution during a recovery check. Persistent application mission history and a simulator's in-memory mission ledger have different lifetimes; inspect their state before starting a new task.

## Recovery planning

Document the desired recovery point, off-host copy location, credentials required for restoration, and the person responsible for the operation. Test recovery after storage or schema changes. See [Security](SECURITY.md) and [Migration policy](MIGRATION_POLICY.md).
