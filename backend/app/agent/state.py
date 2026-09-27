from typing import Any, TypedDict


class WarehouseAgentState(TypedDict):
    messages: list[dict[str, Any]]
    user_id: int
    request_id: str
    user_message: str
    intent: str | None
    tool_round: int
    tool_events: list[dict[str, Any]]
    pending_tool_calls: list[dict[str, Any]]
    entities: dict[str, Any]
    answer: str | None
    ui_actions: list[dict[str, Any]]
    proposal_ids: list[int]
    grounded_facts: list[dict[str, Any]]
    narrative: str
    telemetry: list[dict[str, Any]]
    model_call_count: int
    max_rounds_exceeded: bool
    deadline_exceeded: bool
    task_contract: dict[str, Any]
