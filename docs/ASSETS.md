# Assets and sample data

MaterialBrain source and project-authored documentation use [Apache License 2.0](../LICENSE), except the [ROS2 subpackage](../robot_bridge/ros2/), which retains its [MIT License](../robot_bridge/ros2/LICENSE). Third-party dependencies and externally sourced material retain their own terms; see [NOTICE](../NOTICE).

## Project visuals

Architecture views are editable Markdown with Mermaid diagrams. The frontend launcher uses the original [MaterialBrain vector illustration](../frontend/src/assets/materialbrain-agent.svg). New screenshots should be captured from a sample runtime and exclude credentials, session state, customer records, and externally owned artwork without permission.

When adding an external image, model, mesh, font, or robot asset, record its source URL, author, license, modifications, and required attribution beside the asset. A file extension or repository license does not establish rights to an imported asset.

## Sample data

Fixtures in `backend/sample_data/` and generated WarehouseBench tasks support development and reproducibility. Synthetic stock, locations, geometry, and observations must retain source labels. Sample quantities are not evidence of live inventory or physical measurements.

Upstream component identifiers and datasheet references may be used as technical facts with attribution. Do not bundle vendor PDFs, purchasing records, or source evidence archives unless their redistribution terms permit it. References to manufacturer documents do not relicense those documents.

## Generated output

Keep model weights, checkpoints, generated task datasets, evaluation traces, backups, and runtime storage outside committed source. Reports shared with an issue or pull request should contain only the information needed to reproduce the behavior, using synthetic inputs where possible.
