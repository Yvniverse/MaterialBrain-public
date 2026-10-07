# Contributing to MaterialBrain

Open an issue before changing a public API, persisted schema, task contract, or robot execution behavior. A pull request should explain the problem, resulting behavior, and relevant validation.

## Local setup

Use Python 3.12, Node.js 22, and pnpm 11. Create a virtual environment for this checkout and install the backend requirements. Install frontend dependencies with the checked-in lockfile.

```bash
python -m venv .venv
# Activate .venv using your shell's activation command.
python -m pip install -r backend/requirements.txt
python -m pip install --no-deps -r backend/requirements-mcp.txt
cd frontend
pnpm install --frozen-lockfile
```

## Isolated database tests

The test Compose overlay exposes PostGIS on loopback port `55432`. Copy `.env.example` to `.env.test`, choose a test password, and set `POSTGRES_DB=materialbrain_public_v02_test`, `POSTGRES_USER=materialbrain_public_test`, and a matching `DATABASE_URL`. Keep its project and database distinct from an application deployment.

```bash
docker compose --env-file .env.test -f docker-compose.yml -f docker-compose.test.yml -p materialbrain_public_v02_test up -d db
```

Set `TEST_POSTGRES_URL` in the test shell to `postgresql+psycopg://materialbrain_public_test:<test-password>@127.0.0.1:55432/materialbrain_public_v02_test`. For a full migration check, set `DATABASE_URL` to that same test URL, then run:

```bash
python tools/check_migration_history.py
cd backend
python -m alembic upgrade head
python -m ruff check app scripts tests warehouse_bench
python -m pytest
```

Spatial geometry, locking, and PostGIS migrations require PostgreSQL. Test setup must not seed, clear, or migrate a deployment database. Stop only this test project when finished:

```bash
docker compose --env-file .env.test -f docker-compose.yml -f docker-compose.test.yml -p materialbrain_public_v02_test down
```

## Frontend checks

```bash
cd frontend
pnpm lint
pnpm test
pnpm build
```

Browser tests require a running application and dedicated test credentials. Use an isolated runtime and sample data; keep exported traces and session state out of source control.

## Design constraints

- Resolve identifiers and deterministic facts on the server; model prose cannot change inventory, map versions, task progress, or approval state.
- Keep tool schemas bounded and permission checks explicit. New tools must be registered with their typed input schema and handler.
- Separate planning, execution, arrival, and handoff. A reached pose is not proof that a material was collected.
- Robot simulation must report its transport and `hardware_control` status. A simulator result cannot imply physical execution.
- Add new Alembic revisions for persisted changes; follow the [migration policy](docs/MIGRATION_POLICY.md).
- Keep sample data reusable, neutral, and explicit about synthetic quantities and geometry.

## Pull request contents

Include focused tests for changed behavior and migration notes when schemas change. UI changes should include a reproducible interaction or a screenshot captured from sample data. Describe permission or transaction invariants when changing write paths.

Do not commit credentials, customer information, source evidence archives, model weights, checkpoints, generated evaluation output, local backups, or browser session files. Third-party assets need source and license information; see [Assets](docs/ASSETS.md).
