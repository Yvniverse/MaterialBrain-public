from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class LoginRequest(BaseModel):
    username: str = Field(min_length=2, max_length=64)
    password: str = Field(min_length=1, max_length=200)


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str = Field(min_length=6, max_length=200)


class RoleOut(ORMModel):
    id: int
    name: str
    description: str
    permissions: list[str]
    is_system: bool


class UserOut(ORMModel):
    id: int
    username: str
    full_name: str
    department: str
    is_active: bool
    must_change_password: bool
    role: RoleOut
    created_at: datetime


class UserCreate(BaseModel):
    username: str = Field(pattern=r"^[A-Za-z0-9_.-]{2,64}$")
    full_name: str = Field(min_length=1, max_length=100)
    department: str = Field(default="", max_length=100)
    role_id: int
    password: str = Field(min_length=6, max_length=200)


class UserUpdate(BaseModel):
    full_name: str | None = Field(default=None, min_length=1, max_length=100)
    department: str | None = Field(default=None, max_length=100)
    role_id: int | None = None
    is_active: bool | None = None


class ResetPasswordRequest(BaseModel):
    new_password: str = Field(min_length=6, max_length=200)


class RoleCreate(BaseModel):
    name: str = Field(min_length=2, max_length=64)
    description: str = Field(default="", max_length=255)
    permissions: list[str] = Field(default_factory=list)


