from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.models import User
from app.services.data_provenance import material_provenance


@dataclass(frozen=True)
class ToolContext:
    db: Session
    user: User
    request_id: str
    client_operation_id: str = ""
    trace_steps: list[dict[str, Any]] | None = None


def location_dict(
    location,
    *,
    organizer=None,
    quantity_at_location=None,
    quantity_is_exact: bool = True,
) -> dict | None:
    if location is None:
        return None
    organizer = organizer or location
    return {
        "location_id": location.id,
        "code": location.code,
        "name": location.name,
        "full_path": location.full_path,
        "organizer_id": organizer.id if organizer and organizer.type == "box" else None,
        "organizer_style": organizer.organizer_style if organizer else None,
        "parent_id": location.parent_id,
        "quantity_at_location": quantity_at_location,
        "quantity_is_exact": quantity_is_exact,
    }


def material_dict(material, *, location=None) -> dict:
    return {
        "id": material.id,
        "code": material.code,
        "name": material.name,
        "mpn": material.mpn,
        "specification": material.specification,
        "package": material.package,
        "manufacturer": material.manufacturer,
        "unit": material.unit,
        "attributes": dict(material.attributes or {}),
        "quantity": str(material.quantity),
        "reserved_quantity": str(material.reserved_quantity),
        "available_quantity": str(material.available_quantity),
        "location": location,
        "provenance": material_provenance(material),
    }
