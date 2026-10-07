#!/usr/bin/env bash
set -eo pipefail
source /opt/ros/jazzy/setup.bash
source /opt/materialbrain/ros2/install/setup.bash
export MATERIALBRAIN_WORLD="${MATERIALBRAIN_WORLD:-/opt/materialbrain/world.v3.json}"
export MATERIALBRAIN_SPATIAL_SNAPSHOT="${MATERIALBRAIN_SPATIAL_SNAPSHOT:-/opt/materialbrain/spatial_snapshot.json}"
export MATERIALBRAIN_EVIDENCE_DIR="${MATERIALBRAIN_EVIDENCE_DIR:-/opt/materialbrain/.evidence}"
export MATERIALBRAIN_NAV2_VERSION="$(dpkg-query -W -f='${Version}' ros-jazzy-nav2-smac-planner)"
mkdir -p /opt/materialbrain/.runtime "$MATERIALBRAIN_EVIDENCE_DIR"
python3 -m materialbrain_ros2.export_map --world "$MATERIALBRAIN_WORLD" --output /opt/materialbrain/.runtime
python3 /opt/materialbrain/ros2/scripts/prepare_lattice.py --output /opt/materialbrain/.runtime/diff_drive_lattice.json
if [[ $# -gt 0 ]]; then exec "$@"; fi
exec ros2 launch materialbrain_ros2 navigation.launch.py