class CategoryData(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    code: str = Field(min_length=1, max_length=64)
    parent_id: int | None = None
    sort_order: int = 0
    is_active: bool = True


class LocationData(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    code: str = Field(min_length=1, max_length=64)
    parent_id: int | None = None
    type: str = Field(default="bin", max_length=32)
    manager: str = Field(default="", max_length=100)
    notes: str = ""
    is_active: bool = True


class OrganizerCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    code: str = Field(min_length=1, max_length=54)
    parent_id: int | None = None
    manager: str = Field(default="", max_length=100)
    notes: str = ""
    is_active: bool = True
    organizer_style: Literal[
        "standard_56", "split_configurable", "drawer_rack_100", "shelf_rack_6"
    ] = "standard_56"
    organizer_left_module: Literal["small", "large"] = "small"
    organizer_right_module: Literal["small", "large"] = "small"


class OrganizerLayoutUpdate(BaseModel):
    organizer_left_module: Literal["small", "large"]
    organizer_right_module: Literal["small", "large"]


class BinContentUpdate(BaseModel):
    material_name: str = Field(min_length=1, max_length=200)
    quantity: int | None = Field(default=None, ge=0)
    notes: str = Field(default="", max_length=2000)

    @field_validator("material_name")
    @classmethod
    def strip_material_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("物料名称不能为空")
        return value


class ShelfStorageBoxCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    notes: str = Field(default="", max_length=2000)

    @field_validator("name")
    @classmethod
    def strip_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("箱子名称不能为空")
        return value


class CableData(BaseModel):
    name: str = Field(default="", max_length=200)
    model: str = Field(default="", max_length=200)
    cable_kind: Literal["terminal", "flat_flex", "micro_coax", "rf_coax"] = "terminal"
    end_style: Literal[
        "double",
        "single",
        "single_tinned",
        "male_female_pair",
        "unspecified",
    ] = "double"
    connector_a: str = Field(default="", max_length=100)
    connector_b: str = Field(default="", max_length=100)
    connector_pitch_mm: Decimal | None = Field(
        default=None,
        gt=0,
        le=100,
        max_digits=6,
        decimal_places=2,
    )
    direction: Literal["same", "reverse", "unspecified"] = "same"
    length_cm: Decimal = Field(
        gt=0,
        le=10000,
        max_digits=8,
        decimal_places=2,
    )
    pin_count: int = Field(default=0, ge=0, le=1000)
    pin_count_b: int = Field(default=0, ge=0, le=1000)
    pin_layout: str = Field(default="", max_length=50)
    quantity: int = Field(default=0, ge=0, le=1_000_000_000)
    storage_location: str = Field(default="", max_length=200)
    notes: str = Field(default="", max_length=2000)
    idempotency_key: str = Field(min_length=8, max_length=100)

    @field_validator(
        "name",
        "model",
        "connector_a",
        "connector_b",
        "pin_layout",
        "storage_location",
        "notes",
    )
    @classmethod
    def strip_cable_text(cls, value: str) -> str:
        return value.strip()

    @model_validator(mode="after")
    def validate_required_spec(self):
        if self.cable_kind != "rf_coax":
            if self.connector_pitch_mm is None:
                raise ValueError("非射频同轴线必须填写接头间距")
            if self.pin_count <= 0:
                raise ValueError("非射频同轴线必须填写 Pin 数")
        return self


class CableQuantityChange(BaseModel):
    delta: int = Field(ge=-1_000_000_000, le=1_000_000_000)
    idempotency_key: str = Field(min_length=8, max_length=100)

    @field_validator("delta")
    @classmethod
    def reject_zero_delta(cls, value: int) -> int:
        if value == 0:
            raise ValueError("库存变化数量不能为 0")
        return value


class CableImportSourceItem(BaseModel):
    key: str = Field(min_length=16, max_length=64)
    identity: str = Field(default="", max_length=64)
    item_id: str = Field(default="", max_length=100)
    variant: str = Field(default="", max_length=1000)
    quantity: int = Field(ge=1, le=1_000_000_000)
    order_no: str = Field(default="", max_length=100)
    source_row: int = Field(ge=2, le=1_000_000)
    unit_price: str = Field(default="", max_length=50)
    product_url: str = Field(default="", max_length=1000)


class CableImportRow(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    model: str = Field(default="", max_length=200)
    cable_kind: Literal["terminal", "flat_flex", "micro_coax", "rf_coax"] = "terminal"
    end_style: Literal[
        "double",
        "single",
        "single_tinned",
        "male_female_pair",
        "unspecified",
    ] = "double"
    connector_a: str = Field(default="", max_length=100)
    connector_b: str = Field(default="", max_length=100)
    connector_pitch_mm: Decimal | None = Field(
        default=None,
        gt=0,
        le=100,
        max_digits=6,
        decimal_places=2,
    )
    direction: Literal["same", "reverse", "unspecified"] = "unspecified"
    length_cm: Decimal = Field(
        gt=0,
        le=10000,
        max_digits=8,
        decimal_places=2,
    )
    pin_count: int = Field(default=0, ge=0, le=1000)
    pin_count_b: int = Field(default=0, ge=0, le=1000)
    pin_layout: str = Field(default="", max_length=50)
    quantity: int = Field(ge=1, le=1_000_000_000)
    unit_price: Decimal | None = Field(default=None, ge=0)
    storage_location: str = Field(default="", max_length=200)
    notes: str = Field(default="", max_length=2000)
    shop: str = Field(default="", max_length=200)
    raw_product_name: str = Field(default="", max_length=1000)
    raw_variant: str = Field(default="", max_length=1000)
    source_rows: list[int] = Field(default_factory=list, max_length=1000)
    source_items: list[CableImportSourceItem] = Field(min_length=1, max_length=1000)

    @field_validator(
        "name",
        "model",
        "connector_a",
        "connector_b",
        "pin_layout",
        "storage_location",
        "notes",
        "shop",
        "raw_product_name",
        "raw_variant",
    )
    @classmethod
    def strip_cable_import_text(cls, value: str) -> str:
        return value.strip()

    @model_validator(mode="after")
    def validate_required_import_spec(self):
        if self.cable_kind != "rf_coax":
            if self.connector_pitch_mm is None:
                raise ValueError("非射频同轴线必须填写接头间距")
            if self.pin_count <= 0:
                raise ValueError("非射频同轴线必须填写 Pin 数")
        return self


class CableImportCommit(BaseModel):
    rows: list[CableImportRow] = Field(min_length=1, max_length=5000)
    idempotency_key: str = Field(min_length=8, max_length=100)


class SupplierData(BaseModel):
    code: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=200)
    contact: str = ""
    phone: str = ""
    email: str = ""
    lead_time_days: int = Field(default=0, ge=0)
    is_active: bool = True


class MaterialBase(BaseModel):
    code: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=200)
    category_id: int | None = None
    location_id: int | None = None
    supplier_id: int | None = None
    mpn: str = ""
    specification: str = ""
    package: str = ""
    footprint: str = ""
    manufacturer: str = ""
    supplier_part_number: str = ""
    unit: str = "pcs"
    unit_price: Decimal = Field(default=Decimal("0"), ge=0)
    safety_stock: Decimal = Field(default=Decimal("0"), ge=0)
    target_stock: Decimal = Field(default=Decimal("0"), ge=0)
    barcode: str = ""
    lifecycle_status: str = "active"
    rohs_status: str = "unknown"
    datasheet_url: str = ""
    tags: list[str] = Field(default_factory=list)
    attributes: dict[str, Any] = Field(default_factory=dict)
    notes: str = ""
    is_active: bool = True


class MaterialCreate(MaterialBase):
    pass


class MaterialUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, min_length=1, max_length=200)
    category_id: int | None = None
    location_id: int | None = None
    supplier_id: int | None = None
    mpn: str | None = None
    specification: str | None = None
    package: str | None = None
    footprint: str | None = None
    manufacturer: str | None = None
    supplier_part_number: str | None = None
    unit: str | None = None
    unit_price: Decimal | None = Field(default=None, ge=0)
    safety_stock: Decimal | None = Field(default=None, ge=0)
    target_stock: Decimal | None = Field(default=None, ge=0)
    barcode: str | None = None
    lifecycle_status: str | None = None
    rohs_status: str | None = None
    datasheet_url: str | None = None
    tags: list[str] | None = None
    attributes: dict[str, Any] | None = None
    notes: str | None = None
    is_active: bool | None = None


