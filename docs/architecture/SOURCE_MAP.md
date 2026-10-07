# Architecture source map

| Architecture view | Implementation |
| --- | --- |
| [Runtime](01-runtime.md) | [Compose](../../docker-compose.yml), [FastAPI](../../backend/app/main.py), [frontend](../../frontend/src/), [Nginx](../../nginx/). |
| [Agent and tools](02-agent-tools.md) | [Agent](../../backend/app/agent/), [registry](../../backend/app/agent/tools/registry.py), [schemas](../../backend/app/schemas/agent.py). |
| [Engineering materials](03-engineering-materials.md) | [Component matching](../../backend/app/component_intelligence/), [domain services](../../backend/app/services/), [models](../../backend/app/models/). |
| [Spatial planning](04-spatial-planning.md) | [Spatial map](../../backend/app/spatial/), [mission planner](../../backend/app/services/spatial_mission/), [spatial API](../../backend/app/api/v1/spatial.py). |
| [TaskGraph and robotics](05-taskgraph-robotics.md) | [Spatial agent](../../backend/app/agent/spatial_agent/), [mission store](../../backend/app/services/spatial_mission_store.py), [ROS2](../../robot_bridge/ros2/), [Embodied Twin](../../frontend/src/embodied/). |
| [Model training and evaluation](06-model-training.md) | [WarehouseBench](../../backend/warehouse_bench/), [training](../../training/), [episode export](../../backend/app/agent/spatial_agent/dataset.py). |

The [API guide](../API.md) links the runtime contracts to user-facing operations. Generated runtime traces and model outputs remain local data rather than architecture source.
