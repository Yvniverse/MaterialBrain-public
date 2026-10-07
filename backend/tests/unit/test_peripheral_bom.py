from decimal import Decimal

from app.services.data_provenance import material_provenance
from app.services.peripheral_bom import (
    material_match,
    required_quantity,
    requirement_spec,
    resolve_inventory_status,
)


def _requirement(value="2.2 nF, 50 V, X7R", *, quantity="1"):
    spec = requirement_spec(value)
    return {
        **spec,
        "capacitance_pf": str(spec["capacitance_pf"]),
        "rated_voltage_v": str(spec["rated_voltage_v"]),
        "required_quantity": quantity,
    }


def _material(material_id, *, value="2200pF", voltage="63V", dielectric="X7R"):
    return {
        "material_id": material_id,
        "code": f"C-{material_id}",
        "mpn": f"CAP-{material_id}",
        "name": f"电容 {value} {voltage} {dielectric or ''}",
        "specification": f"{value} {voltage} {dielectric or ''}",
        "attributes": {
            "capacitance": value,
            "rated_voltage": voltage,
            **({"dielectric": dielectric} if dielectric is not None else {}),
        },
    }


def test_bootstrap_normalizes_nf_to_pf_and_accepts_higher_voltage():
    result = material_match(_requirement(), _material(101))

    assert result["status"] == "exact"
    assert result["normalized_spec"]["capacitance_pf"] == "2200"
    assert result["normalized_spec"]["rated_voltage_v"] == "63"


def test_wrong_voltage_and_capacitance_are_not_matches():
    requirement = _requirement()

    assert material_match(requirement, _material(102, voltage="16V"))["status"] == "mismatch"
    assert material_match(requirement, _material(103, value="2.2uF"))["status"] == "mismatch"


def test_unknown_dielectric_keeps_candidate_ambiguous_even_with_exact_candidate():
    requirement = _requirement()
    exact = {**_material(104), "match_status": "exact", "match_unknowns": []}
    partial = {
        **_material(105, dielectric=None),
        "match_status": "partial",
        "match_unknowns": ["物料介质未填，无法确认"],
    }

    result = resolve_inventory_status(
        requirement,
        [exact, partial],
        {104: {"available_quantity": "5"}},
        {104: {"locations": []}},
    )

    assert result["selection_status"] == "ambiguous_candidates"
    assert result["matched_material_id"] is None


def test_known_requirement_without_match_is_not_design_unknown():
    result = resolve_inventory_status(
        _requirement(),
        [],
        {},
        {},
    )

    assert result["selection_status"] == "no_matching_material"


def test_shortage_and_location_unassigned_are_separate_statuses():
    requirement = _requirement(quantity="2")
    exact = {**_material(106), "match_status": "exact", "match_unknowns": []}

    shortage = resolve_inventory_status(
        requirement,
        [exact],
        {106: {"available_quantity": "1"}},
        {106: {"locations": [{"full_path": "A-01"}]}},
    )
    assert shortage["selection_status"] == "shortage"
    assert shortage["shortage_quantity"] == "1"

    unassigned = resolve_inventory_status(
        _requirement(),
        [{**exact, "material_id": 107}],
        {107: {"available_quantity": "3"}},
        {107: {"locations": []}},
    )
    assert unassigned["selection_status"] == "stocked_location_unassigned"
    assert unassigned["available_quantity"] == "3"


def test_required_quantity_reads_two_piece_request_without_inventing_unknown_quantity():
    assert required_quantity("一个方案两颗", "bootstrap capacitor", Decimal("1")) == Decimal("2")
    assert required_quantity("电感量待定", "inductor", None) is None


def test_phase336_component_class_gate_separates_class_from_electrical_matching():
    requirement = {
        **_requirement(),
        "role": "bootstrap capacitor",
        "expected_component_classes": ["capacitor"],
    }
    correct = {
        **_material(201),
        "attributes": {
            **_material(201)["attributes"],
            "component_type": "capacitor",
        },
    }
    cable = {
        **_material(202),
        "attributes": {
            "component_type": "cable",
            "length": "200mm",
        },
    }
    untyped = _material(203)
    wrong_voltage = {
        **_material(204, voltage="16V"),
        "attributes": {
            **_material(204, voltage="16V")["attributes"],
            "component_type": "capacitor",
        },
    }

    assert material_match(requirement, correct)["match_status"] in {"exact", "compatible"}
    assert material_match(requirement, correct)["class_match"] == "compatible"
    rejected = material_match(requirement, cable)
    assert rejected["match_status"] == "mismatch"
    assert rejected["rejection_reason"] == "component_class_mismatch"
    assert rejected["class_match"] == "mismatch"
    partial = material_match(requirement, untyped)
    assert partial["match_status"] == "partial"
    assert partial["class_match"] == "unknown"
    voltage = material_match(requirement, wrong_voltage)
    assert voltage["class_match"] == "compatible"
    assert voltage["match_status"] == "mismatch"


def test_phase336_provenance_axes_keep_official_spec_and_synthetic_stock_separate():
    provenance = material_provenance(
        {
            "attributes": {
                "component_type": "capacitor",
                "spec_provenance_v2": {
                    "source_type": "official_vendor",
                    "source_url": "https://vendor.example/datasheet.pdf",
                },
                "sample_data": {
                    "stock_is_synthetic": True,
                    "source_ref": "P336-UAT",
                },
            }
        },
        inventory={"available_quantity": "3"},
        locations={"locations": []},
    )
    assert provenance["spec"]["kind"] == "official_vendor"
    assert provenance["stock"]["kind"] == "sample_synthetic"
    assert provenance["location"]["kind"] == "sample_synthetic"
