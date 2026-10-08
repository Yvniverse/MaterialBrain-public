"""P4 observations and proposals are versioned inputs, never automatic map writes."""

from datetime import datetime
from typing import Any, Literal

from pydantic import Field

from app.schemas.spatial import SpatialContract, SpatialPose


class ObservationFrame(SpatialContract):
    observation_id: str
    timestamp: datetime
    map_id: str
    map_revision: str
    frame: Literal["warehouse_map"] = "warehouse_map"
    pose: SpatialPose
    tf: list[dict[str, Any]] = Field(default_factory=list, max_length=32)
    rgb_refs: list[str] = Field(default_factory=list, max_length=16)
    scan_refs: list[str] = Field(default_factory=list, max_length=16)
    pointcloud_refs: list[str] = Field(default_factory=list, max_length=16)
    semantic_detections: list[dict[str, Any]] = Field(default_factory=list, max_length=256)
    map_region: dict[str, Any] | None = None


class MapDeltaProposal(SpatialContract):
    proposal_id: str
    map_id: str
    base_revision: str
    frame: Literal["warehouse_map"] = "warehouse_map"
    geometry_changes: list[dict[str, Any]] = Field(default_factory=list, max_length=128)
    topology_changes: list[dict[str, Any]] = Field(default_factory=list, max_length=128)
    semantic_changes: list[dict[str, Any]] = Field(default_factory=list, max_length=128)
    confidence: float = Field(ge=0, le=1)
    evidence_refs: list[str] = Field(min_length=1, max_length=64)
    status: Literal["proposed", "accepted", "rejected"] = "proposed"


class TrajectorySample(SpatialContract):
    timestamp: datetime
    pose: SpatialPose
    linear_mps: float | None = None
    angular_rps: float | None = None
    velocity_source: Literal["observed", "unavailable"] = "unavailable"
    edge_id: str | None = None
    zone_ids: list[str] = Field(default_factory=list)
    dynamic_events: list[str] = Field(default_factory=list)


class TrajectorySegment(SpatialContract):
    segment_id: str
    mission_id: str
    skill: str
    map_id: str
    map_revision: str
    samples: list[TrajectorySample] = Field(min_length=1, max_length=10000)
