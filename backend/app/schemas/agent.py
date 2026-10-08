from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.services.embodied_navigation.schemas import NavigationExecutionContext


class AgentQueryRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    conversation_id: str | None = Field(default=None, max_length=100)
    navigation_context: NavigationExecutionContext | None = None
    client_operation_id: str | None = Field(
        default=None,
        min_length=8,
        max_length=64,
        pattern=r"^[A-Za-z0-9._:-]+$",
    )

    @field_validator("message")
    @classmethod
    def strip_message(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("消息不能为空")
        return value


class AgentToolEvent(BaseModel):
    tool: str
    status: Literal["running", "success", "error"]
    summary: str
    duration_ms: int = Field(ge=0)
    error_code: str | None = None


class AgentUIAction(BaseModel):
    type: Literal[
        "open_material",
        "open_cable",
        "focus_location",
        "open_project",
        "open_product",
        "request_approval",
    ]
    target_id: int | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class GroundedFact(BaseModel):
    kind: Literal[
        "inventory",
        "location",
        "bom",
        "product_bom",
        "build_readiness",
        "reconciliation",
        "proposal",
        "component_relation",
        "product_alternate",
        "engineering_evidence",
        "evidence_comparison",
        "cable",
        "power_design",
        "engineering_research",
        "navigation",
    ]
    source_tool: str
    entity_id: int | None = None
    field: str
    value: Any
    unit: str | None = None
    label: str


class LLMTelemetry(BaseModel):
    provider: str
    model: str
    finish_reason: str
    max_tokens: int | None = Field(default=None, ge=1)
    status: Literal["success", "error"] = "success"
    error_class: str | None = None
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    total_tokens: int = Field(ge=0)
    latency_ms: int = Field(ge=0)
    tool_call_count: int = Field(ge=0)
    attempts: int = Field(default=1, ge=1)
    retries: int = Field(default=0, ge=0)


class AgentQueryResponse(BaseModel):
    answer: str
    narrative: str = ""
    intent: str | None = None
    entities: dict[str, Any] = Field(default_factory=dict)
    grounded_facts: list[GroundedFact] = Field(default_factory=list)
    tool_events: list[AgentToolEvent] = Field(default_factory=list)
    ui_actions: list[AgentUIAction] = Field(default_factory=list)
    proposal_ids: list[int] = Field(default_factory=list)
    telemetry: list[LLMTelemetry] = Field(default_factory=list)
    execution_mode: Literal["deterministic", "llm_assisted"] = "deterministic"
    model_call_count: int = Field(default=0, ge=0)
    request_id: str
    conversation_id: str


class AgentSuggestionItem(BaseModel):
    type: Literal[
        "material_location",
        "material_inventory",
        "low_stock",
        "project_bom",
        "material_search",
    ]
    text: str = Field(min_length=1, max_length=300)
    material_id: int | None = Field(default=None, gt=0)
    project_id: int | None = Field(default=None, gt=0)


class AgentSuggestionsResponse(BaseModel):
    items: list[AgentSuggestionItem] = Field(max_length=5)
    generated_at: datetime
    source: Literal["database"] = "database"


class SearchMaterialsArgs(BaseModel):
    query: str = Field(min_length=1, max_length=200)
    limit: int = Field(default=10, ge=1, le=30)


class ComponentRequirementArgs(BaseModel):
    requirement: str = Field(min_length=2, max_length=1000)
    limit: int = Field(default=5, ge=1, le=10)


class PowerDesignArgs(BaseModel):
    requirement: str = Field(min_length=4, max_length=2000)


class EngineeringResearchPlan(BaseModel):
    workflow: Literal["engineering_research"] = "engineering_research"
    task_contract: dict[str, Any]
    requirements: dict[str, Any]
    steps: list[dict[str, Any]] = Field(default_factory=list)
    round: int = Field(default=1, ge=1, le=10)
    continuation: bool = False
    adaptive: dict[str, Any] = Field(default_factory=dict)
    focus_scope: Literal["primary", "peripheral", "bom_draft"] = "primary"
    selected_primary_material_id: int | None = None
    read_only: Literal[True] = True
    write_scope: Literal["none"] = "none"


class EngineeringResearchDraft(BaseModel):
    status: Literal["supported", "needs_constraints", "no_grounded_solution"]
    candidate_status: Literal["found", "not_found", "needs_constraints"] = "not_found"
    evidence_status: Literal["sufficient", "partial", "insufficient", "not_checked"] = "not_checked"
    draft_status: Literal["reviewable", "not_formed"] = "not_formed"
    conclusion: str
    buck: dict[str, Any] = Field(default_factory=dict)
    ldo: dict[str, Any] = Field(default_factory=dict)
    manual_review: list[str] = Field(default_factory=list)
    unknowns: list[str] = Field(default_factory=list)
    citations: list[dict[str, Any]] = Field(default_factory=list)
    focus_scope: Literal["primary", "peripheral", "bom_draft"] = "primary"
    selected_primary_material_id: int | None = None
    peripheral_requirements: list[dict[str, Any]] = Field(default_factory=list)
    engineering_bom_draft: dict[str, Any] = Field(default_factory=dict)
    completeness: dict[str, Any] = Field(default_factory=dict)
    topologies: list[dict[str, Any]] = Field(default_factory=list)
    rail_bom_draft: dict[str, Any] = Field(default_factory=dict)
    read_only: Literal[True] = True
    automatic_write: Literal[False] = False


class MaterialIdArgs(BaseModel):
    material_id: int = Field(gt=0)


class CableSearchArgs(BaseModel):
    query: str = Field(default="", max_length=500)
    cable_kind: Literal["terminal", "flat_flex", "micro_coax", "rf_coax"] | None = None
    connector_a: str | None = Field(default=None, max_length=100)
    connector_b: str | None = Field(default=None, max_length=100)
    connector_pitch_mm: Decimal | None = Field(default=None, gt=0, le=20)
    pin_count: int | None = Field(default=None, ge=1, le=200)
    pin_count_b: int | None = Field(default=None, ge=1, le=200)
    direction: Literal["same", "reverse", "unspecified"] | None = None
    end_style: (
        Literal["double", "single", "single_tinned", "male_female_pair", "unspecified"] | None
    ) = None
    length_cm: Decimal | None = Field(default=None, gt=0, le=1000)
    length_tolerance_cm: Decimal = Field(default=Decimal("10"), ge=0, le=1000)
    min_available_quantity: Decimal | None = Field(default=None, ge=0)
    limit: int = Field(default=10, ge=1, le=30)


class ComponentRelationsArgs(BaseModel):
    material_id: int | None = Field(default=None, gt=0)
    material_ids: list[int] = Field(default_factory=list, max_length=20)
    related_material_id: int | None = Field(default=None, gt=0)
    status: Literal["candidate", "validated", "rejected", "revoked"] | None = None

    @model_validator(mode="after")
    def material_scope_required(self):
        values = [*self.material_ids]
        if self.material_id is not None:
            values.append(self.material_id)
        if self.related_material_id is not None:
            values.append(self.related_material_id)
        if not values:
            raise ValueError("必须指定至少一个物料")
        if len(values) != len(set(values)):
            raise ValueError("关系查询物料不能重复")
        return self


class ProductBomAlternatesArgs(BaseModel):
    product_bom_item_id: int | None = Field(default=None, gt=0)
    product_revision_id: int | None = Field(default=None, gt=0)
    product_id: int | None = Field(default=None, gt=0)
    primary_material_id: int | None = Field(default=None, gt=0)
    status: Literal["candidate", "approved", "rejected", "revoked"] | None = None


class DatasheetEvidenceArgs(BaseModel):
    material_ids: list[int] = Field(min_length=1, max_length=10)
    query: str = Field(min_length=1, max_length=500)
    include_superseded: bool = False
    limit: int = Field(default=6, ge=1, le=20)

    @model_validator(mode="after")
    def unique_material_scope(self):
        if len(self.material_ids) != len(set(self.material_ids)):
            raise ValueError("证据查询物料不能重复")
        return self


class ComponentEvidenceCompareArgs(BaseModel):
    first_material_id: int = Field(gt=0)
    second_material_id: int = Field(gt=0)
    fields: list[
        Literal[
            "supply_voltage",
            "input_voltage",
            "output_current",
            "resolution_bits",
            "interface",
            "package",
            "pin",
            "pin_5",
        ]
    ] = Field(min_length=1, max_length=10)

    @model_validator(mode="after")
    def distinct_material_scope(self):
        if self.first_material_id == self.second_material_id:
            raise ValueError("必须比较两个不同物料")
        return self


class ProjectBomArgs(BaseModel):
    project_id: int = Field(gt=0)
    version: str | None = Field(default=None, max_length=32)


class SearchProjectsArgs(BaseModel):
    query: str = Field(min_length=1, max_length=200)
    limit: int = Field(default=10, ge=1, le=30)


class SearchProductsArgs(BaseModel):
    query: str = Field(min_length=1, max_length=200)
    limit: int = Field(default=10, ge=1, le=30)


class ProductBomArgs(BaseModel):
    product_id: int | None = Field(default=None, gt=0)
    product_revision_id: int | None = Field(default=None, gt=0)
    revision: str | None = Field(default=None, max_length=64)

    @model_validator(mode="after")
    def product_or_revision_required(self):
        if self.product_id is None and self.product_revision_id is None:
            raise ValueError("必须指定产品或产品版本")
        return self


class ProductBuildReadinessArgs(ProductBomArgs):
    build_quantity: int = Field(gt=0, le=1_000_000)
    project_id: int | None = Field(default=None, gt=0)


class ProposeBuildMaterialReservationArgs(BaseModel):
    product_revision_id: int = Field(gt=0)
    project_id: int = Field(gt=0)
    build_quantity: int = Field(gt=0, le=1_000_000)
    reason: str = Field(
        default="根据已发布产品 BOM 和项目预留生成构建物料预留方案",
        min_length=2,
        max_length=1000,
    )


class LowStockArgs(BaseModel):
    limit: int = Field(default=20, ge=1, le=100)


class ReservationProposalItem(BaseModel):
    material_id: int = Field(gt=0)
    quantity: Decimal = Field(gt=0, max_digits=14, decimal_places=4)


class ReservationProposalPayload(BaseModel):
    action_type: Literal["reserve_inventory"] = "reserve_inventory"
    project_id: int = Field(gt=0)
    items: list[ReservationProposalItem] = Field(min_length=1, max_length=200)
    reason: str = Field(min_length=2, max_length=1000)
    source: Literal["manual", "build_plan"] = "manual"
    build_plan_id: int | None = Field(default=None, gt=0)
    build_plan_snapshot_hash: str | None = Field(
        default=None,
        min_length=64,
        max_length=64,
    )

    @model_validator(mode="after")
    def unique_materials(self):
        material_ids = [item.material_id for item in self.items]
        if len(material_ids) != len(set(material_ids)):
            raise ValueError("同一物料不能在一个 Proposal 中重复")
        if self.source == "build_plan" and (
            self.build_plan_id is None or self.build_plan_snapshot_hash is None
        ):
            raise ValueError("BuildPlan Proposal 必须包含计划 ID 和快照 hash")
        if self.source == "manual" and (
            self.build_plan_id is not None or self.build_plan_snapshot_hash is not None
        ):
            raise ValueError("手工 Proposal 不能携带 BuildPlan provenance")
        return self


class ProposeInventoryReservationArgs(BaseModel):
    project_id: int = Field(gt=0)
    items: list[ReservationProposalItem] = Field(min_length=1, max_length=200)
    reason: str = Field(
        default="Agent 根据项目 BOM 生成物料预留建议",
        min_length=2,
        max_length=1000,
    )

    @model_validator(mode="after")
    def unique_materials(self):
        material_ids = [item.material_id for item in self.items]
        if len(material_ids) != len(set(material_ids)):
            raise ValueError("同一物料不能重复")
        return self


class ProposalRejectRequest(BaseModel):
    reason: str = Field(default="人工拒绝", min_length=2, max_length=1000)


class AgentProposalDisplayItem(BaseModel):
    material_id: int
    code: str
    name: str
    mpn: str
    required_total: Decimal | None = None
    reserved_for_project_at_plan: Decimal | None = None
    additional_reservation_required: Decimal | None = None


class AgentProposalDisplay(BaseModel):
    project_code: str = ""
    project_name: str = ""
    source: Literal["manual", "build_plan"] = "manual"
    build_plan_id: int | None = None
    build_plan_no: str = ""
    product_code: str = ""
    product_name: str = ""
    product_revision: str = ""
    product_bom_hash: str = ""
    build_quantity: int | None = None
    items: list[AgentProposalDisplayItem] = Field(default_factory=list)


class AgentActionProposalOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    proposal_no: str
    action_type: str
    status: str
    payload: dict[str, Any]
    display: AgentProposalDisplay = Field(default_factory=AgentProposalDisplay)
    reason: str
    created_by_id: int
    approved_by_id: int | None
    request_id: str
    client_operation_id: str
    payload_hash: str
    execution_result: dict[str, Any] | None
    error_message: str
    expires_at: datetime | None
    decided_at: datetime | None
    executed_at: datetime | None
    created_at: datetime
    updated_at: datetime
