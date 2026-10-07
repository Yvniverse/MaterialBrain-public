# Deploy MaterialBrain

The Compose stack runs the web proxy, frontend, backend, PostGIS, and scheduled backups. An optional `robotics` profile adds the ROS2/Nav2 simulator. Use Docker Engine with Compose v2.

## Configure an installation

Check out the v0.2 feature branch and create a local environment file:

```bash
git clone --branch codex/public-v0.2-spatial-agent https://github.com/Yvniverse/MaterialBrain-public.git
cd MaterialBrain-public
cp .env.example .env
```

Set `POSTGRES_PASSWORD` to a long random value and update the matching password in `DATABASE_URL`. URL-encode characters that have special meaning in a URL. Model API keys belong in this local backend configuration.

| Setting | Default and purpose |
| --- | --- |
| `COMPOSE_PROJECT_NAME` | `materialbrain_public_v02`; prefixes the installation's containers, networks, and database volume. |
| `WEB_PORT` | `18080`; the web proxy's host port. |
| `POSTGRES_DB` / `POSTGRES_USER` | `materialbrain_public`; database and application role. |
| `DATABASE_URL` | Connection to the Compose service `db`, using the configured database, user, and password. |
| `MATERIALBRAIN_STORAGE_ROOT` | `./storage`; persistent attachments, evidence, imports, exports, backups, and robot records. |
| `COOKIE_SECURE` | `false` for local HTTP; use `true` when serving the application over HTTPS. |
| `AGENT_ENABLED` | `false`; enable the natural-language Agent entrypoint explicitly. |
| `SPATIAL_SAMPLE_MAP_ENABLED` | `false`; enable automatic sample-map registration after migration, or use the registration command below. |
| `ROS_DOMAIN_ID` | `147`; ROS domain for the optional simulator. |

For another installation, choose a distinct Compose project, database configuration, storage root, browser port, and ROS domain. Use its project name consistently in Compose commands. The base stack exposes only the web proxy; PostGIS and the robot bridge use internal service ports. The [contribution guide](../CONTRIBUTING.md) provides a separate loopback database overlay for tests.

## Start and initialize

Run from repository root:

```bash
docker compose -p materialbrain_public_v02 config --quiet
docker compose -p materialbrain_public_v02 up -d --build
docker compose -p materialbrain_public_v02 exec backend python scripts/create_admin.py
docker compose -p materialbrain_public_v02 exec backend python scripts/register_spatial_map.py --expected-database-name materialbrain_public
```

The backend applies Alembic migrations and initializes permissions at startup. Administrator creation prompts for credentials. Map registration checks the database name and adds the synthetic `MB-EMB-LAB-03` map without activating it as an operational warehouse or changing inventory. Adjust the expected database name when `POSTGRES_DB` differs.

Open [http://localhost:18080](http://localhost:18080), log in, and open `/warehouse-lab`. A basic application health check is:

```bash
curl http://localhost:18080/api/v1/health
docker compose -p materialbrain_public_v02 ps
```

Spatial queries and planning work without a model key. To enable the Agent, set `AGENT_ENABLED=true` in `.env` and recreate the backend with `docker compose -p materialbrain_public_v02 up -d backend`. Optional model providers use server-side credentials and settings.

For an HTTPS deployment, terminate TLS at the site's proxy, set `COOKIE_SECURE=true`, and retain the API origin and CSRF handling described in [Security](SECURITY.md).

## Optional robotics

```bash
docker compose -p materialbrain_public_v02 --profile robotics up -d --build robotics
```

The backend reaches the simulator at `http://robotics:8766`. The bridge has no host port mapping and writes observed records beneath the configured storage root's `robotics/` directory. Review `GET /api/v1/spatial/navigation/health` through an authenticated session before starting a mission.

Mission creation and execution are separate operations. The supplied service controls a simulated base and reports `hardware_control=false` and `inventory_written=false`. See [Robotics](ROBOTICS.md) for readiness, acceptance scenarios, and recovery, and [Spatial Agent](SPATIAL_AGENT.md) for the API contract.

## Operate and upgrade

Inspect service state and recent logs with the same project name:

```bash
docker compose -p materialbrain_public_v02 ps
docker compose -p materialbrain_public_v02 logs --tail 100 backend nginx
```

Back up the database and managed files before an upgrade. The backup service uses `BACKUP_SCHEDULE` and `BACKUP_RETENTION_DAYS`; [Backup and restore](BACKUP_RESTORE.md) explains the generated files and recovery procedure. Check out the desired source revision, review its migration notes, then rebuild this installation with `docker compose -p materialbrain_public_v02 up -d --build`.

To stop the installation while retaining its database volume and storage:

```bash
docker compose -p materialbrain_public_v02 down
```

Database migration details are in [Database](DATABASE.md) and the [migration policy](MIGRATION_POLICY.md).
