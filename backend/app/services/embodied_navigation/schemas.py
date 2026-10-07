from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Pose(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    x: float = Field(ge=0, le=24)
    y: float = Field(ge=0, le=18)
    yaw: float = Field(default=0, ge=-100, le=100)


class PlanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    world_id: Literal["MB-EMB-LAB-03"]
    goal_ids: list[str] = Field(min_length=0, max_length=14)
    scenario_id: Literal["baseline", "blocked-crossing", "isolated-dock", "low-battery"] = (
        "baseline"
    )
    start: Pose | None = None
    payload_kg: float = Field(default=0, ge=0, le=100)
    battery_pct: float | None = Field(default=None, ge=0, le=100)
    optimize: bool = True
    world_revision: str | None = None
    mode: Literal["simulation"] = "simulation"


class NavigationExecutionContext(BaseModel):
    """Bounded simulation state; never authorizes a physical or inventory action."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    world_id: Literal["MB-EMB-LAB-03"]
    world_revision: str = Field(min_length=64, max_length=64)
    goal_ids: list[str] = Field(min_length=0, max_length=14)
    completed_goal_ids: list[str] = Field(default_factory=list, max_length=14)
    pose: Pose
    payload_kg: float = Field(ge=0, le=100)
    battery_pct: float = Field(ge=0, le=100)
    scenario_id: Literal["baseline", "blocked-crossing", "isolated-dock", "low-battery"] = (
        "baseline"
    )
    state: Literal[
        "READY", "PAUSED", "BLOCKED", "REPLANNING", "WAITING_HANDOFF", "COMPLETED", "CANCELLED"
    ]
    mode: Literal["simulation"] = "simulation"
