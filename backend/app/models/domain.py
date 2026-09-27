from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Role(Base, TimestampMixin):
    __tablename__ = "roles"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    description: Mapped[str] = mapped_column(String(255), default="")
    permissions: Mapped[list[str]] = mapped_column(JSON, default=list)
    is_system: Mapped[bool] = mapped_column(Boolean, default=False)


class User(Base, TimestampMixin):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(100))
    password_hash: Mapped[str] = mapped_column(String(255))
    department: Mapped[str] = mapped_column(String(100), default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=True)
    failed_attempts: Mapped[int] = mapped_column(default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    role_id: Mapped[int] = mapped_column(ForeignKey("roles.id"))
    role: Mapped[Role] = relationship()


class Session(Base):
    __tablename__ = "sessions"
    id: Mapped[int] = mapped_column(primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    csrf_token: Mapped[str] = mapped_column(String(64))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    user: Mapped[User] = relationship()


class Category(Base, TimestampMixin):
    __tablename__ = "categories"
    id: Mapped[int] = mapped_column(primary_key=True)
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("categories.id"))
    name: Mapped[str] = mapped_column(String(100))
    code: Mapped[str] = mapped_column(String(64), unique=True)
    sort_order: Mapped[int] = mapped_column(default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class Location(Base, TimestampMixin):
    __tablename__ = "locations"
    id: Mapped[int] = mapped_column(primary_key=True)
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("locations.id"))
    code: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(100))
    type: Mapped[str] = mapped_column(String(32), default="bin")
    full_path: Mapped[str] = mapped_column(String(500))
    manager: Mapped[str] = mapped_column(String(100), default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    organizer_style: Mapped[str | None] = mapped_column(String(32))
    organizer_left_module: Mapped[str | None] = mapped_column(String(16))
    organizer_right_module: Mapped[str | None] = mapped_column(String(16))
    bin_material_name: Mapped[str] = mapped_column(String(200), default="")
    bin_quantity: Mapped[int | None] = mapped_column()
    bin_content_notes: Mapped[str] = mapped_column(Text, default="")


class Supplier(Base, TimestampMixin):
    __tablename__ = "suppliers"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    contact: Mapped[str] = mapped_column(String(100), default="")
    phone: Mapped[str] = mapped_column(String(50), default="")
    email: Mapped[str] = mapped_column(String(200), default="")
    lead_time_days: Mapped[int] = mapped_column(default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class Material(Base, TimestampMixin):
    __tablename__ = "materials"
    __table_args__ = (
        CheckConstraint("quantity >= 0", name="ck_material_quantity_nonnegative"),
        CheckConstraint("reserved_quantity >= 0", name="ck_material_reserved_nonnegative"),
        CheckConstraint("reserved_quantity <= quantity", name="ck_material_reserved_lte_quantity"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200), index=True)
    category_id: Mapped[int | None] = mapped_column(ForeignKey("categories.id"))
    location_id: Mapped[int | None] = mapped_column(ForeignKey("locations.id"))
    supplier_id: Mapped[int | None] = mapped_column(ForeignKey("suppliers.id"))
    mpn: Mapped[str] = mapped_column(String(200), default="", index=True)
    specification: Mapped[str] = mapped_column(String(300), default="")
    package: Mapped[str] = mapped_column(String(100), default="")
    footprint: Mapped[str] = mapped_column(String(100), default="")
    manufacturer: Mapped[str] = mapped_column(String(200), default="")
    supplier_part_number: Mapped[str] = mapped_column(String(200), default="")
    unit: Mapped[str] = mapped_column(String(20), default="pcs")
    unit_price: Mapped[Decimal] = mapped_column(Numeric(14, 4), default=Decimal("0"))
    safety_stock: Mapped[Decimal] = mapped_column(Numeric(14, 4), default=Decimal("0"))
    target_stock: Mapped[Decimal] = mapped_column(Numeric(14, 4), default=Decimal("0"))
    quantity: Mapped[Decimal] = mapped_column(Numeric(14, 4), default=Decimal("0"))
    reserved_quantity: Mapped[Decimal] = mapped_column(Numeric(14, 4), default=Decimal("0"))
    barcode: Mapped[str] = mapped_column(String(200), default="")
    lifecycle_status: Mapped[str] = mapped_column(String(32), default="active")
    rohs_status: Mapped[str] = mapped_column(String(32), default="unknown")
    datasheet_url: Mapped[str] = mapped_column(String(500), default="")
    tags: Mapped[list[str]] = mapped_column(JSON, default=list)
    attributes: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    notes: Mapped[str] = mapped_column(Text, default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False)
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    updated_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    category: Mapped[Category | None] = relationship()
    location: Mapped[Location | None] = relationship()
    supplier: Mapped[Supplier | None] = relationship()

    @property
    def available_quantity(self) -> Decimal:
        return self.quantity - self.reserved_quantity


class InventoryLot(Base, TimestampMixin):
    __tablename__ = "inventory_lots"
    __table_args__ = (
        UniqueConstraint("material_id", "location_id", name="uq_lot_material_location"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    material_id: Mapped[int] = mapped_column(ForeignKey("materials.id"), index=True)
    location_id: Mapped[int] = mapped_column(ForeignKey("locations.id"), index=True)
    quantity: Mapped[Decimal] = mapped_column(Numeric(14, 4), default=Decimal("0"))


class WarehouseMap(Base, TimestampMixin):
    __tablename__ = "warehouse_maps"
    __table_args__ = (
        UniqueConstraint("warehouse_location_id", "version", name="uq_warehouse_map_version"),
        CheckConstraint(
            "status IN ('draft', 'active', 'archived')",
            name="ck_warehouse_map_status",
        ),
        CheckConstraint(
            "calibration_status IN ('demo_synthetic', 'measured', 'verified')",
            name="ck_warehouse_map_calibration_status",
        ),
        Index("ix_warehouse_map_warehouse_status", "warehouse_location_id", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    warehouse_location_id: Mapped[int] = mapped_column(
        ForeignKey("locations.id"), index=True
    )
    code: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200))
    version: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16), default="draft", index=True)
    calibration_status: Mapped[str] = mapped_column(
        String(24), default="demo_synthetic", index=True
    )
    coordinate_unit: Mapped[str] = mapped_column(String(8), default="m")
    width_m: Mapped[Decimal] = mapped_column(Numeric(10, 3))
    height_m: Mapped[Decimal] = mapped_column(Numeric(10, 3))
    default_start_node_code: Mapped[str] = mapped_column(String(64), default="PACK")
    default_end_node_code: Mapped[str] = mapped_column(String(64), default="PACK")
    graph_hash: Mapped[str] = mapped_column(String(64), index=True)
    geometry_note: Mapped[str] = mapped_column(Text, default="")
    verified_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), index=True)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class WarehouseMapNode(Base, TimestampMixin):
    __tablename__ = "warehouse_map_nodes"
    __table_args__ = (
        UniqueConstraint("warehouse_map_id", "code", name="uq_warehouse_map_node_code"),
        CheckConstraint(
            "node_type IN ('packing', 'entrance', 'aisle', 'intersection', 'pick_face')",
            name="ck_warehouse_map_node_type",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    warehouse_map_id: Mapped[int] = mapped_column(
        ForeignKey("warehouse_maps.id", ondelete="CASCADE"), index=True
    )
    code: Mapped[str] = mapped_column(String(64))
    node_type: Mapped[str] = mapped_column(String(24), index=True)
    label: Mapped[str] = mapped_column(String(200), default="")
    x_m: Mapped[Decimal] = mapped_column(Numeric(10, 3))
    y_m: Mapped[Decimal] = mapped_column(Numeric(10, 3))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)


class WarehouseMapEdge(Base, TimestampMixin):
    __tablename__ = "warehouse_map_edges"
    __table_args__ = (
        UniqueConstraint("warehouse_map_id", "code", name="uq_warehouse_map_edge_code"),
        CheckConstraint("distance_m > 0", name="ck_warehouse_map_edge_distance_positive"),
        CheckConstraint("from_node_id <> to_node_id", name="ck_warehouse_map_edge_distinct_nodes"),
        Index("ix_warehouse_map_edge_map_active", "warehouse_map_id", "is_active"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    warehouse_map_id: Mapped[int] = mapped_column(
        ForeignKey("warehouse_maps.id", ondelete="CASCADE"), index=True
    )
    code: Mapped[str] = mapped_column(String(64))
    from_node_id: Mapped[int] = mapped_column(ForeignKey("warehouse_map_nodes.id"), index=True)
    to_node_id: Mapped[int] = mapped_column(ForeignKey("warehouse_map_nodes.id"), index=True)
    distance_m: Mapped[Decimal] = mapped_column(Numeric(11, 4))
    bidirectional: Mapped[bool] = mapped_column(Boolean, default=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    notes: Mapped[str] = mapped_column(Text, default="")


class LocationMapBinding(Base, TimestampMixin):
    __tablename__ = "location_map_bindings"
    __table_args__ = (
        UniqueConstraint("warehouse_map_id", "location_id", name="uq_location_map_binding"),
        CheckConstraint("width_m > 0", name="ck_location_map_binding_width_positive"),
        CheckConstraint("depth_m > 0", name="ck_location_map_binding_depth_positive"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    warehouse_map_id: Mapped[int] = mapped_column(
        ForeignKey("warehouse_maps.id", ondelete="CASCADE"), index=True
    )
    location_id: Mapped[int] = mapped_column(ForeignKey("locations.id"), index=True)
    pick_node_id: Mapped[int] = mapped_column(ForeignKey("warehouse_map_nodes.id"), index=True)
    x_m: Mapped[Decimal] = mapped_column(Numeric(10, 3))
    y_m: Mapped[Decimal] = mapped_column(Numeric(10, 3))
    width_m: Mapped[Decimal] = mapped_column(Numeric(10, 3))
    depth_m: Mapped[Decimal] = mapped_column(Numeric(10, 3))
    rotation_deg: Mapped[Decimal] = mapped_column(Numeric(7, 2), default=Decimal("0"))
    facing: Mapped[str] = mapped_column(String(16), default="aisle")
    local_geometry_kind: Mapped[str] = mapped_column(String(32), default="organizer")


class Project(Base, TimestampMixin):
    __tablename__ = "projects"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    manager_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    status: Mapped[str] = mapped_column(String(32), default="planning")
    start_date: Mapped[date | None] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date)
    notes: Mapped[str] = mapped_column(Text, default="")
    members: Mapped[list[int]] = mapped_column(JSON, default=list)
    product_revision_id: Mapped[int | None] = mapped_column(
        ForeignKey("product_revisions.id", ondelete="SET NULL"), index=True
    )


class Product(Base, TimestampMixin):
    __tablename__ = "products"
    __table_args__ = (
        CheckConstraint(
            "lifecycle_status IN ('active', 'archived')",
            name="ck_product_lifecycle_status",
        ),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200), index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    lifecycle_status: Mapped[str] = mapped_column(String(32), default="active", index=True)


class ProductRevision(Base, TimestampMixin):
    __tablename__ = "product_revisions"
    __table_args__ = (
        UniqueConstraint("product_id", "revision", name="uq_product_revision"),
        CheckConstraint(
            "status IN ('draft', 'released', 'obsolete')",
            name="ck_product_revision_status",
        ),
        Index("ix_product_revision_product_status", "product_id", "status"),
        Index(
            "uq_product_revisions_one_default",
            "product_id",
            unique=True,
            postgresql_where=text("is_default = true"),
            sqlite_where=text("is_default = 1"),
        ),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), index=True)
    revision: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), default="draft")
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    notes: Mapped[str] = mapped_column(Text, default="")
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    bom_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    released_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))


