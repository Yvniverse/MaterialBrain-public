from decimal import Decimal
from types import SimpleNamespace

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.models import InventoryLot, Location, Material
from evals.generate_production_shadow_cases import (
    build_cases,
    build_coverage,
    build_multiturn_cases,
)
from evals.run_production_shadow_eval import _facts_match
from evals.seed_golden_db import seed_golden_database


def test_component_intelligence_fixture_profile_is_full_and_structured(tmp_path):
    database_url = f"sqlite:///{(tmp_path / 'component-golden.db').as_posix()}"
    mapping = seed_golden_database(
        database_url,
        reset=True,
        fixture_profile="component-intelligence",
    )
    engine = create_engine(database_url)
    with Session(engine) as db:
        materials = list(db.scalars(select(Material)).all())
        can_part = db.scalar(
            select(Material).where(Material.code == "PORT-CAN-TCAN1044")
        )
        low_confidence = db.scalar(
            select(Material).where(Material.code == "C130723")
        )
        assert len(materials) == 139
        assert len(mapping["materials"]) == 139
        assert can_part.attributes["component_type"] == "CAN transceiver"
        assert (
            can_part.attributes["portfolio_demo"]["catalog_confidence"] == "high"
        )
        assert low_confidence.attributes["portfolio_demo"]["catalog_confidence"] == "low"
        assert "component_type" not in low_confidence.attributes
    engine.dispose()


def test_shadow_generation_is_balanced_and_reports_aggregate_coverage(tmp_path):
    database_url = f"sqlite:///{(tmp_path / 'shadow-eval.db').as_posix()}"
    mapping = seed_golden_database(database_url, reset=True)
    engine = create_engine(database_url)
    with Session(engine) as db:
        source = db.get(Material, next(iter(mapping["materials"].values())))
        db.add(
            Material(
                code="AMBIGUOUS-SHADOW-ONLY",
                name=source.name,
                mpn="AMBIGUOUS-SHADOW-ONLY",
                quantity=Decimal("1"),
                reserved_quantity=Decimal("0"),
                safety_stock=Decimal("0"),
                target_stock=Decimal("0"),
                created_by_id=mapping["user_id"],
                updated_by_id=mapping["user_id"],
            )
        )
        cross_field = Material(
            code="AMBIGUOUS-CROSS-FIELD",
            name="Cross field candidate",
            mpn=source.name,
            quantity=Decimal("1"),
            reserved_quantity=Decimal("0"),
            safety_stock=Decimal("0"),
            target_stock=Decimal("0"),
            created_by_id=mapping["user_id"],
            updated_by_id=mapping["user_id"],
        )
        db.add(cross_field)
        secondary = Location(
            code="SHADOW-SECONDARY",
            name="Shadow secondary",
            full_path="Shadow / Secondary",
        )
        db.add(secondary)
        db.flush()
        source_lot = db.scalar(
            select(InventoryLot).where(InventoryLot.material_id == source.id)
        )
        source_lot.quantity = source.quantity - Decimal("2")
        db.add(
            InventoryLot(
                material_id=source.id,
                location_id=secondary.id,
                quantity=Decimal("1"),
            )
        )
        db.commit()

        coverage = build_coverage(db)
        cases = build_cases(db, seed=20260827, max_cases=60)
        multi_cases = build_multiturn_cases(db, seed=20260827, max_cases=10)

    categories = {case["category"] for case in cases}
    assert {
        "material_inventory",
        "material_location",
        "reserved_material",
        "material_ambiguity",
        "low_stock",
        "project_bom",
        "bom_stock",
    }.issubset(categories)
    assert coverage["active_materials"] == len(mapping["materials"]) + 2
    assert coverage["projects_with_bom"] == len(mapping["projects"])
    assert coverage["ambiguous_exact_identifier_groups"] >= 1
    assert coverage["partial_location_materials"] >= 1
    assert coverage["multi_location_materials"] >= 1
    assert coverage["inconsistent_location_materials"] == 0
    assert coverage["field_coverage"]["name"] == coverage["active_materials"]
    assert 8 <= len(multi_cases) <= 10
    assert all(case["execution_mode"] == "true_multiturn" for case in multi_cases)
    assert {case["category"] for case in multi_cases}.issuperset(
        {
            "multi_material_followup",
            "multi_context_switch",
            "multi_reset",
            "multi_material_disambiguation",
        }
    )
    cross_field_case = next(
        case
        for case in cases
        if case["category"] == "material_ambiguity" and source.name in case["query"]
    )
    assert cross_field.id in cross_field_case["oracle"]["critical_facts"]["candidate_ids"]
    location_oracles = [
        case["oracle"]["critical_facts"]
        for case in cases
        if case["category"] == "material_location"
    ]
    assert any(item.get("distribution_status") == "partial" for item in location_oracles)
    assert any(item.get("allocation_mode") == "multi_location" for item in location_oracles)
    engine.dispose()


