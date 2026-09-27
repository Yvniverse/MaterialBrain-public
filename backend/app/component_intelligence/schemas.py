from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field


class ComponentSearchRequest(BaseModel):
    requirement: str = Field(min_length=2, max_length=1000)
    limit: int = Field(default=8, ge=1, le=20)
    conversation_id: str | None = Field(default=None, max_length=100)


class NumericRange(BaseModel):
    minimum: Decimal
    maximum: Decimal


class ComponentQuery(BaseModel):
    raw_text: str
    keywords: list[str] = Field(default_factory=list, max_length=30)
    component_types: list[str] = Field(default_factory=list, max_length=10)
    interfaces: list[str] = Field(default_factory=list, max_length=20)
    voltage_values_v: list[Decimal] = Field(default_factory=list, max_length=10)
    voltage_range_v: NumericRange | None = None
    supply_voltage_v: Decimal | None = None
    input_voltage_v: Decimal | None = None
    output_voltage_v: Decimal | None = None
    bus_voltage_v: Decimal | None = None
    logic_voltage_v: Decimal | None = None
    current_a: Decimal | None = None
    current_min_a: Decimal | None = None
    current_max_a: Decimal | None = None
    current_range_a: NumericRange | None = None
    resolution_bits: int | None = Field(default=None, ge=1, le=128)
    package_preferences: list[str] = Field(default_factory=list, max_length=10)
    manufacturer_preferences: list[str] = Field(default_factory=list, max_length=10)
    hard_constraints: dict[str, Any] = Field(default_factory=dict)
    soft_preferences: dict[str, Any] = Field(default_factory=dict)
    context_reference: bool = False
    location_requested: bool = False
    replacement_intent: bool = False


class ComponentCandidate(BaseModel):
    material_id: int
    code: str
    name: str
    mpn: str
    specification: str
    package: str
    manufacturer: str
    score: int = Field(ge=1)
    match_reasons: list[str]
    hard_constraint_matches: list[str] = Field(default_factory=list)
    soft_preference_matches: list[str] = Field(default_factory=list)
    metadata_confidence: str
    technical_claims_allowed: bool
    engineering_verification_required: bool = True
    validated_relations: list[dict[str, Any]] = Field(default_factory=list)
    inventory: dict[str, Any]
    locations: dict[str, Any]


class ComponentSearchResponse(BaseModel):
    conversation_id: str
    selected_material_id: int | None = None
    guidance: str = ""
    query: ComponentQuery
    candidates: list[ComponentCandidate]
    count: int = Field(ge=0)
    candidate_only: bool = True
    engineering_caveat: str
    read_only: bool = True


class ComponentCoreResult(BaseModel):
    query: ComponentQuery
    candidates: list[ComponentCandidate]
    count: int = Field(ge=0)
    candidate_only: bool = True
    engineering_caveat: str
    read_only: bool = True
