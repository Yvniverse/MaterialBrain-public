# Database and spatial schema

The application uses PostgreSQL 17 with PostGIS 3.5. Alembic manages schema evolution; SQLAlchemy models define current domain records. SQLite provides compatibility for selected unit tests and cannot establish PostGIS geometry behavior.

## Core records

| Domain | Records |
| --- | --- |
| Access | Users, roles, sessions, permissions, and audit events. |
| Materials | Materials, suppliers, cable specifications, engineering documents, and evidence anchors. |
| Engineering | Projects, product revisions, BOM items, reviewed relations, and build plans. |
| Warehouse | Inventory lots, locations, movements, reservations, loans, pick tasks, and allocations. |
| Agent | Conversation context, action proposals, and structured episodes. |
| Spatial | Warehouse maps, route nodes/edges, location bindings, assets, semantic zones, docks, and dynamic overlays. |

## Spatial frame

The sample map uses local metric coordinates with SRID `0`. Distances, widths, service times, battery estimates, and resource limits have declared units in the spatial schema. Map snapshots include a revision and graph hash.

`warehouse_maps` retains warehouse identity and scalar metadata. Geometry extends existing route nodes, edges, and location bindings. Additive tables store polygons for spatial assets and zones, registered dock poses, and active restrictions with expiry times.

GiST indexes support geometry predicates. PostgreSQL executes containment, intersection, nearby, nearest-dock, and affected-edge queries. Overlays are filtered by active state and time; expired restrictions do not remain permanent map facts.

Register the supplied map with the database identity guard:

```bash
docker compose -p materialbrain_public_v02 exec backend python scripts/register_spatial_map.py --expected-database-name materialbrain_public
```

Change the expected name if `POSTGRES_DB` differs. Registration is idempotent and does not change inventory or activate the synthetic map as an operational warehouse.

## Migrations

The backend startup command applies `alembic upgrade head`. Spatial schema additions are in `0016_spatial_hd_map`; the image must support `CREATE EXTENSION postgis`.

Released migration files are pinned by `backend/alembic/MIGRATION_HISTORY_SHA256.json`. New changes use a new revision. The migration entrypoint prevents the legacy metadata bootstrap from creating spatial tables before their warehouse dependencies; the explicit spatial revision owns those tables.

```bash
python tools/check_migration_history.py
cd backend
python -m alembic upgrade head
```

Use a fresh test database for migration roundtrips. Keep its project, credentials, volume, storage, and host port separate from an application instance; [Contributing](../CONTRIBUTING.md) supplies the test overlay commands.

## Transactions and recovery

Inventory writes, allocation settlement, and approvals use transaction boundaries, permission checks, and idempotency keys. Creating or completing a simulated robot mission does not settle inventory.

Attachment and evidence files live outside the database. A recovery set includes a verified database dump and the corresponding managed file storage. Follow [Backup and restore](BACKUP_RESTORE.md) and [Deployment](DEPLOYMENT.md) for operational procedures.