def test_shadow_material_ambiguity_rejects_live_fact_reads():
    case = {
        "category": "material_ambiguity",
        "oracle": {
            "entity_type": "material",
            "must_disambiguate": True,
            "critical_facts": {"candidate_ids": [2, 4]},
        },
    }
    safe = SimpleNamespace(
        entities={"material_candidates": {"items": [{"id": 4}, {"id": 2}]}},
        tool_events=[],
    )
    unsafe = SimpleNamespace(
        entities={
            "material_candidates": {"items": [{"id": 2}, {"id": 4}]},
            "inventory": {"available_quantity": "1"},
        },
        tool_events=[],
    )

    assert _facts_match(case, safe) == (True, [])
    passed, failures = _facts_match(case, unsafe)
    assert passed is False
    assert "ambiguous material was read without selection" in failures


def test_shadow_location_oracle_distinguishes_partial_from_multi_location():
    case = {
        "category": "material_location",
        "oracle": {
            "must_disambiguate": False,
            "critical_facts": {
                "primary_location_id": 7,
                "lot_quantity_total": "9.0000",
                "location_count": 2,
                "distribution_status": "partial",
                "allocation_mode": "multi_location",
                "primary_location_quantity_is_exact": True,
            },
        },
    }
    response = SimpleNamespace(
        entities={
            "locations": {
                "lot_quantity_total": "9.0000",
                "count": 2,
                "distribution_status": "partial",
                "locations": [
                    {"location_id": 7, "quantity_is_exact": True},
                    {"location_id": 8, "quantity_is_exact": True},
                ],
            }
        },
        tool_events=[],
    )
    assert _facts_match(case, response) == (True, [])

    response.entities["locations"]["locations"] = [
        {"location_id": 7, "quantity_is_exact": False},
        {"location_id": 8, "quantity_is_exact": True},
    ]
    passed, failures = _facts_match(case, response)
    assert passed is False
    assert "location fact mismatch: allocation_mode" in failures
    assert "primary location quantity exactness mismatch" in failures


def test_shadow_bom_stock_matches_read_only_analysis():
    case = {
        "category": "bom_stock",
        "oracle": {
            "must_disambiguate": False,
            "critical_facts": {
                "version": "V1",
                "shortage_count": 1,
                "sufficient": False,
                "shortage_material_ids": [9],
            },
        },
    }
    response = SimpleNamespace(
        entities={
            "bom_analysis": {
                "version": "V1",
                "shortage_count": 1,
                "sufficient": False,
                "items": [
                    {"material_id": 8, "sufficient": True},
                    {"material_id": 9, "sufficient": False},
                ],
            }
        },
        tool_events=[],
    )

    assert _facts_match(case, response) == (True, [])


def test_shadow_multi_version_bom_requires_the_real_project_candidate():
    case = {
        "category": "project_bom",
        "oracle": {
            "entity_type": "project",
            "must_disambiguate": True,
            "critical_facts": {"available_versions": ["V1", "V2"]},
        },
    }
    safe = SimpleNamespace(
        entities={"project_candidates": {"items": [{"id": 7, "available_versions": ["V1", "V2"]}]}},
        tool_events=[],
    )
    missing = SimpleNamespace(entities={}, tool_events=[])

    assert _facts_match(case, safe) == (True, [])
    assert _facts_match(case, missing)[0] is False
