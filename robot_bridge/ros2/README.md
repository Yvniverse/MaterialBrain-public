# ROS 2 Jazzy / Nav2 simulation

This package executes registered MaterialBrain missions with ROS 2 Jazzy and Nav2. The base consumes `/cmd_vel`, integrates a differential-drive pose, publishes odometry and TF, and raycasts laser scans against the canonical warehouse geometry. A swept rectangular footprint rejects penetration. Localization uses a synthetic `warehouse_map -> odom` transform.

The geometry source is `backend/app/services/embodied_navigation/world.v3.json`. The build derives a semantic snapshot, a 5 cm occupancy raster, and a matching differential-drive lattice primitive set. Map geometry, docks and content revision remain attached to every plan and event.

## Navigation configuration

The global planner uses `nav2_smac_planner::SmacPlannerLattice`. The controller uses `nav2_rotation_shim_controller::RotationShimController` with `nav2_mppi_controller::MPPIController`, `DiffDrive`, and full-footprint obstacle checking. Costmaps use a 0.76 x 0.58 m body with 0.09 m padding. Behavior trees include costmap clearing, wait, backup and spin recovery.

`GET /health` reports observed lifecycle state, action-server readiness, sensors, plugin parameters, installed Nav2 version, build SHA and map revision. A configuration file alone does not establish readiness.

## Start and test

From repository root with `.env` configured:

```bash
docker compose --profile robotics up --build -d
docker compose exec robotics bash -lc 'source /opt/ros/jazzy/setup.bash; source /opt/materialbrain/ros2/install/setup.bash; python3 /opt/materialbrain/ros2/scripts/acceptance.py --scenario nominal'
```

The bridge listens on the internal service port 8766. The public Compose project uses ROS domain 147 and stores generated robot records under its own storage root. For parallel simulators, use distinct Compose projects, ROS domains and storage roots.

For backend/API access, enable sample map registration or run `scripts/register_spatial_map.py --expected-database-name <your-public-database>` after Alembic reaches `0016_spatial_hd_map`.

## Internal HTTP contract

| Endpoint | Behavior |
| --- | --- |
| `GET /health` | Observed runtime readiness, plugins, versions, robot state and map revision. |
| `POST /missions` | Accept a frozen ready MissionPlan with registered IDs and the exact map revision. Identical retries are idempotent. |
| `GET /missions/{id}?after_sequence=N` | Return actual pose, mission status, completed/remaining stops, measured metrics and events after the cursor. |
| `POST /missions/{id}/handoff` | Verify an arrived dock and scan code. A rejected scan cannot complete a stop. |
| `POST /missions/{id}/cancel` | Hold the simulated base and confirm Nav2 cancellation while preserving completed work. |
| `POST /missions/{id}/replan` | Accept a replacement plan for the same mission, preserving observed pose, completed handoffs and event sequence. |
| `POST /obstacles` | Add or remove validated simulation obstacles. Reject geometry overlapping the robot body. |

The backend sends server-owned plans through this internal transport. Each event carries a monotonic sequence, stable event ID, map identity, `execution_boundary=ros2_nav2_simulation` and `hardware_control=false`. Registered graph nodes and directed edges determine waypoints; arbitrary goal coordinates are rejected.

The bridge separates arrival from scan/handoff confirmation. Planned time and energy are estimates; measured navigation metrics come from runtime observations. Battery discharge and charging are explicitly simulated. HOME and CHARGER are registered service docks.

A replan holds motion until the previous action has a confirmed terminal result. A cancellation acknowledgement alone cannot release the dispatch barrier. Late goal acceptance is cancelled and drained. Transport failures retain the hold and mission ledger for an explicit recovery attempt.

## Acceptance records

`acceptance.py --scenario all` covers nominal navigation, blocked paths, behavior-tree recovery, cancellation, low battery and charging. `--scenario semantic --plan <container-plan.json>` executes a planner-produced graph mission. Runs write topic JSONL, event JSONL and measured summaries under `/opt/materialbrain/.evidence`.

Freeze a completed run with `scripts/evidence_manifest.py --output <new-bundle-directory>`. For independent simulator instances, `scripts/aggregate_evidence.py --bundle <manifest> --bundle <manifest> --expected-sha <build-sha> --output <new-summary.json>` checks source/map/plugin identities and member hashes. Use separate ROS domains and record directories for each instance.

The WarehouseBench importer accepts measured summaries only when map revision, simulation boundary, active plugins, topic/event hashes and action feedback agree:

```bash
cd backend
python -m warehouse_bench --nav2-summary <summary.json> --nav2-topics <topics.jsonl> --nav2-events <events.jsonl> --output <local-output-directory>
```

## Source-level tests

Pure contracts and ROS callback lifecycle regressions can run without a ROS installation:

```powershell
$env:PYTHONPATH = 'robot_bridge/ros2'
python -m pytest robot_bridge/ros2/tests
```

ROS package builds and runtime acceptance require the optional robotics image. The package includes no physical robot driver.