class ProductBomItem(Base, TimestampMixin):
    __tablename__ = "product_bom_items"
    __table_args__ = (
        UniqueConstraint(
            "product_revision_id",
            "material_id",
            name="uq_product_bom_item",
        ),
        CheckConstraint(
            "quantity_per_unit > 0",
            name="ck_product_bom_qty_positive",
        ),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    product_revision_id: Mapped[int] = mapped_column(ForeignKey("product_revisions.id"), index=True)
    material_id: Mapped[int] = mapped_column(ForeignKey("materials.id"), index=True)
    quantity_per_unit: Mapped[Decimal] = mapped_column(Numeric(14, 4))
    notes: Mapped[str] = mapped_column(Text, default="")


class ComponentRelation(Base, TimestampMixin):
    """Human-reviewed engineering evidence between a canonical Material pair."""

    __tablename__ = "component_relations"
    __table_args__ = (
        CheckConstraint(
            "source_material_id < target_material_id",
            name="ck_component_relation_canonical_materials",
        ),
        CheckConstraint(
            "relation_type IN ('similar_to', 'electrical_compatible', "
            "'pin_compatible', 'same_footprint')",
            name="ck_component_relation_type",
        ),
        CheckConstraint(
            "status IN ('candidate', 'validated', 'rejected', 'revoked')",
            name="ck_component_relation_status",
        ),
        UniqueConstraint(
            "source_material_id",
            "target_material_id",
            "relation_type",
            name="uq_component_relation",
        ),
        Index(
            "ix_component_relation_source_status",
            "source_material_id",
            "status",
        ),
        Index(
            "ix_component_relation_target_status",
            "target_material_id",
            "status",
        ),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    source_material_id: Mapped[int] = mapped_column(ForeignKey("materials.id"), index=True)
    target_material_id: Mapped[int] = mapped_column(ForeignKey("materials.id"), index=True)
    relation_type: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(24), default="candidate", index=True)
    confidence_note: Mapped[str] = mapped_column(Text, default="")
    evidence_summary: Mapped[str] = mapped_column(Text, default="")
    evidence_refs: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    created_by_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    reviewed_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), index=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    validated_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    validated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rejected_reason: Mapped[str] = mapped_column(Text, default="")
    revoked_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_reason: Mapped[str] = mapped_column(Text, default="")


