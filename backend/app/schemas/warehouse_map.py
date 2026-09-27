from __future__ import annotations

from pydantic import BaseModel, Field


class WarehouseRoutePreviewRequest(BaseModel):
    location_ids: list[int] = Field(min_length=1, max_length=200)
    closed_edge_codes: list[str] = Field(default_factory=list, max_length=100)


class WarehouseMapStatusUpdate(BaseModel):
    calibration_status: str


class WarehouseMapDraftCloneRequest(BaseModel):
    code: str = Field(min_length=2, max_length=64)
    version: str = Field(min_length=1, max_length=32)
    name: str = Field(min_length=1, max_length=200)


class WarehouseMapNodeInput(BaseModel):
    code: str = Field(min_length=1, max_length=64)
    node_type: str
    x_m: float
    y_m: float
    label: str = ""
    is_active: bool = True


class WarehouseMapEdgeInput(BaseModel):
    code: str = Field(min_length=1, max_length=64)
    from_node: str = Field(min_length=1, max_length=64)
    to_node: str = Field(min_length=1, max_length=64)
    distance_m: float = Field(gt=0)
    bidirectional: bool = True
    enabled: bool = True
    notes: str = ""


class WarehouseMapBindingInput(BaseModel):
    location_code: str = Field(min_length=1, max_length=64)
    pick_node_code: str = Field(min_length=1, max_length=64)
    x_m: float
    y_m: float
    width_m: float = Field(gt=0)
    depth_m: float = Field(gt=0)
    rotation_deg: float = 0
    facing: str = "aisle"
    local_geometry_kind: str = "organizer"


class WarehouseMapDraftDefinitionUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    width_m: float = Field(gt=0)
    height_m: float = Field(gt=0)
    default_start_node: str = Field(min_length=1, max_length=64)
    default_end_node: str | None = None
    geometry_note: str = ""
    nodes: list[WarehouseMapNodeInput] = Field(min_length=1, max_length=1000)
    edges: list[WarehouseMapEdgeInput] = Field(min_length=1, max_length=3000)
    organizer_bindings: list[WarehouseMapBindingInput] = Field(
        default_factory=list, max_length=1000
    )


class WarehouseMapCalibrationUpdate(BaseModel):
    calibration_status: str
    geometry_note: str | None = None


class WarehouseMapActivateRequest(BaseModel):
    confirm_verified_geometry: bool = False
