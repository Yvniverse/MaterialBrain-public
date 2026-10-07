# Spatial Agent

MaterialBrain turns registered semantic destinations and robot constraints into inspectable mission plans. PostGIS supplies the spatial facts; the mission planner produces an ordered plan; TaskGraph tracks execution observations.

## Register the sample map

Start the web application and apply migrations using the [README](../README.md). The sample map is `MB-EMB-LAB-03`, with local metric coordinates in SRID 0 and synthetic geometry. Register it in the default application database:

```bash
docker compose -p materialbrain_public_v02 exec backend python scripts/register_spatial_map.py --expected-database-name materialbrain_public
```

Replace the expected database name if you changed `POSTGRES_DB`. The command checks the current database identity and spatial migration before registration. It adds the sample map without changing inventory or activating it as an operational warehouse map.

`SPATIAL_SAMPLE_MAP_ENABLED=true` can register the same map during backend startup after migrations. Keep registration explicit when configuring an existing installation.

## Read spatial facts

After logging in, request `GET /api/v1/spatial/maps/MB-EMB-LAB-03/snapshot`. The response includes:

- `map_id` and `revision`;
- coordinate frame, floor, walls, and equipment geometry;
- semantic zones and affordances;
- directed route nodes and edges;
- registered task docks, `HOME`, and `CHARGER`;
- active dynamic overlays and source metadata.

`POST /api/v1/spatial/query` accepts a map ID and query object. For example:

```json
{
  "map_id": "MB-EMB-LAB-03",
  "query": {
    "kind": "nearby",
    "entity": "docks",
    "point": {"x": 3.0, "y": 3.0},
    "radius_m": 5,
    "limit": 5
  }
}
```

Supported query kinds are `contains`, `nearby`, `nearest_dock`, `intersects`, `edge_zones`, and `affected_edges`. PostgreSQL executes indexed spatial predicates; the geometry compatibility helpers used in some unit tests do not replace PostGIS integration testing.

## Plan a mission

The task docks are `P-IC`, `P-SENSOR`, `P-PWR`, `P-LAB`, `P-MODULE`, `P-PASSIVE`, `P-CONN`, `P-CABLE`, and `P-RECEIVE`. Use a dock's registered ID rather than free coordinates as a destination.

`POST /api/v1/spatial/missions/plan` takes a `MissionRequest`:

| Field | Meaning |
| --- | --- |
| `map_id`, `map_revision` | Snapshot identity; copy the current revision from the snapshot. |
| `start_pose` | Current robot pose in the map frame; use observed navigation health during execution. |
| `goal_ids` | Registered destinations to visit. |
| `profile` | `fastest`, `safest`, or `esd_safe`. |
| `stops` | Optional per-stop service time, demand, priority, and time window. |
| `constraints` | Payload capacity, current battery, battery reserve, and robot class. |
| `completed_goal_ids` | Previously completed task stops when replanning. |
| `return_home` | Include a final return-home segment. |
| `dynamic_overlays` | Additional active restrictions included in planning. |

The planner filters unsafe or restricted edges, constructs pairwise paths, and uses OR-Tools for constrained stop ordering. It returns `READY`, `INFEASIBLE`, or `CLARIFICATION`, with ordered stops, segments, solver information, objective terms, resource estimates, and violations.

Planning estimates describe the filtered graph and configured battery/service model. They are distinct from measured Nav2 execution. A solver result establishes optimality only within the scope reported by that result.

## Create and execute

`POST /api/v1/spatial/missions` accepts `{request: MissionRequest, client_operation_id: "stable-operation-id"}` and stores the plan with a TaskGraph. Creation does not move the robot. Start through `POST /api/v1/spatial/missions/{id}/start` after reviewing the plan and [robot readiness](ROBOTICS.md).

The `/warehouse-lab` UI provides the Embodied Twin. Agent mission cards also link to `/warehouse-twin?workspace=robot-lab`. Enable `AGENT_ENABLED=true` to use the natural-language Agent entrypoint; deterministic spatial requests do not require model credentials. Inspect the current plan, robot state, and remaining goals there.

Poll `GET /api/v1/spatial/missions/{id}` for progress. At a task dock, arrival precedes scan/handoff verification. Submit `{goal_id, scan_code}` to the handoff endpoint; an incorrect scan does not complete the stop. Completed handoffs remain recorded through cancellation or replanning.

The supplied simulation transport reports `hardware_control=false` and `inventory_written=false`. Stock transactions and picking settlement use their own permission and transaction paths.

## Recovery and observation

Cancel a mission explicitly when it should stop. Replanning uses the current pose, completed goals, and remaining work. Supported obstacle scenarios are `blocked-crossing` and `isolated-dock`; an obstacle update is a simulation operation.

Episodes are available from `GET /api/v1/spatial/missions/{id}/episode`. Readiness exports expose observation and trajectory contracts. Map-delta validation reports whether a proposed change satisfies the map contract; it does not automatically write a map revision.

For deterministic task generation, route verification, and replay, see [WarehouseBench](WAREHOUSEBENCH.md).
