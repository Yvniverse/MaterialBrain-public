# Robot execution bridge

MaterialBrain connects server-owned warehouse missions to a ROS 2 Jazzy / Nav2 simulator. The backend authenticates users, plans against the registered semantic map, and reconciles observed navigation events into an owner-scoped TaskGraph.

- [`ros2/`](ros2/README.md) contains the ROS package, simulated base, navigation configuration, HTTP mission transport, and acceptance tools.
- `nav2_adapter.py` provides a navigator-injection interface for integrations that supply their own navigator and pose factory. Dock identity, readiness, idempotency, cancellation, and arrival are checked before progress is accepted.
- `world/` contains reference docking, occupancy, and collision assets for the canonical synthetic warehouse. Coordinates use local metres; registered dock IDs are the navigation targets.

## Run the simulator

From repository root, configure `.env` using `.env.example`, then run:

```bash
docker compose --profile robotics up --build -d
```

The Compose project uses its own database, storage and networks. The robotics service is available to the backend at `http://robotics:8766` and uses `ROS_DOMAIN_ID=147` by default. Change the domain and project/storage settings when running additional independent instances.

Enable `SPATIAL_SAMPLE_MAP_ENABLED=true` to register the canonical sample map after migration. For explicit registration against the default public database:

```bash
docker compose exec backend python scripts/register_spatial_map.py --expected-database-name materialbrain_public
```

Open the warehouse twin's Robot Lab workspace to plan and start a mission. Arrival, scan verification and human handoff are separate events. Missions preserve completed stops through cancellation, transport pauses and replanning. Navigation never settles inventory; stock changes remain in the normal business workflow.

The shipped runtime controls a simulated base. A physical deployment requires its own drivers, localization, safety controls and readiness integration.
