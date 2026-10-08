"""Select Nav2's installed differential-drive 5cm control set and verify primitives."""

import argparse
import hashlib
import json
from pathlib import Path


def prepare(search: str, output: str):
    candidates = []
    for path in Path(search).rglob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            meta = data["lattice_metadata"]
            if abs(meta["grid_resolution"] - 0.05) < 1e-9 and meta["motion_model"] in (
                "diff",
                "differential",
            ):
                candidates.append((abs(meta["turning_radius"] - 0.5), path, data))
        except (KeyError, ValueError, TypeError):
            continue
    if not candidates:
        raise RuntimeError(
            "installed Nav2 has no differential-drive 5cm primitive set; "
            "cannot claim State Lattice qualification"
        )
    _, path, data = min(candidates, key=lambda c: (c[0], str(c[1])))
    rotations = [
        p
        for p in data["primitives"]
        if p["start_angle_index"] != p["end_angle_index"]
        and all(abs(v[0]) + abs(v[1]) < 1e-9 for v in p["poses"])
    ]
    if len(rotations) < 2 * data["lattice_metadata"]["num_of_headings"]:
        raise RuntimeError("differential control set lacks left/right in-place rotations")
    destination = Path(output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(path.read_bytes())
    report = {
        "source": str(path),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "lattice_metadata": data["lattice_metadata"],
        "in_place_rotation_primitives": len(rotations),
    }
    destination.with_suffix(".manifest.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, indent=2))
    return report


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--search", default="/opt/ros/jazzy/share/nav2_smac_planner/sample_primitives")
    p.add_argument("--output", required=True)
    a = p.parse_args()
    prepare(a.search, a.output)
