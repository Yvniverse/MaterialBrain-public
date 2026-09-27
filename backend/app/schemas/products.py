from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field


class ProductCreate(BaseModel):
    code: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=4000)
    lifecycle_status: Literal["active", "archived"] = "active"


class ProductUpdate(BaseModel):
    code: str | None = Field(default=None, min_length=1, max_length=64)
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=4000)
    lifecycle_status: Literal["active", "archived"] | None = None


class ProductRevisionCreate(BaseModel):
    revision: str = Field(min_length=1, max_length=64)
    status: Literal["draft", "released", "obsolete"] = "draft"
    is_default: bool = False
    notes: str = Field(default="", max_length=4000)
    released_at: datetime | None = None


class ProductRevisionUpdate(BaseModel):
    revision: str | None = Field(default=None, min_length=1, max_length=64)
    status: Literal["draft", "released", "obsolete"] | None = None
    is_default: bool | None = None
    notes: str | None = Field(default=None, max_length=4000)
    released_at: datetime | None = None


class ProductBomItemCreate(BaseModel):
    material_id: int = Field(gt=0)
    quantity_per_unit: Decimal = Field(gt=0, max_digits=14, decimal_places=4)
    notes: str = Field(default="", max_length=4000)


class ProductBomItemUpdate(BaseModel):
    quantity_per_unit: Decimal | None = Field(
        default=None,
        gt=0,
        max_digits=14,
        decimal_places=4,
    )
    notes: str | None = Field(default=None, max_length=4000)


class ProductRevisionReleaseRequest(BaseModel):
    make_default: bool = False


class ProductRevisionCloneRequest(BaseModel):
    new_revision: str = Field(min_length=1, max_length=64)
    notes: str | None = Field(default=None, max_length=4000)


class BuildReadinessRequest(BaseModel):
    build_quantity: int = Field(gt=0, le=1_000_000)
    project_id: int | None = Field(default=None, gt=0)


class BuildPlanCreateRequest(BaseModel):
    product_revision_id: int = Field(gt=0)
    project_id: int = Field(gt=0)
    build_quantity: int = Field(gt=0, le=1_000_000)
    client_operation_id: str = Field(min_length=8, max_length=128)
    notes: str = Field(default="", max_length=4000)


class BuildPlanReservationRequest(BaseModel):
    client_operation_id: str = Field(min_length=8, max_length=64)
    reason: str = Field(
        default="根据构建计划创建待人工审批的物料预留",
        min_length=2,
        max_length=1000,
    )
