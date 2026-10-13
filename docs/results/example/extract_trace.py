"""Extract one deterministic, public-safe paired example from saved frozen TEST files.

This reads existing evidence only. Example:
  python extract_trace.py --bundle SAVED_BUNDLE --audit SAVED_READ_ONLY_AUDIT

No model inference, training, TEST rerun, or example-based checkpoint selection occurs.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


HERE = Path(__file__).resolve().parent
EXPECTED = {
    "scenarios-test.jsonl": "050300aa2a170fc0f0a093b8c8f5608e4ded9e87e789f98d463e251bcf171176",
    "S-traces.jsonl": "65031245fffd722dfd76a82ce47a6e282e68eeb9270fb7bcc7af5c5d69fff9dd",
    "S+Anchor+GRPO-ck16-traces.jsonl": "50bca196a3ec9f955719319d9e0ad02258f7829d3eed75085562504a6dd699f8",
    "READ_ONLY_RESULT_AUDIT.json": "e5afdaff10bad59abe3d2411686343df3304cc1c190bd3208ea5bc8ec96739f4",
}


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verified(path: Path, logical_name: str) -> None:
    actual = file_sha256(path)
    if actual != EXPECTED[logical_name]:
        raise ValueError(f"Unrecognized {logical_name}: {actual}")


def records(path: Path):
    with path.open("rb") as stream:
        for one_based_row, raw in enumerate(stream, 1):
            if raw.strip():
                yield one_based_row, json.loads(raw), sha256(raw)


def episode_for(path: Path, group_id: str):
    matches = [(row, episode, line_sha) for row, episode, line_sha in records(path)
               if episode.get("group_id") == group_id]
    if len(matches) != 1:
        raise ValueError(f"Expected one episode for group {group_id}, found {len(matches)}")
    return matches[0]


def public_actions(episode: dict) -> tuple[list[dict], int]:
    allowed = {"load", "query_routes", "travel", "inspect_dock", "scan", "handoff", "finish"}
    expected_arg_keys = {"load": {"stops"}, "query_routes": set(),
                         "travel": {"destination", "route"}, "inspect_dock": set(),
                         "scan": {"code"}, "handoff": {"key"}, "finish": set()}
    actions = []
    redactions = 0
    for one_based_step, step in enumerate(episode["trajectory"], 1):
        action = step["action"]
        name = action["name"]
        if name not in allowed:
            raise ValueError(f"Unexpected action type {name}")
        args = action.get("args") or {}
        if set(args) != expected_arg_keys[name]:
            raise ValueError(f"Unexpected saved arguments for {name}: {sorted(args)}")
        if name == "load":
            safe_args = {"stops": args["stops"]}
        elif name == "travel":
            safe_args = {"destination": args["destination"], "route": args["route"]}
        elif name == "scan":
            safe_args = {"code": args["code"]}
        elif name == "handoff":
            if "key" not in args:
                raise ValueError("Missing saved handoff key")
            safe_args = {"key": "[redacted mission-scoped id]"}
            redactions += 1
        else:
            safe_args = {}
        observation = step["observation"]
        actions.append({
            "step": one_based_step,
            "name": name,
            "args": safe_args,
            "tick_before": observation["ticks"],
            "remaining_ticks_before": observation["remaining_ticks"],
            "verifier_reason_if_terminal": step.get("verifier", {}).get("reason"),
        })
    return actions, redactions


def public_arm(episode: dict, source_row: int, line_sha: str) -> dict:
    actions, redactions = public_actions(episode)
    metrics = episode["metrics"]
    return {
        "arm": episode["arm"],
        "source_trace_row_one_based": source_row,
        "source_episode_jsonl_line_sha256": line_sha,
        "original_episode_hashes": {
            "semantic_trajectory_sha256": episode["semantic_trajectory_sha256"],
            "diagnostic_trace_sha256": episode["diagnostic_trace_sha256"],
            "harness_trace_sha256": episode["harness_trace_sha256"],
        },
        "actions": actions,
        "mission_scoped_handoff_keys_redacted": redactions,
        "terminal": {
            "reason": episode["termination_reason"],
            "time_tick": metrics["time"],
            "route_cost": metrics["route_cost"],
            "energy": metrics["energy"],
            "task_success": metrics["task_success"],
            "recovery_success": metrics["recovery_success"],
            "safety_violations": metrics["safety_violations"],
            "unauthorized_actions": metrics["unauthorized_actions"],
        },
    }


def format_action(row: dict) -> str:
    args = row["args"]
    name = row["name"]
    if name == "load":
        return f"`load({','.join(args['stops'])})`"
    if name == "travel":
        return f"`travel({args['destination']}, {args['route']})`"
    if name == "scan":
        return f"`scan({args['code']})`"
    if name == "handoff":
        return "`handoff([redacted id])`"
    return f"`{name}()`"


def markdown(output: dict) -> str:
    scenario = output["scenario"]
    s = output["arms"]["S"]
    rl = output["arms"]["S+Anchor+GRPO-ck16"]
    actions_s, actions_rl = s["actions"], rl["actions"]
    lines = [
        "# 一条已保存的冻结 TEST 配对轨迹：返航时窗",
        "",
        "这是按原始 `scenarios-test.jsonl` **输入顺序选出的首个合格容量胜例**，用于解释已报告结果，不参与训练、模型选择或新增评测。两个模型从相同的冻结场景与前缀状态开始；完整的原始动作顺序及其保存时的 tick 见下表，[机器可读副本](PAIRED_TRACE.json)记录源文件与 episode 哈希。",
        "",
        f"场景 `group_id={scenario['group_id']}`，模板 `{scenario['template_id']}`，`scenario_sha256={scenario['scenario_sha256']}`，`cut_state_sha256={scenario['cut_state_sha256']}`。截止 tick **{scenario['deadline_tick']}**；预设 slack **{scenario['slack_ticks']}**。第 {output['selection']['input_row_one_based']} 行即首个符合规则的输入。",
        "",
        "| 步 | S：动作（执行前 tick／剩余） | Anchor+GRPO16：动作（执行前 tick／剩余） |",
        "|---:|---|---|",
    ]
    for index in range(max(len(actions_s), len(actions_rl))):
        left = actions_s[index] if index < len(actions_s) else None
        right = actions_rl[index] if index < len(actions_rl) else None
        def cell(row):
            return (f"{format_action(row)} · {row['tick_before']}／{row['remaining_ticks_before']}"
                    if row else "—")
        lines.append(f"| {index + 1} | {cell(left)} | {cell(right)} |")
    lines.extend([
        "",
        f"前 **15 步**动作及参数相同。第 16 步两者都从 tick **30** 返航 HOME，剩余 **6 ticks**；S 的原始参数为 `route=safest`，组合臂为 `route=fastest`。S 在 tick **{s['terminal']['time_tick']}** 以 `{s['terminal']['reason']}` 结束，超过 tick {scenario['deadline_tick']}；组合臂在 tick **35** 发出第 17 步 `finish()`，并于 tick **{rl['terminal']['time_tick']}** 以 `{rl['terminal']['reason']}` 结束。记录的总 `route_cost` 为 **{s['terminal']['route_cost']}→{rl['terminal']['route_cost']}**。",
        "",
        "这是保存轨迹的观察性比较：共同前缀与最后返航参数差异支持对失败路径的解释，但未执行只改变一个参数的反事实实验。两个 `handoff` 动作中的 mission-scoped key 已在公开副本中遮盖；其动作顺序、其他参数、tick、结局与原始哈希保持可核对。该 TEST 案例已暴露，不可回填训练或用于调参。",
        "",
        "## 原始证据 SHA256",
        "",
        "| 证据 | SHA256 |",
        "|---|---|",
    ])
    for name, digest in output["source_file_sha256"].items():
        lines.append(f"| `{name}` | `{digest}` |")
    for arm, data in output["arms"].items():
        lines.append(f"| `{arm}` episode JSONL 原始行 | `{data['source_episode_jsonl_line_sha256']}` |")
        for name, digest in data["original_episode_hashes"].items():
            lines.append(f"| `{arm}` {name} | `{digest}` |")
    lines.extend(["", "本公开摘录省略了原始模型 token 序列、内部观测和运行路径；[JSON](PAIRED_TRACE.json)保存核验该例所需的场景、序列及结局字段。", ""])
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True, help="Directory containing inputs/ and raw/ saved files")
    parser.add_argument("--audit", type=Path, required=True, help="Saved read-only action-comparison audit JSON")
    args = parser.parse_args()
    scenario_path = args.bundle / "inputs" / "scenarios-test.jsonl"
    s_path = args.bundle / "raw" / "S-traces.jsonl"
    rl_path = args.bundle / "raw" / "S+Anchor+GRPO-ck16-traces.jsonl"
    for name, path in [("scenarios-test.jsonl", scenario_path), ("S-traces.jsonl", s_path),
                       ("S+Anchor+GRPO-ck16-traces.jsonl", rl_path),
                       ("READ_ONLY_RESULT_AUDIT.json", args.audit)]:
        verified(path, name)
    audit = json.loads(args.audit.read_text(encoding="utf-8"))
    selected = None
    for input_row, scenario, scenario_line_sha in records(scenario_path):
        if scenario.get("family") != "capacity_conflict":
            continue
        group_id = scenario["group_id"]
        s_row, s, s_line_sha = episode_for(s_path, group_id)
        rl_row, rl, rl_line_sha = episode_for(rl_path, group_id)
        if (s.get("input_capacity_eligible") and rl.get("input_capacity_eligible")
                and s["termination_reason"] == "DEADLINE_EXHAUSTED"
                and rl["termination_reason"] == "SUCCESS"
                and not s["metrics"]["task_success"] and rl["metrics"]["task_success"]):
            selected = (input_row, scenario, scenario_line_sha, s_row, s, s_line_sha,
                        rl_row, rl, rl_line_sha)
            break
    if selected is None:
        raise ValueError("No eligible saved paired capacity win")
    input_row, scenario, scenario_line_sha, s_row, s, s_line_sha, rl_row, rl, rl_line_sha = selected
    for episode in (s, rl):
        for key in ("group_id", "scenario_sha256", "cut_state_sha256"):
            if episode[key] != scenario[key]:
                raise ValueError(f"Mismatched saved pair {key}")
    audit_rows = audit["capacity_action_sequence_comparison"]["S"]["rows"]
    audit_row = next((r for r in audit_rows if r["group_id"] == scenario["group_id"]), None)
    if not audit_row or audit_row["classification"] != "home_route_argument_only" or not audit_row["win"]:
        raise ValueError("Pair is not a saved home-route-only win")
    s_public = public_arm(s, s_row, s_line_sha)
    rl_public = public_arm(rl, rl_row, rl_line_sha)
    a, b = s_public["actions"], rl_public["actions"]
    if a[:15] != b[:15] or len(a) != 16 or len(b) != 17:
        raise ValueError("Unexpected common prefix / action lengths")
    if not (a[15]["name"] == b[15]["name"] == "travel"
            and a[15]["args"] == {"destination": "HOME", "route": "safest"}
            and b[15]["args"] == {"destination": "HOME", "route": "fastest"}
            and b[16]["name"] == "finish"):
        raise ValueError("Unexpected decisive final HOME action")
    if (scenario["base_scenario"]["deadline"] != 36
            or scenario["difficulty_cell"]["slack_ticks"] != 0
            or a[15]["tick_before"] != 30 or b[15]["tick_before"] != 30):
        raise ValueError("Unexpected saved deadline/tick state")
    output = {
        "kind": "SAVED_FROZEN_TEST_PAIRED_CAPACITY_EXAMPLE",
        "selection": {
            "rule": "First original input-order capacity_conflict with eligible S deadline failure and paired GRPO success; read-only illustration",
            "input_row_one_based": input_row,
            "scenario_jsonl_line_sha256": scenario_line_sha,
            "training_or_selection_use": False,
        },
        "source_file_sha256": dict(EXPECTED),
        "scenario": {
            "group_id": scenario["group_id"],
            "family": scenario["family"],
            "template_id": scenario["template_id"],
            "scenario_sha256": scenario["scenario_sha256"],
            "cut_state_sha256": scenario["cut_state_sha256"],
            "base_scenario_sha256": s["base_scenario_sha256"],
            "prefix_sha256": s["prefix_sha256"],
            "deadline_tick": scenario["base_scenario"]["deadline"],
            "slack_ticks": scenario["difficulty_cell"]["slack_ticks"],
            "prefix_time_tick": scenario["prefix_metrics"]["time"],
            "completion_budget_steps": scenario["completion_budget"],
        },
        "arms": {"S": s_public, "S+Anchor+GRPO-ck16": rl_public},
        "comparison": {
            "common_action_prefix_length": 15,
            "decisive_step": 16,
            "decisive_destination": "HOME",
            "S_route": "safest",
            "GRPO_route": "fastest",
            "candidate_extra_action": "finish",
            "saved_audit_classification": audit_row["classification"],
            "observation_only_not_counterfactual": True,
        },
        "redaction": "Only mission-scoped handoff keys are replaced; action order, all other displayed args, ticks, metrics, and saved SHA references are retained.",
    }
    (HERE / "PAIRED_TRACE.json").write_text(json.dumps(output, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    (HERE / "PAIRED_TRACE.md").write_text(markdown(output), encoding="utf-8")
    print(json.dumps({"selected_input_row": input_row, "group_id": scenario["group_id"],
                      "json_sha256": file_sha256(HERE / "PAIRED_TRACE.json"),
                      "markdown_sha256": file_sha256(HERE / "PAIRED_TRACE.md")}, indent=2))


if __name__ == "__main__":
    main()
