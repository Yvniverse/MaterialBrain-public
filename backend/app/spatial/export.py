"""CLI for ROS/container builds: python -m app.spatial.export --output map.json."""

import argparse
import json
from pathlib import Path

from .snapshot import build_lab_snapshot


def main():
    parser = argparse.ArgumentParser(description="Export the canonical metric SpatialMapSnapshot")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    snapshot = build_lab_snapshot()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(
            snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        )
        + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "map_id": snapshot["map_id"],
                "revision": snapshot["revision"],
                "output": str(args.output),
            }
        )
    )


if __name__ == "__main__":
    main()
