"""Authenticated spatial queries and owner-scoped ROS2 simulation missions."""

import uuid
from typing import Any, Literal

from fastapi import APIRouter, Depends
from pydantic import Field

from app.api.deps import DB, CurrentUser, require_any
from app.core.config import settings
from app.schemas.spatial import MissionPlan, MissionRequest, SpatialContract, SpatialMapSnapshot
from app.schemas.spatial_readiness import MapDeltaProposal, ObservationFrame, TrajectorySegment
from app.services.spatial_poll_diagnostics import poll_mark
from app.services.spatial_transport import SpatialRobotTransport

router = APIRouter(
    prefix="/spatial",
    tags=["空间任务"],
    dependencies=[Depends(require_any("material:view", "location:manage", "picking:view"))],
)


class QueryRequest(SpatialContract):
    map_id: str = "MB-EMB-LAB-03"
    query: dict[str, Any]


class CreateMission(SpatialContract):
    request: MissionRequest
    conversation_id: str | None = None
    client_operation_id: str = Field(
        default_factory=lambda: uuid.uuid4().hex, min_length=8, max_length=100
    )


class HandoffRequest(SpatialContract):
    goal_id: str
    scan_code: str = Field(min_length=1, max_length=100)


class ObstacleRequest(SpatialContract):
    operation: Literal["add", "remove"]
    id: str = Field(default="blocked-crossing", min_length=1, max_length=100)
    scenario_id: Literal["blocked-crossing", "isolated-dock"] = "blocked-crossing"


@router.get("/maps/{map_id}/snapshot", response_model=SpatialMapSnapshot)
def snapshot(map_id: str, db: DB, user: CurrentUser):
    from app.spatial import SpatialMapService

    return SpatialMapService(db).snapshot(map_id)


@router.post("/query")
def spatial_query(body: QueryRequest, db: DB, user: CurrentUser):
    from app.spatial import SpatialMapService

    return SpatialMapService(db).query(body.map_id, body.query)


@router.post("/missions/plan", response_model=MissionPlan)
def mission_plan(body: MissionRequest, db: DB, user: CurrentUser):
    from app.services.spatial_mission import plan_mission
    from app.spatial import SpatialMapService

    return plan_mission(SpatialMapService(db).snapshot(body.map_id), body.model_dump(mode="json"))


@router.get("/navigation/health")
def navigation_health(user: CurrentUser):
    return SpatialRobotTransport(settings.spatial_robot_bridge_url).request("GET", "/health")


@router.get("/skills")
def spatial_skills(user: CurrentUser):
    from app.agent.spatial_agent import skill_manifest

    return {"skills": skill_manifest(), "inventory_written": False, "hardware_control": False}


@router.post("/missions")
def create_mission(body: CreateMission, db: DB, user: CurrentUser):
    from app.agent.spatial_integration import SpatialAgentIntegration

    return SpatialAgentIntegration(db, user).create_mission(
        body.request.model_dump(mode="json"),
        conversation_id=body.conversation_id,
        operation_id=body.client_operation_id,
    )


@router.get("/missions/{mission_id}")
def read_mission(mission_id: str, db: DB, user: CurrentUser):
    from app.agent.spatial_integration import SpatialAgentIntegration

    poll_mark("route_entry")
    return SpatialAgentIntegration(db, user).poll(mission_id)


@router.post("/missions/{mission_id}/start")
def start_mission(mission_id: str, db: DB, user: CurrentUser):
    from app.agent.spatial_integration import SpatialAgentIntegration

    return SpatialAgentIntegration(db, user).command(mission_id, "start")


@router.post("/missions/{mission_id}/cancel")
def cancel_mission(mission_id: str, db: DB, user: CurrentUser):
    from app.agent.spatial_integration import SpatialAgentIntegration

    return SpatialAgentIntegration(db, user).command(mission_id, "cancel")


class ReplanRequest(SpatialContract):
    profile: Literal["fastest", "safest", "esd_safe"] | None = None


@router.post("/missions/{mission_id}/replan")
def replan_mission(body: ReplanRequest, mission_id: str, db: DB, user: CurrentUser):
    from app.agent.spatial_integration import SpatialAgentIntegration

    return SpatialAgentIntegration(db, user).command(mission_id, "replan", body.model_dump())


@router.post("/missions/{mission_id}/handoff")
def handoff(body: HandoffRequest, mission_id: str, db: DB, user: CurrentUser):
    from app.agent.spatial_integration import SpatialAgentIntegration

    return SpatialAgentIntegration(db, user).command(mission_id, "handoff", body.model_dump())


@router.post("/missions/{mission_id}/obstacles")
def obstacle(body: ObstacleRequest, mission_id: str, db: DB, user: CurrentUser):
    from app.agent.spatial_integration import SpatialAgentIntegration

    return SpatialAgentIntegration(db, user).command(mission_id, "obstacles", body.model_dump())


@router.get("/missions/{mission_id}/episode")
def export_mission(mission_id: str, db: DB, user: CurrentUser):
    from app.agent.spatial_integration import SpatialAgentIntegration

    return SpatialAgentIntegration(db, user).episode(mission_id)


@router.get("/readiness/contracts")
def readiness_contracts(user: CurrentUser):
    return {
        "ObservationFrame": ObservationFrame.model_json_schema(),
        "MapDeltaProposal": MapDeltaProposal.model_json_schema(),
        "TrajectorySegment": TrajectorySegment.model_json_schema(),
        "automatic_map_write": False,
        "multi_robot_runtime": False,
        "training_executed": False,
    }


@router.get("/missions/{mission_id}/readiness-data")
def mission_readiness(mission_id: str, db: DB, user: CurrentUser):
    from app.services.spatial_mission_store import SpatialMissionStore
    from app.services.spatial_readiness_export import observed_readiness_bundle

    _, graph = SpatialMissionStore(db, user).find(mission_id)
    return observed_readiness_bundle(graph)


@router.get("/benchmarks")
def benchmarks(db: DB, user: CurrentUser):
    from app.services.spatial_benchmark import benchmark_summary
    from app.spatial import SpatialMapService

    return benchmark_summary(SpatialMapService(db).snapshot("MB-EMB-LAB-03"))


@router.post("/readiness/validate-map-delta")
def validate_map_delta(body: MapDeltaProposal, db: DB, user: CurrentUser):
    from app.services.spatial_readiness import MapUpdateValidator
    from app.spatial import SpatialMapService

    return MapUpdateValidator().validate(body, SpatialMapService(db).snapshot(body.map_id))
