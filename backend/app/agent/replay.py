"""Safe, hash-bound fixture validation for deterministic read-only traces.

This module does not execute production tool trajectories.  Its replay output
is a ``fixture_validation`` artifact containing hashes and redacted metadata;
open-ended ``executable_replay`` remains outside this phase.
"""

from __future__ import annotations

import re
from typing import Any

from app.agent.episode import (
    REDACTION_VERSION,
    canonical_hash,
    public_fact_refs,
    safe_failures,
    safe_trace_steps,
)

REPLAY_SCHEMA_VERSION = 1
REPLAY_MODE = "fixture_validation"


def episode_export(episode: Any) -> dict[str, Any]:
    """Export an AgentEpisode without prompts, tool arguments, or observations."""

    telemetry = list(getattr(episode, "telemetry", None) or [])
    business_outcome = dict(getattr(episode, "business_outcome", None) or {})
    human_feedback = dict(getattr(episode, "human_feedback", None) or {})
    return {
        "episode_id": str(getattr(episode, "id", "")),
        "request_id": str(getattr(episode, "request_id", "")),
        "conversation_id": str(getattr(episode, "conversation_id", "") or ""),
        "client_operation_id": str(getattr(episode, "client_operation_id", "") or ""),
        "entry_surface": str(getattr(episode, "entry_surface", "other")),
        "execution_mode": str(getattr(episode, "execution_mode", "deterministic")),
        "status": str(getattr(episode, "status", "error")),
        "task_contract_hash": str(getattr(episode, "task_contract_hash", "")),
        "model_provider": str(getattr(episode, "model_provider", "") or ""),
        "model_name": str(getattr(episode, "model_name", "") or ""),
        "prompt_version": str(getattr(episode, "prompt_version", "") or ""),
        "tool_schema_version": str(getattr(episode, "tool_schema_version", "")),
        "steps": safe_trace_steps(list(getattr(episode, "steps", None) or [])),
        "grounded_fact_refs": public_fact_refs(
            list(getattr(episode, "grounded_facts", None) or [])
        ),
        "final_result_hash": canonical_hash(getattr(episode, "final_result", None) or {}),
        "hard_failures": safe_failures(list(getattr(episode, "hard_failures", None) or [])),
        "telemetry_summary": {
            "model_call_count": len(telemetry),
            "input_tokens": int(getattr(episode, "input_tokens", 0) or 0),
            "output_tokens": int(getattr(episode, "output_tokens", 0) or 0),
            "total_tokens": int(getattr(episode, "total_tokens", 0) or 0),
            "latency_ms": int(getattr(episode, "latency_ms", 0) or 0),
        },
        "business_outcome_hash": canonical_hash(business_outcome) if business_outcome else None,
        "human_feedback_hash": canonical_hash(human_feedback) if human_feedback else None,
        "replay_parent_id": getattr(episode, "replay_parent_id", None),
        "trace_version": str(getattr(episode, "trace_version", "1")),
        "redaction_version": str(getattr(episode, "redaction_version", REDACTION_VERSION)),
    }


def build_replay_fixture(
    episode: Any,
    *,
    candidate_sha: str,
    tool_schema_digest: str,
) -> dict[str, Any]:
    """Build a hash-only fixture; only read/none steps can be replayable."""

    steps: list[dict[str, Any]] = []
    for step in safe_trace_steps(list(getattr(episode, "steps", None) or [])):
        replayable = bool(step.get("replayable"))
        steps.append(
            {
                "sequence": int(step.get("sequence", len(steps))),
                "tool": str(step.get("tool") or ""),
                "schema_version": str(step.get("schema_version") or ""),
                "argument_hash": str(step.get("argument_hash") or ""),
                "sanitized_arguments": None,
                "expected_result_hash": str(step.get("result_hash") or ""),
                "sanitized_observation": None,
                "replayable": replayable,
                "non_replayable_reason": None if replayable else "tool_capability_not_read_only",
            }
        )
    return {
        "schema_version": REPLAY_SCHEMA_VERSION,
        "replay_mode": REPLAY_MODE,
        "source_episode_id": str(getattr(episode, "id", "")),
        "source_candidate_sha": candidate_sha,
        "tool_schema_digest": tool_schema_digest,
        "task_contract_hash": str(getattr(episode, "task_contract_hash", "")),
        "steps": steps,
        "expected_final_result_hash": canonical_hash(getattr(episode, "final_result", None) or {}),
        "redaction_version": str(getattr(episode, "redaction_version", REDACTION_VERSION)),
    }


def verify_replay_fixture(fixture: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    forbidden_keys = {
        "message",
        "prompt",
        "raw_user_message",
        "arguments",
        "observation",
        "system_prompt",
        "developer_prompt",
        "api_key",
        "password",
        "cookie",
        "authorization",
        "database_url",
    }
    failures.extend(f"forbidden_key:{key}" for key in sorted(forbidden_keys.intersection(fixture)))
    if fixture.get("schema_version") != REPLAY_SCHEMA_VERSION:
        failures.append("schema_version")
    if fixture.get("replay_mode") != REPLAY_MODE:
        failures.append("replay_mode")
    if not isinstance(fixture.get("source_candidate_sha"), str) or not re.fullmatch(
        r"[0-9a-f]{40}", fixture["source_candidate_sha"]
    ):
        failures.append("source_candidate_sha")
    if not isinstance(fixture.get("tool_schema_digest"), str) or not re.fullmatch(
        r"[0-9a-f]{64}", fixture["tool_schema_digest"]
    ):
        failures.append("tool_schema_digest")
    sequences = [step.get("sequence") for step in fixture.get("steps") or []]
    if sequences != list(range(len(sequences))):
        failures.append("step_order")
    for step in fixture.get("steps") or []:
        if not isinstance(step, dict) or not step.get("tool"):
            failures.append("step_tool")
            continue
        if step.get("sanitized_arguments") is not None:
            failures.append(f"arguments_not_redacted:{step.get('tool', '')}")
        if step.get("sanitized_observation") is not None:
            failures.append(f"observation_not_redacted:{step.get('tool', '')}")
        for key in ("argument_hash", "expected_result_hash"):
            value = str(step.get(key) or "")
            if not re.fullmatch(r"[0-9a-f]{64}", value):
                failures.append(f"{key}:{step.get('tool', '')}")
        if step.get("replayable") is False and not step.get("non_replayable_reason"):
            failures.append(f"non_replayable_reason:{step.get('tool', '')}")
    if len(str(fixture.get("expected_final_result_hash") or "")) != 64:
        failures.append("expected_final_result_hash")
    return failures
