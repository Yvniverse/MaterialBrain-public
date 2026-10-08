"""Rasterize unchanged canonical OBBs; no altered goals, axes or fitted coordinates."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

from .geometry import load_world, point_rect_distance, static_obstacles


def export_map(world_path: str, output: str, resolution: float = 0.05) -> dict:
    world = load_world(world_path)
    directory = Path(output)
    directory.mkdir(parents=True, exist_ok=True)
    width, height = round(world["width"] / resolution), round(world["height"] / resolution)
    obstacles = static_obstacles(world)
    raster = bytearray(width * height)
    cell_radius = resolution * math.sqrt(2) / 2
    for row in range(height):
        y = (height - row - 0.5) * resolution
        for col in range(width):
            x = (col + 0.5) * resolution
            occupied = min(x, y, world["width"] - x, world["height"] - y) < resolution
            if not occupied:
                occupied = any(point_rect_distance(x, y, r) <= cell_radius for r in obstacles)
            raster[row * width + col] = 0 if occupied else 254
    pgm = f"P5\n{width} {height}\n255\n".encode() + raster
    (directory / "warehouse.pgm").write_bytes(pgm)
    (directory / "warehouse.yaml").write_text(
        f"image: warehouse.pgm\nmode: trinary\nresolution: {resolution}\n"
        "origin: [0.0, 0.0, 0.0]\nnegate: 0\noccupied_thresh: 0.65\nfree_thresh: 0.25\n",
        encoding="utf-8",
    )
    registry = {g["id"]: g["pose"] for g in world["goals"]}
    registry.update(HOME=world["home"], CHARGER=world["charger"])
    report = {
        "schema_version": 1,
        "map_id": world["id"],
        "source_sha256": hashlib.sha256(Path(world_path).read_bytes()).hexdigest(),
        "world_revision_sha256": world["revision_sha256"],
        "raster_sha256": hashlib.sha256(pgm).hexdigest(),
        "origin": [0, 0, 0],
        "frame": "warehouse_map",
        "width_m": world["width"],
        "height_m": world["height"],
        "resolution_m": resolution,
        "static_obb_count": len(obstacles),
        "registered_docks": registry,
        "raster_policy": (
            "conservative cells intersecting canonical OBBs; robot inflation belongs to Nav2"
        ),
    }
    (directory / "export-report.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8"
    )
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--world", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--resolution", type=float, default=0.05)
    args = parser.parse_args()
    print(json.dumps(export_map(args.world, args.output, args.resolution), indent=2))


if __name__ == "__main__":
    main()
