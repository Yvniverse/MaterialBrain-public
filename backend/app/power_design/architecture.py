from __future__ import annotations

from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field

PowerBomStatus = Literal[
    "needs_input",
    "needs_selection",
    "candidate_found",
    "selected",
    "no_grounded_candidate",
    "not_applicable",
    "out_of_stock",
    "blocked_by_input",
    "complete_draft",
]

PowerBomMatchStatus = Literal["exact", "compatible", "partial", "mismatch"]
PowerBomConstraintOperator = Literal["eq", "gte", "lte", "range", "one_of", "preferred"]
PowerBomConstraintSourceKind = Literal[
    "datasheet",
    "reference_design",
    "deterministic_calculation",
    "user_input",
]


class PowerBomConstraint(BaseModel):
    key: str
    operator: PowerBomConstraintOperator
    value: Any
    unit: str | None = None
    hard: bool = True
    source_kind: PowerBomConstraintSourceKind = "datasheet"
    source_anchor: dict[str, Any] = Field(default_factory=dict)
    citation: dict[str, Any] = Field(default_factory=dict)


class PowerBomMatchSummary(BaseModel):
    exact_or_compatible_candidates: int = 0
    partial_candidates: int = 0
    mismatch_candidates: int = 0
    selected_candidate_valid: bool | None = None


class PowerBomCompletenessSummary(BaseModel):
    requirements_defined: int = 0
    requirements_grounded: int = 0
    candidate_covered: int = 0
    explicitly_selected: int = 0
    unresolved: int = 0
    needs_input: int = 0
    evidence_gap: int = 0
    complete_for_engineering_review: bool = False
    complete_for_bom_preview_ready_path: bool = False
    required_roles: int = 0
    grounded_roles: int = 0
    candidate_covered_roles: int = 0
    selected_roles: int = 0
    unresolved_roles: int = 0
    needs_input_roles: int = 0
    out_of_stock_roles: int = 0
    complete_for_review: bool = False
    blocking_reasons: list[str] = Field(default_factory=list)


class PowerCandidateReference(BaseModel):
    material_id: int
    code: str
    mpn: str
    package: str | None = None
    inventory: dict[str, Any] = Field(default_factory=dict)
    locations: list[str] = Field(default_factory=list)
    location_facts: list[dict[str, Any]] = Field(default_factory=list)
    selection_status: PowerBomStatus = "candidate_found"
    match_status: PowerBomMatchStatus | None = None
    match_reasons: list[str] = Field(default_factory=list)
    match_unknowns: list[str] = Field(default_factory=list)
    selection_basis: Literal["explicit_user"] | None = None
    selection_provenance: dict[str, Any] = Field(default_factory=dict)
    inventory_status: Literal["in_stock", "out_of_stock", "unknown", "not_checked"] = (
        "unknown"
    )
    peripheral_roles: list[dict[str, Any]] = Field(default_factory=list)
    engineering_parameters: list[dict[str, Any]] = Field(default_factory=list)
    citations: list[dict[str, Any]] = Field(default_factory=list)
    component_class: str = "unknown"
    class_source: str = "unknown"
    class_match: Literal["compatible", "unknown", "mismatch"] = "unknown"
    rejection_reason: str | None = None
    provenance: dict[str, dict[str, str]] = Field(default_factory=dict)


class PowerRailBomRequirement(BaseModel):
    requirement_id: str
    role: str
    required: bool = True
    required_quantity: Decimal | None = None
    exact_value: str | None = None
    evidence_status: Literal["grounded", "not_grounded", "unknown"] = "unknown"
    status: PowerBomStatus
    selection_status: PowerBomStatus
    inventory_status: Literal["in_stock", "out_of_stock", "unknown", "not_checked"] = (
        "not_checked"
    )
    selected_material_id: int | None = None
    selection_basis: Literal["explicit_user"] | None = None
    selection_provenance: dict[str, Any] = Field(default_factory=dict)
    selection_conflict: str | None = None
    constraints: list[PowerBomConstraint] = Field(default_factory=list)
    match_summary: PowerBomMatchSummary = Field(default_factory=PowerBomMatchSummary)
    candidates: list[PowerCandidateReference] = Field(default_factory=list)
    peripheral_roles: list[dict[str, Any]] = Field(default_factory=list)
    citations: list[dict[str, Any]] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    component_class: str = "unknown"
    expected_component_classes: list[str] = Field(default_factory=list)
    provenance: dict[str, dict[str, str]] = Field(default_factory=dict)
    completeness_contribution: dict[str, Any] = Field(default_factory=dict)