class ProductBomAlternate(Base, TimestampMixin):
    """Qualification scoped to one immutable ProductRevision BOM position."""

    __tablename__ = "product_bom_alternates"
    __table_args__ = (
        CheckConstraint(
            "status IN ('candidate', 'approved', 'rejected', 'revoked')",
            name="ck_product_bom_alternate_status",
        ),
        CheckConstraint(
            "priority > 0",
            name="ck_product_bom_alternate_priority_positive",
        ),
        UniqueConstraint(
            "product_bom_item_id",
            "alternate_material_id",
            name="uq_product_bom_alternate",
        ),
        Index(
            "ix_product_bom_alternate_item_status",
            "product_bom_item_id",
            "status",
        ),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    product_bom_item_id: Mapped[int] = mapped_column(
        ForeignKey("product_bom_items.id", ondelete="CASCADE"), index=True
    )
    alternate_material_id: Mapped[int] = mapped_column(ForeignKey("materials.id"), index=True)
    status: Mapped[str] = mapped_column(String(24), default="candidate", index=True)
    priority: Mapped[int] = mapped_column(default=100)
    usage_condition: Mapped[str] = mapped_column(Text, default="")
    engineering_note: Mapped[str] = mapped_column(Text, default="")
    evidence_refs: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    source_component_relation_id: Mapped[int | None] = mapped_column(
        ForeignKey("component_relations.id", ondelete="SET NULL"), index=True
    )
    created_by_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    reviewed_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), index=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approved_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rejected_reason: Mapped[str] = mapped_column(Text, default="")
    revoked_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_reason: Mapped[str] = mapped_column(Text, default="")


