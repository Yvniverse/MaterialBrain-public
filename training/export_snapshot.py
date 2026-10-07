"""Export the public semantic map and typed skills for offline WarehouseBench."""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend"))


def export(output: Path):
    from app.spatial.snapshot import build_lab_snapshot

    from training.materialbrain_training.common import file_hash, write_json
    from training.materialbrain_training.vendor.skills import skill_manifest

    if output.exists():
        raise FileExistsError("Snapshot exists; choose a new output path")
    vendor = ROOT / "training/materialbrain_training/vendor"
    identity = [
        {"bundled": path.name, "bundled_sha256": file_hash(path)}
        for path in sorted(vendor.glob("*.py"))
    ]
    snapshot = {
        "contract_version": "materialbrain-warehousebench-v1",
        "source_sha": subprocess.check_output(
            ["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True
        ).strip(),
        "map": build_lab_snapshot(),
        "skills": skill_manifest(),
        "planner_identity": {"files": identity},
    }
    write_json(output, snapshot)
    write_json(output.with_name(output.stem + ".manifest.json"), {
        "contract_version": snapshot["contract_version"],
        "files": {output.name: file_hash(output)},
    })
    return {"map_revision": snapshot["map"]["revision"], "sha256": file_hash(output)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(export(args.output))


if __name__ == "__main__":
    main()