class MaterialOut(MaterialBase):
    model_config = ConfigDict(from_attributes=True)
    id: int
    quantity: Decimal
    reserved_quantity: Decimal
    available_quantity: Decimal
    created_at: datetime
    updated_at: datetime


class InventoryOperation(BaseModel):
    material_id: int
    quantity: Decimal = Field(gt=0, max_digits=14, decimal_places=0)
    idempotency_key: str = Field(min_length=8, max_length=100)
    reason: str = Field(min_length=2, max_length=1000)
    notes: str = Field(default="", max_length=2000)
    project_id: int | None = None
    source_location_id: int | None = None
    target_location_id: int | None = None
    movement_id: int | None = None


class AdjustOperation(BaseModel):
    material_id: int
    actual_quantity: Decimal = Field(ge=0, max_digits=14, decimal_places=0)
    idempotency_key: str = Field(min_length=8, max_length=100)
    reason: str = Field(min_length=2, max_length=1000)
    notes: str = ""


class MovementOut(ORMModel):
    id: int
    movement_no: str
    material_id: int
    operation_type: str
    quantity_delta: Decimal
    before_quantity: Decimal
    after_quantity: Decimal
    before_reserved: Decimal
    after_reserved: Decimal
    operator_id: int
    reason: str
    notes: str
    request_id: str
    created_at: datetime


class ProjectData(BaseModel):
    code: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=200)
    manager_id: int
    status: Literal["planning", "active", "paused", "completed", "archived"] = "planning"
    start_date: date | None = None
    end_date: date | None = None
    notes: str = ""
    members: list[int] = Field(default_factory=list)
    product_revision_id: int | None = Field(default=None, gt=0)


class BomData(BaseModel):
    material_id: int
    required_quantity: Decimal = Field(gt=0)
    version: str = "V1"
    notes: str = ""


class StocktakeData(BaseModel):
    material_id: int
    actual_quantity: Decimal = Field(ge=0)
    reason: str = Field(min_length=2)
    idempotency_key: str = Field(min_length=8, max_length=100)


class PurchaseOrderData(BaseModel):
    supplier_id: int
    items: list[dict[str, Any]] = Field(min_length=1)
    expected_date: date | None = None


class Page(BaseModel):
    items: list[Any]
    total: int
    page: int
    page_size: int


class ImportCommit(BaseModel):
    rows: list[dict[str, Any]] = Field(min_length=1, max_length=5000)

    @field_validator("rows")
    @classmethod
    def validate_rows(cls, rows: list[dict[str, Any]]):
        for row in rows:
            if not row.get("code") or not row.get("name"):
                raise ValueError("每行必须包含 code 和 name")
            try:
                quantity = Decimal(str(row.get("quantity") or 0))
            except (ValueError, ArithmeticError) as exc:
                raise ValueError("库存数量必须是整数") from exc
            if quantity < 0 or quantity != quantity.to_integral_value():
                raise ValueError("库存数量必须是大于或等于 0 的整数")
        return rows
