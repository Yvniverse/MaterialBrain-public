"""Runtime-bound, read-only compact WarehouseBench projection."""

import copy
import json
import threading
from pathlib import Path

from app.core.config import settings

_cache: dict[tuple[str, str], dict] = {}
_lock = threading.Lock()


def benchmark_summary(snapshot: dict) -> dict:
    from warehouse_bench.runner import run_benchmark

    key = (snapshot["revision"], settings.materialbrain_build_sha)
    with _lock:
        if key not in _cache:
            report = run_benchmark(
                snapshot, seed=34017, repetitions=1, source_sha=settings.materialbrain_build_sha
            )
            _cache.clear()
            _cache[key] = {name: value for name, value in report.items() if name != "episodes"}
        result = copy.deepcopy(_cache[key])
    evidence = Path(settings.spatial_nav2_evidence_dir) / "summary.json"
    if evidence.is_file():
        from warehouse_bench.metrics import summarize
        from warehouse_bench.nav2 import import_nav2_summary

        summary = json.loads(evidence.read_text(encoding="utf8"))
        if summary.get("build_sha") != settings.materialbrain_build_sha:
            result["nav2_evidence_status"] = "STALE_BUILD"
        else:
            episodes = import_nav2_summary(evidence, snapshot)
            result["summaries"]["nav2_state_lattice:fastest"] = summarize(episodes)
            result["algorithm_availability"][-1] = {
                "algorithm": "nav2_state_lattice",
                "status": "EXECUTED",
            }
            result["nav2_evidence_status"] = "HASH_VERIFIED"
            result["nav2_evidence_artifacts"] = summary["artifacts"]
            result["nav2_safety_checks"] = summary.get("safety_checks", [])
    return result