class PowerArchitectureStage(BaseModel):
    stage_id: str
    topology: Literal["buck", "ldo", "filter"]
    input_voltage_v: Decimal | None = None
    output_voltage_v: Decimal | None = None
    load_current_a: Decimal | None = None
    current_basis: Literal["user_total", "user_rail", "derived_from_total", "not_allocated"]
    loss_w: Decimal | None = None
    ideal_efficiency: Decimal | None = None
    quiescent_current_a: Decimal | None = None
    quiescent_input_power_w: Decimal | None = None
    thermal_screen: dict[str, Any] = Field(default_factory=dict)
    loss_status: Literal[
        "calculated",
        "unknown_load",
        "requires_efficiency_curve",
        "not_applicable",
    ]
    headroom_v: Decimal | None = None
    dropout_status: Literal["not_applicable", "verify_at_load", "unknown"]
    candidate_devices: list[PowerCandidateReference] = Field(default_factory=list)
    selection_status: PowerBomStatus = "needs_selection"
    selected_material_id: int | None = None
    selection_basis: Literal["explicit_user"] | None = None
    engineering_facts: list[dict[str, Any]] = Field(default_factory=list)
    bom_requirements: list[PowerRailBomRequirement] = Field(default_factory=list)
    completeness: PowerBomCompletenessSummary = Field(default_factory=PowerBomCompletenessSummary)
    notes: list[str] = Field(default_factory=list)


class PowerRail(BaseModel):
    rail_id: str
    label: str
    voltage_v: Decimal | None = None
    load_current_a: Decimal | None = None
    current_basis: Literal["user_total", "user_rail", "derived_from_total", "not_allocated"]
    sensitive_analog: bool = False
    stage_ids: list[str] = Field(default_factory=list)
    selection_status: PowerBomStatus = "needs_selection"
    completeness: PowerBomCompletenessSummary = Field(default_factory=PowerBomCompletenessSummary)
    notes: list[str] = Field(default_factory=list)


class PowerArchitecture(BaseModel):
    topology: Literal["direct_buck", "buck_ldo", "split_rails"]
    label: str
    availability: Literal["candidate_found", "conceptual", "no_grounded_candidate"]
    selected_by_user: bool = False
    total_load_current_a: Decimal | None = None
    rails: list[PowerRail] = Field(default_factory=list)
    stages: list[PowerArchitectureStage] = Field(default_factory=list)
    summary: str
    constraints: list[str] = Field(default_factory=list)
    read_only: Literal[True] = True


class PowerRailBomDraft(BaseModel):
    # Keep the historical top-level values for API compatibility. Richer
    # selection/input semantics live on rails, stages, and requirements.
    status: Literal[
        "needs_selection",
        "needs_confirmation",
        "not_supported",
        "needs_input",
        "candidate_found",
        "selected",
        "no_grounded_candidate",
        "not_applicable",
        "blocked_by_input",
        "complete_draft",
    ]
    selected_topology: str | None = None
    rails: list["PowerRailBomRailDraft"] = Field(default_factory=list)
    completeness: PowerBomCompletenessSummary = Field(default_factory=PowerBomCompletenessSummary)
    manual_review: list[str] = Field(default_factory=list)
    read_only: Literal[True] = True
    automatic_write: Literal[False] = False


class PowerRailBomStageDraft(BaseModel):
    stage_id: str
    topology: Literal["buck", "ldo", "filter"]
    input_voltage_v: Decimal | None = None
    output_voltage_v: Decimal | None = None
    load_current_a: Decimal | None = None
    loss_w: Decimal | None = None
    loss_status: str | None = None
    headroom_v: Decimal | None = None
    dropout_status: str | None = None
    selection_status: PowerBomStatus = "needs_selection"
    engineering_facts: list[dict[str, Any]] = Field(default_factory=list)
    candidate_devices: list[PowerCandidateReference] = Field(default_factory=list)
    bom_requirements: list[PowerRailBomRequirement] = Field(default_factory=list)
    selected_material_id: int | None = None
    selection_basis: Literal["explicit_user"] | None = None
    completeness: PowerBomCompletenessSummary = Field(default_factory=PowerBomCompletenessSummary)


class PowerRailBomRailDraft(BaseModel):
    rail_id: str
    label: str
    voltage_v: Decimal | None = None
    load_current_a: Decimal | None = None
    current_basis: Literal["user_total", "user_rail", "derived_from_total", "not_allocated"] = (
        "not_allocated"
    )
    sensitive_analog: bool = False
    selection_status: PowerBomStatus = "needs_selection"
    stages: list[PowerRailBomStageDraft] = Field(default_factory=list)
    completeness: PowerBomCompletenessSummary = Field(default_factory=PowerBomCompletenessSummary)
