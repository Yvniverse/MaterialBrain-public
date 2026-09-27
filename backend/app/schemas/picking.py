from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field


class PickTaskCreateRequest(BaseModel):
    build_plan_id: int = Field(gt=0)
    client_operation_id: str = Field(min_length=8, max_length=128)
    notes: str = Field(default="", max_length=4000)
    build_plan_snapshot_hash: str = Field(min_length=64, max_length=64)
    closed_edge_codes: list[str] = Field(default_factory=list, max_length=100)


class PickTaskReplanRequest(BaseModel):
    client_operation_id: str = Field(min_length=8, max_length=128)
    reason: str = Field(min_length=2, max_length=1000)
    closed_edge_codes: list[str] = Field(default_factory=list, max_length=100)


class PickTaskCancelRequest(BaseModel):
    reason: str = Field(min_length=2, max_length=1000)


class PickAllocationConfirmRequest(BaseModel):
    quantity: Decimal = Field(gt=0, max_digits=14, decimal_places=4)
    idempotency_key: str = Field(min_length=8, max_length=100)
    confirmation_method: Literal["manual", "barcode", "qr"]
    location_token: str = Field(min_length=1, max_length=300)
    material_token: str = Field(min_length=1, max_length=300)
    notes: str = Field(default="", max_length=2000)
    manual_override_reason: str = Field(default="", max_length=1000)


class PickScanValidateRequest(BaseModel):
    location_token: str = Field(default="", max_length=300)
    material_token: str = Field(default="", max_length=300)


class PickIssueRequest(BaseModel):
    issue_type: Literal[
        "location_blocked",
        "stock_shortage",
        "label_unreadable",
        "damaged_material",
        "other",
    ]
    allocation_id: int | None = Field(default=None, gt=0)
    notes: str = Field(min_length=2, max_length=1000)
