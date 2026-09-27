from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

RelationType = Literal[
    "similar_to",
    "electrical_compatible",
    "pin_compatible",
    "same_footprint",
]


class EvidenceRef(BaseModel):
    type: str = Field(min_length=1, max_length=80)
    reference: str = Field(min_length=1, max_length=240)
    summary: str = Field(default="", max_length=2000)


class ComponentRelationCreate(BaseModel):
    source_material_id: int = Field(gt=0)
    target_material_id: int = Field(gt=0)
    relation_type: RelationType
    confidence_note: str = Field(default="", max_length=2000)
    evidence_summary: str = Field(default="", max_length=4000)
    evidence_refs: list[EvidenceRef] = Field(default_factory=list, max_length=50)


class RelationRejectRequest(BaseModel):
    reason: str = Field(min_length=2, max_length=2000)

    @field_validator("reason")
    @classmethod
    def strip_reason(cls, value: str) -> str:
        return value.strip()


class RelationRevokeRequest(RelationRejectRequest):
    pass


class EvidenceLinkRequest(BaseModel):
    evidence_anchor_id: int = Field(gt=0)
    role: Literal["supporting", "contradicting", "context"] = "supporting"
    review_note: str = Field(default="", max_length=2000)


class ProductBomAlternateCreate(BaseModel):
    alternate_material_id: int = Field(gt=0)
    priority: int = Field(default=100, gt=0, le=10000)
    usage_condition: str = Field(default="", max_length=4000)
    engineering_note: str = Field(default="", max_length=4000)
    evidence_refs: list[EvidenceRef] = Field(default_factory=list, max_length=50)
    source_component_relation_id: int | None = Field(default=None, gt=0)


def evidence_data(values: list[EvidenceRef]) -> list[dict[str, Any]]:
    return [item.model_dump(mode="json") for item in values]
