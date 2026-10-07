# Database migration policy

Released Alembic revisions are immutable. Persisted schema changes require a new revision; change application compatibility code separately when necessary.

The existing frozen migration hashes are recorded in [MIGRATION_HISTORY_SHA256.json](../backend/alembic/MIGRATION_HISTORY_SHA256.json). Validate them from the repository root:

```bash
python tools/check_migration_history.py
```

Then apply migrations to an isolated database before testing:

```bash
cd backend
python -m alembic upgrade head
```

The spatial map revision requires PostgreSQL with PostGIS. Use the provided PostGIS Compose service for application startup or the separate service in `docker-compose.test.yml` for tests.

New revisions should avoid importing evolving ORM metadata where it can alter historical behavior. Review indexes, constraints, data conversion, and downgrade implications. Extend the freeze manifest deliberately when a new revision becomes part of the supported migration history.

Application rollback and database downgrade are different operations. Prefer forward-compatible, additive changes. Before a data-changing downgrade, test the recovery path and verify a database/file backup in a separate runtime.
