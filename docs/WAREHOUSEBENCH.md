# WarehouseBench and model training

WarehouseBench exposes repeatable spatial tasks, independent route checks, and per-task results. Training utilities generate verified synthetic decisions from a frozen map snapshot. Mission episode export handles observed execution as a separate data source.

## Planner benchmark

Install the backend requirements, then run from `backend/`:

```bash
python -m warehouse_bench --seed 17 --repetitions 3 --output ../storage/benchmarks/planners
```

The two planner algorithms are `heading_grid_astar` and `semantic_graph_ortools`. The catalog includes registered destinations, multiple stops, routing profiles, payload/service constraints, time windows, blocked routes, remaining-goal replans, and charging requirements. Select subsets with repeatable `--scenario` or `--algorithm` arguments; `--help` lists the options.

Output contains `warehouse-bench.json`, episode JSONL, and an artifact hash manifest. Record the seed, world/map revision, algorithm, task configuration, and source revision with a result. Wall-clock timings and capture timestamps may vary between runs.

The verifier checks geometry, keepout zones, active closures, graph restrictions, required goals, payload, battery reserve, and time windows. Feasible plans, correct infeasibility decisions, unsupported tasks, and completed robot missions are distinct outcomes.

Distances and optimality ratios carry a named graph reference. Exact small-task comparisons apply to the filtered graph's stated scope. Planner time and energy estimates are separate from robot measurements.

## Measured Nav2 runs

After running a dedicated ROS2 acceptance scenario, import its summary and matching topic/event records:

```bash
python -m warehouse_bench --nav2-summary summary.json --nav2-topics topics.jsonl --nav2-events events.jsonl --output ../storage/benchmarks/nav2
```

The importer verifies the map revision, simulation transport, plugin configuration, finite metrics, and recorded artifact hashes. Without those inputs, Nav2 execution is marked as unavailable rather than inferred from a planned route. See [Robotics](ROBOTICS.md) for startup and acceptance.

## Synthetic training dataset

Run these commands from the repository root after installing backend requirements:

```bash
python training/export_snapshot.py --output training/datasets/snapshot.json
python training/generate_dataset.py --snapshot training/datasets/snapshot.json --output training/datasets/warehousebench --groups 1200 --workers 2
python training/replay.py --snapshot training/datasets/snapshot.json --data training/datasets/warehousebench --split test
```

The portable contract version is `materialbrain-warehousebench-v1`. Snapshot hashes bind geometry and skill contracts to the generated tasks. Group-based splitting keeps related scenarios in one train/validation/test partition. Generated decisions are verified before export; manifests record the input and file hashes.

The dataset directory contains `scenarios-{split}.jsonl`, `sft-{split}.jsonl`, `episodes-{split}.jsonl`, frozen split metadata, and `DATASET_MANIFEST.json`. Treat it as a versioned input to a run, and retain it with the run configuration outside committed source. Export to a fresh snapshot path and dataset directory for each run.

## SFT

Install [training/requirements-sft.txt](../training/requirements-sft.txt) in a suitable GPU environment. These optional PyTorch, Transformers, and PEFT dependencies are separate from application dependencies. Supply an existing local text-model directory; the entrypoint does not download model weights.

```bash
python training/train_sft.py --model /path/to/local-Qwen3.5-text-model --snapshot training/datasets/snapshot.json --data training/datasets/warehousebench --output training/outputs/sft --source-sha "$(git rev-parse HEAD)"
```

The source revision is recorded with the run. Optional `--receipt` checks a frozen dataset input receipt. `--smoke` provides a small training check. Use `--resume training/outputs/sft/checkpoint-N` to continue a compatible checkpoint; the trainer checks the checkpoint and run configuration before resuming.

Outputs include the LoRA adapter, checkpoints, configuration, and input metadata. Evaluate the adapter on a held-out task partition and retain the measured outputs.

## Observed mission episodes

Export an episode from `GET /api/v1/spatial/missions/{id}/episode`. Save a collection as JSON or JSONL, then run from `backend/`:

```bash
python -m app.agent.spatial_agent.dataset --input ../storage/episodes/episodes.jsonl --output-dir ../storage/datasets/observed
```

The exporter validates and deduplicates episodes, groups splits by map/scenario/seed, and writes `episodes.jsonl`, `sft-train.jsonl`, `sft-validation.jsonl`, `sft-test.jsonl`, `grpo-ready.jsonl`, and a hash manifest. SFT records contain the instruction, state, available/chosen/expert skills, observed outcome, and verifier fields; the GRPO-ready projection retains observed trajectories and reward metadata.

Exporting an episode dataset does not run training. Observed records and the synthetic trainer's decision contracts use different inputs; adapt and validate schemas explicitly before combining them.

## Reporting results

Publish the task configuration, software versions, source revision, map and dataset hashes, model settings, outcome counts, and measured metrics needed to reproduce a comparison. Keep planner feasibility, simulator completion, and hardware execution in their own reported scopes. Sample source, asset, and redistribution guidance is in [Assets](ASSETS.md).
