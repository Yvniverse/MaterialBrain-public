# Assets and sample data

MaterialBrain source and project-authored documentation use [Apache License 2.0](../LICENSE), except the [ROS2 subpackage](../robot_bridge/ros2/), which retains its [MIT License](../robot_bridge/ros2/LICENSE). Third-party dependencies and externally sourced material retain their own terms; see [NOTICE](../NOTICE).

## Project visuals

Architecture views are editable Markdown with Mermaid diagrams. Glacier uses project-provided interface components and CSS; the warehouse and laboratory render their equipment and geometry with Three.js.

The transparent [MaterialBrain Bot illustration](../frontend/src/embodied/assets/materialbrain-bot-v3.png) is maintainer-provided artwork generated with ChatGPT / gpt-image, as identified by its embedded C2PA provenance. The 1254 × 1254 RGBA image is reused for the assistant, workbench and laboratory with its pixels and provenance metadata preserved. Its source SHA256 is `2f17b97b7911532c390a9f201f66a0e98e56ab65c2761fb775849785be239dcb`. The original [vector illustration](../frontend/src/assets/materialbrain-agent.svg) remains available in the source tree.

The six [README gallery images](screenshots/) show the running application with synthetic inventory and a simulated robot. Capture routes, viewport dimensions, application revisions and image hashes are recorded in the [screenshot manifest](screenshots/manifest.json). Screenshots should exclude credentials, session tokens, customer records and externally owned artwork without permission.

When adding an external image, model, mesh, font or robot asset, record its source URL, author, license, modifications and required attribution beside the asset. A file extension or repository license does not establish rights to an imported asset.

## Sample data

Fixtures in `backend/sample_data/` and generated WarehouseBench tasks support development and reproducibility. Synthetic stock, locations, geometry and observations must retain source labels. Sample quantities are not evidence of live inventory or physical measurements.

Upstream component identifiers and datasheet references may be used as technical facts with attribution. Do not bundle vendor PDFs, purchasing records or source evidence archives unless their redistribution terms permit it. References to manufacturer documents do not relicense those documents.

## Generated output

Keep model weights, checkpoints, generated task datasets, evaluation traces, backups and runtime storage outside committed source. Product screenshots may be included under `docs/screenshots/` with their capture metadata. Reports shared with an issue or pull request should contain only the information needed to reproduce the behavior, using synthetic inputs where possible.
