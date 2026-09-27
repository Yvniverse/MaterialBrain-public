import json
from pathlib import Path


def test_portfolio_baseline_manifest_v2_has_reproducibility_identity():
    path = (
        Path(__file__).resolve().parents[2]
        / "portfolio_demo_data"
        / "v2_1"
        / "portfolio_baseline_manifest_v2.json"
    )
    manifest = json.loads(path.read_text(encoding="utf-8"))
    required = {
        "git_commit",
        "alembic_revision",
        "portfolio_seed_version",
        "product_seed_version",
        "material_count",
        "project_count",
        "product_count",
        "product_revision_count",
        "project_bom_count",
        "product_bom_count",
        "location_count",
        "sha256",
    }
    assert required.issubset(manifest)
    assert manifest["manifest_version"] == 2
    assert manifest["alembic_revision"] == "0009_build_plan_governance"
    assert manifest["portfolio_seed_version"] == "v2"
    assert manifest["product_seed_version"] == "portfolio_product_demo_v1"
    assert manifest["material_count"] == 131
    assert manifest["project_count"] == 12
    assert manifest["product_count"] == 6
    assert manifest["product_revision_count"] == 7
    assert manifest["project_bom_count"] == 161
    assert manifest["product_bom_count"] == 64
    assert manifest["location_count"] == 747


def test_portfolio_baseline_manifest_v3_is_stable_0010_relation_snapshot():
    path = (
        Path(__file__).resolve().parents[2]
        / "portfolio_demo_data"
        / "v2_1"
        / "portfolio_baseline_manifest_v3.json"
    )
    manifest = json.loads(path.read_text(encoding="utf-8"))
    assert manifest["manifest_version"] == 3
    assert manifest["kind"] == "materialbrain_portfolio_baseline"
    assert manifest["database_is_synthetic"] is True
    assert manifest["source_release_tag"] == "portfolio-v2.1"
    assert manifest["alembic_revision"] == "0010_component_relations"
    assert manifest["relation_seed_version"] == "portfolio_component_relations_v1"
    assert manifest["entity_counts"]["component_relation_count"] == 4
    assert manifest["entity_counts"]["product_bom_alternate_count"] == 3
    assert manifest["entity_counts"]["administrator_count"] == 1
    assert manifest["real_history_business_rows_remaining"] == 0


def test_portfolio_baseline_manifest_v4_is_stable_0011_evidence_snapshot():
    path = (
        Path(__file__).resolve().parents[2]
        / "portfolio_demo_data"
        / "v2_2"
        / "portfolio_baseline_manifest_v4.json"
    )
    manifest = json.loads(path.read_text(encoding="utf-8"))
    assert manifest["manifest_version"] == 4
    assert manifest["phase"] == "2.4"
    assert manifest["database_is_synthetic"] is True
    assert manifest["baseline_source_commit"] == manifest["git_commit"]
    assert len(manifest["baseline_source_commit"]) == 40
    assert len(manifest["compatible_release_commit"]) == 40
    assert manifest["compatible_release_commit"] != manifest["baseline_source_commit"]
    assert "compatible_release_tag" in manifest
    assert manifest["alembic_revision"] == "0011_engineering_evidence"
    assert (
        manifest["synthetic_datasheet_dataset_version"]
        == "materialbrain_synthetic_datasheet_evidence_v1"
    )
    assert manifest["entity_counts"] | {
        "evidence_document_count": 11,
        "evidence_page_count": 36,
        "evidence_anchor_count": 36,
        "component_relation_evidence_link_count": 7,
        "product_bom_alternate_evidence_link_count": 3,
    } == manifest["entity_counts"]
    assert manifest["entity_counts"]["administrator_count"] == 1
    assert manifest["administrator_credentials_changed"] is False
    assert manifest["password_hash_printed_or_replaced"] is False
    assert manifest["real_history_business_rows_remaining"] == 0


def test_portfolio_baseline_manifest_v6_has_exact_cable_drawer_truth():
    path = (
        Path(__file__).resolve().parents[2]
        / "portfolio_demo_data"
        / "v2_3"
        / "portfolio_baseline_manifest_v6.json"
    )
    manifest = json.loads(path.read_text(encoding="utf-8"))
    counts = manifest["entity_counts"]
    assert manifest["manifest_version"] == 6
    assert manifest["phase"] == "2.5.1"
    assert manifest["source_release_tag"] == "portfolio-v2.3"
    assert manifest["alembic_revision"] == "0011_engineering_evidence"
    assert manifest["cable_location_layout_version"] == "portfolio_cable_drawer_rack_v1"
    assert counts["location_count"] == 857
    assert counts["cable_count"] == 80
    assert counts["cable_positive_inventory_lot_count"] == 80
    assert counts["cable_drawer_inventory_quantity"] == 1186
    assert counts["cable_legacy_source_inventory_quantity"] == 0
    assert manifest["administrator_credentials_changed"] is False
    assert manifest["password_hash_printed_or_replaced"] is False
    assert manifest["real_history_business_rows_remaining"] == 0


def test_portfolio_baseline_manifest_v7_has_only_real_vendor_evidence():
    path = (
        Path(__file__).resolve().parents[2]
        / "portfolio_demo_data"
        / "v2_4"
        / "portfolio_baseline_manifest_v7.json"
    )
    manifest = json.loads(path.read_text(encoding="utf-8"))
    counts = manifest["entity_counts"]
    assert manifest["manifest_version"] == 7
    assert manifest["phase"] == "2.5.4"
    assert manifest["source_release_tag"] is None
    assert manifest["suggested_release_tag"] == "portfolio-v2.3.4"
    assert manifest["real_datasheet_dataset_version"] == "portfolio_real_datasheets_v1"
    assert manifest["synthetic_datasheet_dataset_version"] is None
    assert counts["evidence_document_count"] == 8
    assert counts["evidence_page_count"] == 722
    assert counts["evidence_anchor_count"] == 10
    assert counts["vendor_upload_document_count"] == 8
    assert counts["synthetic_fixture_document_count"] == 0
    assert counts["component_relation_evidence_link_count"] == 0
    assert counts["product_bom_alternate_evidence_link_count"] == 0
    assert counts["administrator_count"] == 1
    assert manifest["administrator_credentials_changed"] is False
    assert manifest["password_hash_printed_or_replaced"] is False
    assert manifest["real_history_business_rows_remaining"] == 0
