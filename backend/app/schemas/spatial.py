"""Versioned, server-owned Spatial Agent transport contracts."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class SpatialContract(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class SpatialPose(SpatialContract):
    x: float
    y: float
    yaw: float = 0


class MissionConstraints(SpatialContract):
    payload_capacity_kg: float = Field(default=18, gt=0, le=1000)
    battery_pct: float = Field(default=100, ge=0, le=100)
    battery_reserve_pct: float = Field(default=15, ge=0, le=100)
    robot_class: str = "MB-R01"


class MissionStop(SpatialContract):
    goal_id: str = Field(min_length=1, max_length=100)
    service_s: float = Field(default=5, ge=0, le=86400)
    demand_kg: float = Field(default=0, ge=0, le=1000)
    priority: int = Field(default=0, ge=0, le=100)
    time_window_s: tuple[float, float] | None = None

    @model_validator(mode="after")
    def ordered_window(self):
        if self.time_window_s and not 0 <= self.time_window_s[0] <= self.time_window_s[1]:
            raise ValueError("time window must be ordered and nonnegative")
        return self


class MissionRequest(SpatialContract):
    map_id: str = "MB-EMB-LAB-03"
    map_revision: str
    profile: Literal["fastest", "safest", "esd_safe"] = "fastest"
    start_pose: SpatialPose
    goal_ids: list[str] = Field(min_length=1, max_length=32)
    completed_goal_ids: list[str] = Field(default_factory=list, max_length=32)
    stops: list[MissionStop] = Field(default_factory=list, max_length=32)
    constraints: MissionConstraints = Field(default_factory=MissionConstraints)
    return_home: bool = True
    dynamic_overlays: list[dict[str, Any]] = Field(default_factory=list, max_length=32)


class SpatialMapSnapshot(SpatialContract):
    schema_version: Literal[1] = 1
    map_id: str
    revision: str
    frame: dict[str, Any]
    geometry: dict[str, Any]
    zones: list[dict[str, Any]]
    route_graph: dict[str, Any]
    docks: list[dict[str, Any]]
    affordances: list[dict[str, Any]]
    dynamic_overlays: list[dict[str, Any]] = Field(default_factory=list)
    provenance: dict[str, Any] | str


class MissionPlan(SpatialContract):
    schema_version: Literal[1] = 1
    mission_id: str
    map_id: str
    map_revision: str
    profile: Literal["fastest", "safest", "esd_safe"]
    status: Literal["READY", "INFEASIBLE", "CLARIFICATION"]
    stops: list[dict[str, Any]]
    ordered_goal_ids: list[str]
    completed_goal_ids: list[str] = Field(default_factory=list)
    constraints: dict[str, Any]
    solver: dict[str, Any]
    metrics: dict[str, Any]
    objective_terms: dict[str, Any]
    segments: list[dict[str, Any]]
    violations: list[dict[str, Any] | str] = Field(default_factory=list)


class ExecutionEvent(SpatialContract):
    schema_version: Literal[1] = 1
    mission_id: str
    event_id: str = Field(min_length=1, max_length=160)
    sequence: int = Field(ge=0)
    map_revision: str
    timestamp: str
    type: str
    goal_id: str | None = None
    pose: SpatialPose | None = None
    battery_pct: float | None = Field(default=None, ge=0, le=100)
    payload_kg: float | None = Field(default=None, ge=0)
    details: dict[str, Any] = Field(default_factory=dict)


class TaskGraph(SpatialContract):
    schema_version: Literal[1] = 1
    task_id: str
    conversation_id: str | None = None
    map_id: str
    map_revision: str
    mission_id: str | None = None
    nodes: list[dict[str, Any]]
    current_skill: str | None = None
    completed_goal_ids: list[str]
    remaining_goal_ids: list[str]
    robot_state: dict[str, Any]
    recovery: dict[str, Any] | None = None
    events: list[dict[str, Any]]
    last_valid_plan: dict[str, Any] | None = None
    status: str
