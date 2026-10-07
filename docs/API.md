# API

The API prefix is `/api/v1`. A running installation exposes [interactive documentation](http://localhost:18080/api/docs) and [OpenAPI JSON](http://localhost:18080/api/openapi.json). OpenAPI defines the current request fields, bounds, and response schemas.

## Authentication

`POST /auth/login` creates a `materialbrain_session` HttpOnly cookie and a `materialbrain_csrf` cookie. Browser writes send the CSRF value in `X-CSRF-Token`. `GET /auth/me` returns the current user; `POST /auth/logout` revokes the session. Password changes use `PUT /auth/change-password`.

Endpoint permissions are checked on the backend. Inventory write payloads require an `idempotency_key`; repeated operations with the same valid key do not create a second transaction.

## Endpoint groups

Paths below are relative to `/api/v1`.

| Group | Principal operations |
| --- | --- |
| `/materials`, `/categories`, `/locations`, `/cables` | Material identity, attributes, locations, visual organizers, and cable queries. |
| `/inventory`, `/stock-movements`, `/stocktakes` | Authorized stock transactions, movements, reservations, and counts. |
| `/projects`, `/products`, `/purchase-orders` | Project BOMs, product revisions, build workflows, and purchasing. |
| `/evidence-documents`, `/component-intelligence` | Document evidence and component matching; see OpenAPI for evidence lifecycle routes. |
| `/agent/query`, `/agent/suggestions`, `/agent/proposals` | Structured agent responses and proposal lifecycle. |
| `/warehouse-maps` | Warehouse map versions, calibration, route previews, and twin snapshots. |
| `/navigation-lab/world`, `/navigation-lab/plan` | Read-only sample world and baseline navigation planning. |
| `/spatial` | PostGIS queries, semantic mission planning, stored missions, and execution events. |
| `/users`, `/roles`, `/audit-logs` | Administrative access management and audit history. |
| `/imports`, `/exports`, `/attachments` | Bounded imports, exports, and managed files. |

## Spatial operations

| Method and path | Behavior |
| --- | --- |
| `GET /spatial/maps/{map_id}/snapshot` | Read geometry, graph, zones, docks, dynamic overlays, and content revision. |
| `POST /spatial/query` | Query contains, nearby, nearest dock, intersects, edge zones, or affected edges. |
| `POST /spatial/missions/plan` | Return a `MissionPlan` without dispatching robot motion. |
| `GET /spatial/skills` | Read available spatial skill contracts. |
| `POST /spatial/missions` | Store a mission plan and TaskGraph. |
| `GET /spatial/missions/{id}` | Poll mission state and observed progress. |
| `POST /spatial/missions/{id}/start` | Explicitly start the stored plan in the configured simulation transport. |
| `POST /spatial/missions/{id}/cancel` | Cancel navigation while retaining completed progress. |
| `POST /spatial/missions/{id}/replan` | Replan remaining work from current state; optional profile selects `fastest`, `safest`, or `esd_safe`. |
| `POST /spatial/missions/{id}/handoff` | Submit the arrived stop's `goal_id` and `scan_code`. |
| `POST /spatial/missions/{id}/obstacles` | Add or remove a supported simulation obstacle scenario. |
| `GET /spatial/missions/{id}/episode` | Export an observed mission episode. |
| `GET /spatial/navigation/health` | Read bridge readiness, transport, map revision, and robot state. |
| `GET /spatial/benchmarks` | Read the available planner benchmark summary. |
| `GET /spatial/readiness/contracts` | Read observation, map-delta, and trajectory schemas. |
| `GET /spatial/missions/{id}/readiness-data` | Export observation contracts from mission state. |
| `POST /spatial/readiness/validate-map-delta` | Validate a proposed map change without automatically applying it. |

Spatial routes require at least one of `material:view`, `location:manage`, or `picking:view`; stored missions are scoped to their owner. The bridge itself is an internal service, reached through these authenticated APIs.

## Errors and state

Application business errors use `{code,message,details,request_id}`. Validation errors return HTTP 422. Framework-level failures may use FastAPI's `detail` field. Preserve the request ID when reporting a problem.

Mission requests carry a map revision. A stale map, invalid destination, disconnected start pose, or unsatisfied resource constraint prevents execution. `READY`, `INFEASIBLE`, and `CLARIFICATION` describe planning results; execution status and events are separate.

For complete workflows, see [Spatial Agent](SPATIAL_AGENT.md), [Robotics](ROBOTICS.md), and [Agent development](AGENT_DEVELOPMENT.md).
