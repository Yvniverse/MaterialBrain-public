from typing import Literal

from pydantic import BaseModel, Field, model_validator

EvidenceField = Literal[
    "supply_voltage",
    "input_voltage",
    "output_current",
    "resolution_bits",
    "interface",
    "package",
    "pin",
    "pin_5",
]


class EvidenceSearchRequest(BaseModel):
    material_ids: list[int] = Field(min_length=1, max_length=10)
    query: str = Field(min_length=1, max_length=500)
    include_superseded: bool = False
    limit: int = Field(default=6, ge=1, le=20)

    @model_validator(mode="after")
    def unique_scope(self):
        if len(self.material_ids) != len(set(self.material_ids)):
            raise ValueError("物料范围不能重复")
        return self


class EvidenceCompareRequest(BaseModel):
    first_material_id: int = Field(gt=0)
    second_material_id: int = Field(gt=0)
    fields: list[EvidenceField] = Field(min_length=1, max_length=10)

    @model_validator(mode="after")
    def different_materials(self):
        if self.first_material_id == self.second_material_id:
            raise ValueError("必须比较两个不同物料")
        return self


class EvidenceClaimCitation(BaseModel):
    claim: str = Field(min_length=1, max_length=2000)
    confidence: Literal["supported", "partial", "insufficient"]
    support_anchor_ids: list[int] = Field(default_factory=list, max_length=20)

