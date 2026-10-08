"""Small deterministic provenance helpers for material fact groups.

Identity/specification, stock, and location are deliberately independent.  A
sample row can carry an official-vendor specification while its quantity
and bin are still synthetic demonstration data.
"""

from __future__ import annotations

from typing import Any


def _attributes(material: Any) -> dict[str, Any]:
    if isinstance(material, dict):
        value = material.get("attributes")
    else:
        value = getattr(material, "attributes", None)
    return dict(value) if isinstance(value, dict) else {}


def _first(mapping: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = mapping.get(key)
        if value not in (None, ""):
            return value
    return None


def _source(*values: Any, fallback: str) -> str:
    for value in values:
        if value not in (None, ""):
            return str(value)
    return fallback


def material_provenance(
    material: Any,
    *,
    inventory: dict[str, Any] | None = None,
    locations: dict[str, Any] | None = None,
) -> dict[str, dict[str, str]]:
    """Return independent identity/spec/stock/location provenance axes."""

    attributes = _attributes(material)
    sample = attributes.get("sample_data")
    sample = sample if isinstance(sample, dict) else {}
    if not sample and attributes.get("stock_is_synthetic") is True:
        sample = {
            "stock_is_synthetic": True,
            "source_ref": attributes.get("sample_cable_dataset")
            or attributes.get("sample_dataset")
            or "sample_data",
        }
    catalog = attributes.get("catalog_provenance")
    catalog = catalog if isinstance(catalog, dict) else {}
    spec_v2 = attributes.get("spec_provenance_v2")
    spec_v2 = spec_v2 if isinstance(spec_v2, dict) else {}

    identity_source = _first(catalog, "identity_source", "source_ref")
    if identity_source:
        identity = {
            "kind": "catalog",
            "source": str(identity_source),
        }
    elif str(spec_v2.get("source_type") or "").casefold() == "official_vendor":
        identity = {
            "kind": "official_vendor",
            "source": _source(
                spec_v2.get("source_url"),
                spec_v2.get("source_mpn"),
                fallback="official_vendor",
            ),
        }
    elif sample.get("stock_is_synthetic") is True:
        identity = {
            "kind": "sample_synthetic",
            "source": _source(
                sample.get("source_ref"),
                sample.get("dataset_version"),
                fallback="sample_data",
            ),
        }
    else:
        identity = {"kind": "unknown", "source": ""}

    if str(spec_v2.get("source_type") or "").casefold() == "official_vendor":
        spec = {
            "kind": "official_vendor",
            "source": _source(
                spec_v2.get("source_url"),
                spec_v2.get("source_mpn"),
                fallback="official_vendor",
            ),
        }
    elif attributes.get("managed_evidence") or attributes.get("engineering_evidence"):
        spec = {"kind": "managed_evidence", "source": "managed_engineering_evidence"}
    elif catalog:
        spec = {
            "kind": "catalog",
            "source": _source(
                catalog.get("source_ref"),
                catalog.get("identity_source"),
                fallback="catalog",
            ),
        }
    elif sample.get("stock_is_synthetic") is True:
        spec = {
            "kind": "sample_synthetic",
            "source": _source(
                sample.get("source_ref"),
                sample.get("dataset_version"),
                fallback="sample_data",
            ),
        }
    else:
        spec = {"kind": "unknown", "source": ""}

    if sample.get("stock_is_synthetic") is True:
        stock = {
            "kind": "sample_synthetic",
            "source": _source(
                sample.get("source_ref"),
                sample.get("dataset_version"),
                fallback="sample_data",
            ),
        }
        location = {
            "kind": "sample_synthetic",
            "source": _source(
                sample.get("source_ref"),
                sample.get("dataset_version"),
                fallback="sample_data",
            ),
        }
    else:
        stock = {
            "kind": "operational_db" if inventory is not None else "unknown",
            "source": "inventory_availability" if inventory is not None else "",
        }
        location = {
            "kind": "operational_db" if locations is not None else "unknown",
            "source": "material_locations" if locations is not None else "",
        }

    return {
        "identity": identity,
        "spec": spec,
        "stock": stock,
        "location": location,
    }