class EngineeringDocument(Base, TimestampMixin):
    """Immutable identity and revision metadata for traceable engineering evidence."""

    __tablename__ = "engineering_documents"
    __table_args__ = (
        CheckConstraint(
            "scope_type IN ('material', 'product_revision')",
            name="ck_engineering_document_scope_type",
        ),
        CheckConstraint(
            "document_type IN ('datasheet', 'errata', 'application_note', "
            "'engineering_note', 'synthetic_test')",
            name="ck_engineering_document_type",
        ),
        CheckConstraint(
            "status IN ('current', 'superseded', 'withdrawn')",
            name="ck_engineering_document_status",
        ),
        CheckConstraint(
            "ingest_status IN ('pending', 'ready', 'failed')",
            name="ck_engineering_document_ingest_status",
        ),
        CheckConstraint(
            "((scope_type = 'material' AND material_id IS NOT NULL "
            "AND product_revision_id IS NULL) OR "
            "(scope_type = 'product_revision' AND product_revision_id IS NOT NULL "
            "AND material_id IS NULL))",
            name="ck_engineering_document_scope_owner",
        ),
        UniqueConstraint("file_sha256", name="uq_engineering_document_file_sha256"),
        Index(
            "ix_engineering_document_material_status",
            "material_id",
            "status",
            "ingest_status",
        ),
        Index(
            "ix_engineering_document_revision_status",
            "product_revision_id",
            "status",
            "ingest_status",
        ),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    document_key: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    scope_type: Mapped[str] = mapped_column(String(32))
    material_id: Mapped[int | None] = mapped_column(ForeignKey("materials.id"), index=True)
    product_revision_id: Mapped[int | None] = mapped_column(
        ForeignKey("product_revisions.id"), index=True
    )
    document_type: Mapped[str] = mapped_column(String(32))
    title: Mapped[str] = mapped_column(String(300))
    manufacturer: Mapped[str] = mapped_column(String(200), default="")
    document_revision: Mapped[str] = mapped_column(String(80), default="")
    document_date: Mapped[date | None] = mapped_column(Date)
    source_type: Mapped[str] = mapped_column(String(32), default="upload")
    source_url: Mapped[str] = mapped_column(String(1000), default="")
    original_filename: Mapped[str] = mapped_column(String(300), default="")
    storage_key: Mapped[str] = mapped_column(String(500), default="")
    file_sha256: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    page_count: Mapped[int] = mapped_column(default=0)
    status: Mapped[str] = mapped_column(String(24), default="current", index=True)
    supersedes_document_id: Mapped[int | None] = mapped_column(
        ForeignKey("engineering_documents.id")
    )
    ingest_status: Mapped[str] = mapped_column(String(24), default="pending", index=True)
    ingest_error: Mapped[str] = mapped_column(Text, default="")
    extraction_version: Mapped[str] = mapped_column(String(64), default="")
    created_by_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)


