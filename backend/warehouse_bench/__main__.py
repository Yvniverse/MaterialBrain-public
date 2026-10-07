"""python -m warehouse_bench --seed 17 --repetitions 3 --output <directory>."""

import argparse
import json
from pathlib import Path

from .metrics import summarize
from .nav2 import import_nav2_summary
from .runner import ALGORITHMS, run_benchmark, write_results


def main():
    parser = argparse.ArgumentParser(description="Execute canonical WarehouseBench planners")
    parser.add_argument("--seed", type=int, default=17)
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--algorithm", action="append", choices=ALGORITHMS)
    parser.add_argument("--scenario", action="append")
    parser.add_argument("--source-sha")
    parser.add_argument("--output", type=Path, default=Path(__file__).parent / "results")
    parser.add_argument("--nav2-summary", type=Path)
    parser.add_argument("--nav2-topics", type=Path)
    parser.add_argument("--nav2-events", type=Path)
    arguments = parser.parse_args()
    report = run_benchmark(
        seed=arguments.seed,
        repetitions=arguments.repetitions,
        algorithms=tuple(arguments.algorithm) if arguments.algorithm else ALGORITHMS,
        case_ids=arguments.scenario,
        source_sha=arguments.source_sha,
    )
    if arguments.nav2_summary:
        from app.spatial.snapshot import build_lab_snapshot

        episodes = import_nav2_summary(
            arguments.nav2_summary,
            build_lab_snapshot(),
            seed=arguments.seed,
            topics_path=arguments.nav2_topics,
            events_path=arguments.nav2_events,
        )
        report["episodes"].extend(episodes)
        for availability in report["algorithm_availability"]:
            if availability["algorithm"] == "nav2_state_lattice":
                availability.update(
                    {
                        "status": "IMPORTED_MEASURED_EVIDENCE",
                        "reason": (
                            "Exact-revision topic/event hashes and active State Lattice/MPPI "
                            "plugins verified"
                        ),
                    }
                )
        report["summaries"]["nav2_state_lattice:measured"] = summarize(episodes)
    manifest = write_results(report, arguments.output)
    print(json.dumps({"artifacts": manifest, "summaries": report["summaries"]}, indent=2))


if __name__ == "__main__":
    main()
