# WarehouseBench

WarehouseBench compares heading-grid A* and semantic-graph OR-Tools planning on the canonical synthetic warehouse. Run from the backend directory:

```bash
python -m warehouse_bench --seed 17 --repetitions 3 --output <local-output-directory>
```

The runner writes a JSON report, JSONL episodes and a SHA256 manifest. Seed and map/world revision reproduce requests, path geometry, ordering and resource estimates. Planning latency and capture timestamps are measured and may vary.

The scenario catalog exercises single and multiple registered targets, safest/ESD profiles, payload capacity, service times, priorities, blocked-aisle recovery, charging and infeasible time windows. Replans retain completed handoffs and carried payload. Targets always come from the registered map.

An independent verifier checks static geometry, keepout, active closures, directed edge restrictions, completed goals, payload, battery reserve and time windows. Feasible plans and correct infeasibility decisions are counted separately. Unsupported baseline constraints are explicit skips. Reports include latency percentiles, path/cost ratios, minimum clearance, constraint violations, task metrics, estimated mission time/energy and charging stops.

`path_ratio` uses a length-only Dijkstra graph bound plus exact Held-Karp ordering for at most eight remaining goals. This graph bound does not prove a continuous-space optimum. OR-Tools reports optimality only when its solver returns `ROUTING_OPTIMAL`; the separately named exact oracle applies to its unconstrained cost matrix.

## Measured Nav2 runs

Navigation measurements are optional. Import a completed ROS2 acceptance run:

```bash
python -m warehouse_bench --nav2-summary <summary.json> --nav2-topics <topics.jsonl> --nav2-events <events.jsonl> --output <local-output-directory>
```

The importer requires the matching map revision, simulation boundary, active State Lattice/MPPI plugins, differential-drive/full-footprint configuration, action feedback, finite measured metrics and matching topic/event hashes. Missing measurements remain unavailable; failed runs retain their failure status.

## Observed episodes and SFT records

Export an owner-scoped mission episode through `GET /api/v1/spatial/missions/{mission_id}/episode`, then prepare deduplicated records:

```bash
python -m app.agent.spatial_agent.dataset --input saved-episodes.jsonl --output-dir <local-dataset-directory>
```

The exporter accepts observed execution events, retains map/scenario grouping in deterministic train/validation/test splits, and omits failed actions from SFT labels. It prepares data; model training uses the separate public training entrypoint described in [model post-training](../../docs/architecture/06-model-training.md).