class EngineeringDocumentPage(Base, TimestampMixin):
    __tablename__ = "engineering_document_pages"
    __table_args__ = (
        UniqueConstraint("document_id", "page_number", name="uq_engineering_document_page"),
        CheckConstraint("page_number > 0", name="ck_engineering_document_page_positive"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(
        ForeignKey("engineering_documents.id", ondelete="CASCADE"), index=True
    )
    page_number: Mapped[int] = mapped_column()
    text_content: Mapped[str] = mapped_column(Text, default="")
    text_sha256: Mapped[str] = mapped_column(String(64), index=True)
    native_text_quality: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    ocr_status: Mapped[str] = mapped_column(String(32), default="not_assessed", index=True)


class EngineeringDocumentBlock(Base, TimestampMixin):
    """Additive layout index; never a replacement for EvidenceAnchor facts."""

    __tablename__ = "engineering_document_blocks"
    __table_args__ = (
        UniqueConstraint(
            "document_page_id",
            "block_index",
            "extractor_version",
            name="uq_engineering_document_block_version",
        ),
        CheckConstraint(
            "block_type IN ('heading', 'paragraph', 'table', 'pin_description', "
            "'application_circuit', 'unparsed')",
            name="ck_engineering_document_block_type",
        ),
        CheckConstraint("reading_order > 0", name="ck_engineering_document_block_reading_order"),
        CheckConstraint(
            "location_status IN ('available', 'location_unavailable')",
            name="ck_engineering_document_block_location_status",
        ),
        Index("ix_engineering_document_blocks_page_order", "document_page_id", "reading_order"),
        Index("ix_engineering_document_blocks_source_sha", "source_sha256"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    document_page_id: Mapped[int] = mapped_column(
        ForeignKey("engineering_document_pages.id", ondelete="CASCADE"), index=True
    )
    block_index: Mapped[int] = mapped_column()
    block_type: Mapped[str] = mapped_column(String(32))
    reading_order: Mapped[int] = mapped_column()
    text_content: Mapped[str] = mapped_column(Text, default="")
    text_sha256: Mapped[str] = mapped_column(String(64), index=True)
    bbox: Mapped[list[float] | None] = mapped_column(JSON)
    location_status: Mapped[str] = mapped_column(String(32), default="location_unavailable")
    provenance: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    extractor_version: Mapped[str] = mapped_column(String(64))
    source_sha256: Mapped[str] = mapped_column(String(64), index=True)


class EvidenceAnchor(Base, TimestampMixin):
    __tablename__ = "evidence_anchors"
    __table_args__ = (
        UniqueConstraint(
            "document_page_id",
            "excerpt_sha256",
            name="uq_evidence_anchor_page_excerpt",
        ),
        CheckConstraint(
            "anchor_source IN ('extracted', 'deterministic_parser', 'human')",
            name="ck_evidence_anchor_source",
        ),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    document_page_id: Mapped[int] = mapped_column(
        ForeignKey("engineering_document_pages.id", ondelete="CASCADE"), index=True
    )
    section_title: Mapped[str] = mapped_column(String(300), default="")
    excerpt_text: Mapped[str] = mapped_column(Text)
    excerpt_sha256: Mapped[str] = mapped_column(String(64), index=True)
    structured_fact: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    anchor_source: Mapped[str] = mapped_column(String(32), default="extracted")
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))


class ComponentRelationEvidenceLink(Base, TimestampMixin):
    __tablename__ = "component_relation_evidence_links"
    __table_args__ = (
        UniqueConstraint(
            "component_relation_id",
            "evidence_anchor_id",
            name="uq_component_relation_evidence",
        ),
        CheckConstraint(
            "role IN ('supporting', 'contradicting', 'context')",
            name="ck_component_relation_evidence_role",
        ),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    component_relation_id: Mapped[int] = mapped_column(
        ForeignKey("component_relations.id", ondelete="CASCADE"), index=True
    )
    evidence_anchor_id: Mapped[int] = mapped_column(
        ForeignKey("evidence_anchors.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(String(24), default="supporting")
    review_note: Mapped[str] = mapped_column(Text, default="")


class ProductBomAlternateEvidenceLink(Base, TimestampMixin):
    __tablename__ = "product_bom_alternate_evidence_links"
    __table_args__ = (
        UniqueConstraint(
            "product_bom_alternate_id",
            "evidence_anchor_id",
            name="uq_product_bom_alternate_evidence",
        ),
        CheckConstraint(
            "role IN ('supporting', 'contradicting', 'context')",
            name="ck_product_bom_alternate_evidence_role",
        ),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    product_bom_alternate_id: Mapped[int] = mapped_column(
        ForeignKey("product_bom_alternates.id", ondelete="CASCADE"), index=True
    )
    evidence_anchor_id: Mapped[int] = mapped_column(
        ForeignKey("evidence_anchors.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(String(24), default="supporting")
    review_note: Mapped[str] = mapped_column(Text, default="")


class BomItem(Base, TimestampMixin):
    __tablename__ = "bom_items"
    __table_args__ = (UniqueConstraint("project_id", "version", "material_id", name="uq_bom_item"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    version: Mapped[str] = mapped_column(String(32), default="V1")
    material_id: Mapped[int] = mapped_column(ForeignKey("materials.id"))
    required_quantity: Mapped[Decimal] = mapped_column(Numeric(14, 4))
    notes: Mapped[str] = mapped_column(Text, default="")


class ProjectReservation(Base, TimestampMixin):
    __tablename__ = "project_reservations"
    __table_args__ = (
        UniqueConstraint("project_id", "material_id", name="uq_project_material_reservation"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    material_id: Mapped[int] = mapped_column(ForeignKey("materials.id"), index=True)
    quantity: Mapped[Decimal] = mapped_column(Numeric(14, 4), default=Decimal("0"))
    consumed_quantity: Mapped[Decimal] = mapped_column(Numeric(14, 4), default=Decimal("0"))


class Loan(Base, TimestampMixin):
    __tablename__ = "loans"
    id: Mapped[int] = mapped_column(primary_key=True)
    loan_no: Mapped[str] = mapped_column(String(64), unique=True)
    material_id: Mapped[int] = mapped_column(ForeignKey("materials.id"), index=True)
    borrower_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    quantity: Mapped[Decimal] = mapped_column(Numeric(14, 4))
    returned_quantity: Mapped[Decimal] = mapped_column(Numeric(14, 4), default=Decimal("0"))
    due_date: Mapped[date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(32), default="borrowed")
    notes: Mapped[str] = mapped_column(Text, default="")


class Stocktake(Base, TimestampMixin):
    __tablename__ = "stocktakes"
    id: Mapped[int] = mapped_column(primary_key=True)
    stocktake_no: Mapped[str] = mapped_column(String(64), unique=True)
    material_id: Mapped[int] = mapped_column(ForeignKey("materials.id"))
    book_quantity: Mapped[Decimal] = mapped_column(Numeric(14, 4))
    actual_quantity: Mapped[Decimal] = mapped_column(Numeric(14, 4))
    difference: Mapped[Decimal] = mapped_column(Numeric(14, 4))
    reason: Mapped[str] = mapped_column(Text)
    operator_id: Mapped[int] = mapped_column(ForeignKey("users.id"))


class StockMovement(Base):
    __tablename__ = "stock_movements"
    id: Mapped[int] = mapped_column(primary_key=True)
    movement_no: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    material_id: Mapped[int] = mapped_column(ForeignKey("materials.id"), index=True)
    operation_type: Mapped[str] = mapped_column(String(40), index=True)
    quantity_delta: Mapped[Decimal] = mapped_column(Numeric(14, 4))
    before_quantity: Mapped[Decimal] = mapped_column(Numeric(14, 4))
    after_quantity: Mapped[Decimal] = mapped_column(Numeric(14, 4))
    before_reserved: Mapped[Decimal] = mapped_column(Numeric(14, 4))
    after_reserved: Mapped[Decimal] = mapped_column(Numeric(14, 4))
    operator_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id"))
    loan_id: Mapped[int | None] = mapped_column(ForeignKey("loans.id"))
    source_location_id: Mapped[int | None] = mapped_column(ForeignKey("locations.id"))
    target_location_id: Mapped[int | None] = mapped_column(ForeignKey("locations.id"))
    reversal_of_id: Mapped[int | None] = mapped_column(ForeignKey("stock_movements.id"))
    reason: Mapped[str] = mapped_column(Text)
    notes: Mapped[str] = mapped_column(Text, default="")
    request_id: Mapped[str] = mapped_column(String(64), index=True)
    idempotency_key: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )


class IdempotencyRecord(Base):
    __tablename__ = "idempotency_records"
    __table_args__ = (UniqueConstraint("user_id", "endpoint", "key", name="uq_idempotency_scope"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    endpoint: Mapped[str] = mapped_column(String(100))
    key: Mapped[str] = mapped_column(String(100))
    response: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id: Mapped[int] = mapped_column(primary_key=True)
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), index=True)
    action: Mapped[str] = mapped_column(String(100), index=True)
    resource_type: Mapped[str] = mapped_column(String(64))
    resource_id: Mapped[str] = mapped_column(String(64), default="")
    before_data: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    after_data: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    ip_address: Mapped[str] = mapped_column(String(64), default="")
    request_id: Mapped[str] = mapped_column(String(64), index=True)
    success: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )


class AgentActionProposal(Base, TimestampMixin):
    __tablename__ = "agent_action_proposals"
    __table_args__ = (
        CheckConstraint(
            "action_type IN ('reserve_inventory')",
            name="ck_agent_proposal_action_type",
        ),
        CheckConstraint(
            "status IN ('pending', 'approved', 'rejected', 'executed', 'failed', 'expired')",
            name="ck_agent_proposal_status",
        ),
        UniqueConstraint(
            "created_by_id",
            "client_operation_id",
            "action_type",
            name="uq_agent_proposal_business_operation",
        ),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    proposal_no: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    action_type: Mapped[str] = mapped_column(String(40), index=True)
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    reason: Mapped[str] = mapped_column(Text, default="")
    created_by_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    approved_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    request_id: Mapped[str] = mapped_column(String(64), index=True)
    client_operation_id: Mapped[str] = mapped_column(String(64), index=True)
    payload_hash: Mapped[str] = mapped_column(String(64), index=True)
    execution_result: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    error_message: Mapped[str] = mapped_column(Text, default="")
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class BuildPlan(Base, TimestampMixin):
    __tablename__ = "build_plans"
    __table_args__ = (
        CheckConstraint(
            "status IN ('ready', 'reservation_pending', 'reserved', 'stale', 'cancelled')",
            name="ck_build_plan_status",
        ),
        CheckConstraint("build_quantity > 0", name="ck_build_plan_quantity_positive"),
        UniqueConstraint(
            "created_by_id",
            "client_operation_id",
            name="uq_build_plan_business_operation",
        ),
        UniqueConstraint(
            "reservation_proposal_id",
            name="uq_build_plan_reservation_proposal",
        ),
        Index("ix_build_plan_project_status", "project_id", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    plan_no: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    product_revision_id: Mapped[int] = mapped_column(ForeignKey("product_revisions.id"), index=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    build_quantity: Mapped[int]
    product_bom_hash: Mapped[str] = mapped_column(String(64))
    snapshot_hash: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(32), default="ready", index=True)
    reservation_proposal_id: Mapped[int | None] = mapped_column(
        ForeignKey("agent_action_proposals.id"), index=True
    )
    created_by_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    source_request_id: Mapped[str] = mapped_column(String(64), default="")
    client_operation_id: Mapped[str] = mapped_column(String(128))
    notes: Mapped[str] = mapped_column(Text, default="")
    reserved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    stale_reason: Mapped[str] = mapped_column(Text, default="")


class BuildPlanItem(Base, TimestampMixin):
    __tablename__ = "build_plan_items"
    __table_args__ = (
        UniqueConstraint(
            "build_plan_id",
            "material_id",
            name="uq_build_plan_material",
        ),
        CheckConstraint(
            "quantity_per_unit > 0",
            name="ck_build_plan_item_qpu_positive",
        ),
        CheckConstraint(
            "required_total > 0",
            name="ck_build_plan_item_required_positive",
        ),
        CheckConstraint(
            "additional_reservation_required >= 0",
            name="ck_build_plan_item_additional_nonnegative",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    build_plan_id: Mapped[int] = mapped_column(
        ForeignKey("build_plans.id", ondelete="CASCADE"), index=True
    )
    material_id: Mapped[int] = mapped_column(ForeignKey("materials.id"), index=True)
    quantity_per_unit: Mapped[Decimal] = mapped_column(Numeric(14, 4))
    required_total: Mapped[Decimal] = mapped_column(Numeric(14, 4))
    available_quantity_at_plan: Mapped[Decimal] = mapped_column(Numeric(14, 4))
    reserved_for_project_at_plan: Mapped[Decimal] = mapped_column(Numeric(14, 4))
    additional_reservation_required: Mapped[Decimal] = mapped_column(Numeric(14, 4))
    projected_free_available_after_build: Mapped[Decimal] = mapped_column(Numeric(14, 4))
    safety_stock_at_plan: Mapped[Decimal] = mapped_column(Numeric(14, 4))
    below_safety_after_build: Mapped[bool] = mapped_column(Boolean)
    material_status_at_plan: Mapped[str] = mapped_column(String(32), default="active")


class PickTask(Base, TimestampMixin):
    __tablename__ = "pick_tasks"
    __table_args__ = (
        CheckConstraint(
            "status IN ('ready', 'in_progress', 'needs_replan', 'completed', 'cancelled', 'stale')",
            name="ck_pick_task_status",
        ),
        UniqueConstraint(
            "created_by_id",
            "client_operation_id",
            name="uq_pick_task_business_operation",
        ),
        Index("ix_pick_task_build_plan_status", "build_plan_id", "status"),
        Index("ix_pick_task_project_status", "project_id", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    pick_task_no: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    build_plan_id: Mapped[int] = mapped_column(ForeignKey("build_plans.id"), index=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    product_revision_id: Mapped[int] = mapped_column(
        ForeignKey("product_revisions.id"), index=True
    )
    build_plan_snapshot_hash: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(24), default="ready", index=True)
    route_strategy: Mapped[str] = mapped_column(String(32), default="hierarchy_v1")
    warehouse_map_id: Mapped[int | None] = mapped_column(
        ForeignKey("warehouse_maps.id", ondelete="SET NULL"), index=True
    )
    warehouse_graph_hash: Mapped[str] = mapped_column(String(64), default="", index=True)
    route_distance_m: Mapped[Decimal | None] = mapped_column(Numeric(12, 3))
    route_start_node_code: Mapped[str] = mapped_column(String(64), default="")
    route_end_node_code: Mapped[str] = mapped_column(String(64), default="")
    route_constraints: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    route_plan: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_by_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    client_operation_id: Mapped[str] = mapped_column(String(128))
    source_request_id: Mapped[str] = mapped_column(String(64), default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PickTaskItem(Base, TimestampMixin):
    __tablename__ = "pick_task_items"
    __table_args__ = (
        UniqueConstraint("pick_task_id", "material_id", name="uq_pick_task_material"),
        CheckConstraint("required_quantity > 0", name="ck_pick_item_required_positive"),
        CheckConstraint(
            "allocated_quantity >= 0", name="ck_pick_item_allocated_nonnegative"
        ),
        CheckConstraint("picked_quantity >= 0", name="ck_pick_item_picked_nonnegative"),
        CheckConstraint(
            "allocated_quantity <= required_quantity",
            name="ck_pick_item_allocated_lte_required",
        ),
        CheckConstraint(
            "picked_quantity <= allocated_quantity",
            name="ck_pick_item_picked_lte_allocated",
        ),
        CheckConstraint(
            "status IN ('pending', 'partial', 'picked', 'cancelled')",
            name="ck_pick_item_status",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    pick_task_id: Mapped[int] = mapped_column(
        ForeignKey("pick_tasks.id", ondelete="CASCADE"), index=True
    )
    build_plan_item_id: Mapped[int] = mapped_column(
        ForeignKey("build_plan_items.id"), index=True
    )
    material_id: Mapped[int] = mapped_column(ForeignKey("materials.id"), index=True)
    required_quantity: Mapped[Decimal] = mapped_column(Numeric(14, 4))
    allocated_quantity: Mapped[Decimal] = mapped_column(Numeric(14, 4))
    picked_quantity: Mapped[Decimal] = mapped_column(
        Numeric(14, 4), default=Decimal("0")
    )
    reservation_quantity_at_task: Mapped[Decimal] = mapped_column(Numeric(14, 4))
    locatable_quantity_at_task: Mapped[Decimal] = mapped_column(Numeric(14, 4))
    status: Mapped[str] = mapped_column(String(24), default="pending", index=True)


class PickAllocation(Base, TimestampMixin):
    __tablename__ = "pick_allocations"
    __table_args__ = (
        UniqueConstraint(
            "pick_task_item_id", "inventory_lot_id", "generation", name="uq_pick_item_lot"
        ),
        CheckConstraint("generation > 0", name="ck_pick_allocation_generation_positive"),
        CheckConstraint(
            "planned_quantity > 0", name="ck_pick_allocation_planned_positive"
        ),
        CheckConstraint(
            "picked_quantity >= 0", name="ck_pick_allocation_picked_nonnegative"
        ),
        CheckConstraint(
            "picked_quantity <= planned_quantity",
            name="ck_pick_allocation_picked_lte_planned",
        ),
        CheckConstraint(
            "route_sequence > 0", name="ck_pick_allocation_route_positive"
        ),
        CheckConstraint(
            "status IN ('pending', 'partial', 'picked', 'cancelled', 'stale')",
            name="ck_pick_allocation_status",
        ),
        CheckConstraint(
            "confirmation_method IN ('none', 'manual', 'barcode', 'qr')",
            name="ck_pick_allocation_confirmation_method",
        ),
        Index("ix_pick_allocation_lot_status", "inventory_lot_id", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    pick_task_item_id: Mapped[int] = mapped_column(
        ForeignKey("pick_task_items.id", ondelete="CASCADE"), index=True
    )
    inventory_lot_id: Mapped[int] = mapped_column(
        ForeignKey("inventory_lots.id"), index=True
    )
    location_id: Mapped[int] = mapped_column(ForeignKey("locations.id"), index=True)
    planned_quantity: Mapped[Decimal] = mapped_column(Numeric(14, 4))
    picked_quantity: Mapped[Decimal] = mapped_column(
        Numeric(14, 4), default=Decimal("0")
    )
    route_sequence: Mapped[int] = mapped_column(index=True)
    route_key: Mapped[str] = mapped_column(String(600), default="")
    route_node_code: Mapped[str] = mapped_column(String(64), default="")
    route_distance_from_previous_m: Mapped[Decimal | None] = mapped_column(Numeric(12, 3))
    generation: Mapped[int] = mapped_column(default=1, server_default="1")
    status: Mapped[str] = mapped_column(String(24), default="pending", index=True)
    confirmation_method: Mapped[str] = mapped_column(String(16), default="none")
    confirmed_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), index=True
    )
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AgentEpisode(Base):
    __tablename__ = "agent_episodes"
    __table_args__ = (
        CheckConstraint(
            "status IN ('success', 'error', 'blocked')",
            name="ck_agent_episode_status",
        ),
        Index("ix_agent_episode_user_created", "user_id", "created_at"),
        Index("ix_agent_episode_request", "request_id"),
        Index("ix_agent_episode_conversation", "conversation_id"),
        Index("ix_agent_episode_status", "status"),
        Index("ix_agent_episode_replay_parent", "replay_parent_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    request_id: Mapped[str] = mapped_column(String(64))
    conversation_id: Mapped[str] = mapped_column(String(36), default="")
    client_operation_id: Mapped[str] = mapped_column(String(64), default="")
    entry_surface: Mapped[str] = mapped_column(String(32))
    execution_mode: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(24))
    task_contract: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    task_contract_hash: Mapped[str] = mapped_column(String(64), default="")
    model_provider: Mapped[str] = mapped_column(String(64), default="")
    model_name: Mapped[str] = mapped_column(String(128), default="")
    prompt_version: Mapped[str] = mapped_column(String(64), default="")
    tool_schema_version: Mapped[str] = mapped_column(String(64), default="")
    steps: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    grounded_facts: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    final_result: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    hard_failures: Mapped[list[str]] = mapped_column(JSON, default=list)
    telemetry: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    input_tokens: Mapped[int] = mapped_column(default=0)
    output_tokens: Mapped[int] = mapped_column(default=0)
    total_tokens: Mapped[int] = mapped_column(default=0)
    latency_ms: Mapped[int] = mapped_column(default=0)
    business_outcome: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    human_feedback: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    replay_parent_id: Mapped[str | None] = mapped_column(String(36))
    trace_version: Mapped[str] = mapped_column(String(16), default="1")
    redaction_version: Mapped[str] = mapped_column(String(16), default="1")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AgentConversationContext(Base, TimestampMixin):
    """Bounded, server-owned business context for Warehouse Agent turns."""

    __tablename__ = "agent_conversation_contexts"
    __table_args__ = (
        CheckConstraint(
            "status IN ('active', 'closed')",
            name="ck_agent_conversation_status",
        ),
        Index("ix_agent_conversation_owner_status", "user_id", "status"),
        Index("ix_agent_conversation_owner_lookup", "user_id", "id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    status: Mapped[str] = mapped_column(String(16), default="active", index=True)
    context_version: Mapped[int] = mapped_column(default=0)
    selected_material_id: Mapped[int | None] = mapped_column(ForeignKey("materials.id"), index=True)
    selected_project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id"), index=True)
    selected_bom_version: Mapped[str | None] = mapped_column(String(32))
    selected_product_id: Mapped[int | None] = mapped_column(
        ForeignKey("products.id", ondelete="SET NULL"), index=True
    )
    selected_product_revision_id: Mapped[int | None] = mapped_column(
        ForeignKey("product_revisions.id", ondelete="SET NULL"), index=True
    )
    material_candidate_ids: Mapped[list[int]] = mapped_column(JSON, default=list)
    project_candidate_ids: Mapped[list[int]] = mapped_column(JSON, default=list)
    product_candidate_ids: Mapped[list[int]] = mapped_column(JSON, default=list)
    pending_disambiguation: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    last_entity_kind: Mapped[str] = mapped_column(String(32), default="unknown")
    last_intent: Mapped[str] = mapped_column(String(64), default="")
    last_requested_facts: Mapped[list[str]] = mapped_column(JSON, default=list)
    last_build_quantity: Mapped[int | None]
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class Attachment(Base):
    __tablename__ = "attachments"
    id: Mapped[int] = mapped_column(primary_key=True)
    material_id: Mapped[int | None] = mapped_column(ForeignKey("materials.id"), index=True)
    original_name: Mapped[str] = mapped_column(String(255))
    stored_name: Mapped[str] = mapped_column(String(255), unique=True)
    mime_type: Mapped[str] = mapped_column(String(100))
    size: Mapped[int]
    sha256: Mapped[str] = mapped_column(String(64))
    uploaded_by_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PurchaseOrder(Base, TimestampMixin):
    __tablename__ = "purchase_orders"
    id: Mapped[int] = mapped_column(primary_key=True)
    order_no: Mapped[str] = mapped_column(String(64), unique=True)
    supplier_id: Mapped[int] = mapped_column(ForeignKey("suppliers.id"))
    status: Mapped[str] = mapped_column(String(32), default="draft")
    items: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    total_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0"))
    expected_date: Mapped[date | None] = mapped_column(Date)
    created_by_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
