"""Shared observation-to-JSON policy prompt for SFT and inference."""
# ruff: noqa: E501 -- preserve the canonical policy prompt

from __future__ import annotations

import json
import math

from .common import canonical, digest

SYSTEM_RULES = """你是仓库任务技能策略。根据当前观测选择一次动作，只输出一个 JSON 对象，不输出推理或 Markdown。
技能动作严格为 {"name":"已注册技能","args":{契约参数}}。非工具回复仅 {"decision":"clarify","reason":"缺失范围"} 或 {"decision":"deny","reason":"已证实不可行原因"}。
根据真实阶段、依赖、扫码和交接结果推进；封路后等待或重规划，低电量到注册充电点充电。扫码成功之后才交接；全部交接后回 HOME，再总结。
保持已完成站点。缺少目标/范围应澄清，库存不足或确定不可行应说明。不能虚构扫码、坐标、库存写入或成功；不要重复无进展工具。
所有 map_revision、map_id、mission_id、goal_id 必须取自当前观测。地图或任务身份错误时不能执行。
"""


def compact_tools(manifest):
    """Keep actual argument types/enums while avoiding repeated JSON schema boilerplate."""
    result = {}
    for skill in manifest:
        schema = skill["arguments_schema"]
        result[skill["name"]] = {
            "args": {
                key: (
                    {"enum": value["enum"]}
                    if "enum" in value
                    else {
                        k: value[k]
                        for k in ("type", "items", "default", "anyOf")
                        if k in value
                    }
                )
                for key, value in schema.get("properties", {}).items()
            },
            "required": schema.get("required", []),
        }
    return result


def messages_for(snapshot: dict, observation: dict) -> list[dict]:
    return [
        {
            "role": "system",
            "content": SYSTEM_RULES
            + "\n技能契约："
            + canonical(compact_tools(snapshot["skills"])),
        },
        {"role": "user", "content": "当前观测：" + canonical(observation)},
    ]


def prompt_identity(snapshot):
    return digest(
        {
            "system": SYSTEM_RULES,
            "tools": compact_tools(snapshot["skills"]),
            "version": 1,
        }
    )


def parse_decision(text: str) -> dict:
    """Never synthesize a repaired action or turn a failed model call into an expert."""
    candidate = text.strip()
    if candidate.startswith("```json") and candidate.endswith("```"):
        candidate = candidate[7:-3].strip()
    try:

        def reject_constant(value):
            raise ValueError("Non-finite JSON constant: " + value)

        def finite_float(value):
            result = float(value)
            if not math.isfinite(result):
                raise ValueError("Overflowing JSON number")
            return result

        value = json.loads(
            candidate, parse_constant=reject_constant, parse_float=finite_float
        )
    except (ValueError, TypeError):
        return {"name": "__invalid_json__", "args": {}}
    if not isinstance(value, dict):
        return {"name": "__invalid_json__", "args": {}}
    if "name" in value and (
        not isinstance(value["name"], str) or not isinstance(value.get("args"), dict)
    ):
        return {"name": "__invalid_schema__", "args": {}}
    # The shared production reducer checks destination membership before the
    # world verifier. Reject its malformed container input, retaining raw text
    # and token IDs in the trajectory; never substitute a legal destination.
    if (
        value.get("name") == "travel"
        and "destination" in value["args"]
        and not isinstance(value["args"]["destination"], str)
    ):
        return {"name": "__invalid_schema__", "args": {}}
    return value
