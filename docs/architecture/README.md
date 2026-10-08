# Architecture atlas

MaterialBrain separates domain facts, spatial planning, robot execution, and model training. Each view below focuses on one boundary and links to the corresponding implementation.

| View | What it explains |
| --- | --- |
| [01 — Runtime](01-runtime.md) | Browser, API, PostGIS, managed storage, and optional robot service. |
| [02 — Agent and tools](02-agent-tools.md) | Typed contracts, entity resolution, permissions, and structured results. |
| [03 — Engineering materials](03-engineering-materials.md) | Evidence, constraints, draft selections, and BOM preview. |
| [04 — Spatial planning](04-spatial-planning.md) | Semantic map queries and constrained multi-stop planning. |
| [05 — TaskGraph and robotics](05-taskgraph-robotics.md) | Mission events, ROS2/Nav2, arrival, handoff, and recovery. |
| [06 — Model training and evaluation](06-model-training.md) | Synthetic tasks, observed episodes, verification, replay, and SFT. |

[SOURCE_MAP.md](SOURCE_MAP.md) maps these views to source directories. The diagrams are editable Mermaid embedded in Markdown.
