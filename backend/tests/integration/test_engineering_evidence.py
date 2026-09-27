import hashlib
import json
from pathlib import Path

import pytest
from pypdf import PdfWriter
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from app.agent.tools.common import ToolContext
from app.agent.tools.evidence import compare_component_evidence
from app.agent.tools.relations import get_component_relations, get_product_bom_alternates
from app.core.config import Settings, settings
from app.core.exceptions import BusinessError
from app.models import (
    AgentActionProposal,
    ComponentRelation,
    EngineeringDocument,
    EngineeringDocumentBlock,
    EngineeringDocumentPage,
    EvidenceAnchor,
    Material,
    ProductBomAlternate,
    ProductBomAlternateEvidenceLink,
    ProductBomItem,
    StockMovement,
    User,
)
from app.portfolio_demo.evidence_seeder import PortfolioEvidenceSeeder
from app.schemas.agent import (
    ComponentEvidenceCompareArgs,
    ComponentRelationsArgs,
    ProductBomAlternatesArgs,
)
from app.services.component_relations import ComponentRelationReviewService
from app.services.engineering_evidence import (
    EngineeringEvidenceIngestionService,
    EvidenceRetrievalService,
    _deterministic_linear_power_fact,
    _thermal_package_fact,
    is_synthetic_fixture_document,
)
from evals.seed_golden_db import seed_golden_database


@pytest.fixture()
def evidence_db(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'engineering-evidence-test.db'}"
    mapping = seed_golden_database(
        database_url,
        reset=True,
        fixture_profile="component-relations",
    )
    engine = create_engine(database_url)
    maker = sessionmaker(bind=engine, expire_on_commit=False)
    with maker() as db:
        operator = db.get(User, mapping["user_id"])
        yield db, operator, mapping
    engine.dispose()


def _material_id(db, code: str) -> int:
    value = db.scalar(select(Material.id).where(Material.code == code))
    assert value is not None
    return value


def _seed(db, operator):
    return PortfolioEvidenceSeeder(
        db,
        operator,
        Settings(portfolio_evidence_seed_enabled=True),
    ).seed()


def test_evidence_seed_is_exact_idempotent_and_write_safe(evidence_db):
    db, operator, _mapping = evidence_db
    before = (
        db.scalar(select(func.count(StockMovement.id))),
        db.scalar(select(func.count(AgentActionProposal.id))),
        db.scalar(select(func.count(ProductBomItem.id))),
    )
    first = _seed(db, operator)
    second = _seed(db, operator)
    after = (
        db.scalar(select(func.count(StockMovement.id))),
        db.scalar(select(func.count(AgentActionProposal.id))),
        db.scalar(select(func.count(ProductBomItem.id))),
    )

    assert first["document_count"] == 11
    assert first["page_count"] == 36
    assert first["anchor_count"] == 36
    assert first["relation_link_count"] == 7
    assert first["alternate_link_count"] == 3
    assert first["created_documents"] == 11
    documents = list(db.scalars(select(EngineeringDocument)).all())
    assert all(is_synthetic_fixture_document(document) for document in documents)
    atlas_note = next(
        document for document in documents if document.document_key == "SYN-ATLAS-CAN-NOTE-R1"
    )
    assert atlas_note.document_type == "engineering_note"
    assert atlas_note.source_type == "synthetic_fixture"
    assert first["write_sensitive_unchanged"] is True
    assert first["model_calls"] == first["ocr_calls"] == 0
    assert second["created_documents"] == 0
    assert second["created_relation_links"] == 0
    assert second["created_alternate_links"] == 0
    assert before == after


def test_current_revision_and_explicit_history_are_separated(evidence_db):
    db, operator, _mapping = evidence_db
    _seed(db, operator)
    material_id = _material_id(db, "PORT-CAN-MCP2562FD")
    retrieval = EvidenceRetrievalService(db)

    current = retrieval.search_material_evidence(
        material_ids=[material_id], query="current Pin 5 function", limit=6
    )
    history = retrieval.search_material_evidence(
        material_ids=[material_id],
        query="What did superseded Rev A say about Pin 5?",
        include_superseded=True,
        limit=6,
    )

    assert {item["document_key"] for item in current["citations"]} == {"SYN-CAN-B-RB"}
    assert any(fact.get("name") == "VIO" for fact in current["facts"])
    assert not any(fact.get("name") == "VREF" for fact in current["facts"])
    assert history["citations"][0]["document_key"] == "SYN-CAN-B-RA"
    assert any("VREF" in item["excerpt"] for item in history["citations"])


def test_layout_index_is_versioned_and_idempotent(evidence_db):
    db, operator, _mapping = evidence_db
    _seed(db, operator)
    blocks_before = list(db.scalars(select(EngineeringDocumentBlock)).all())
    assert blocks_before
    assert {block.extractor_version for block in blocks_before} <= {
        "pymupdf-blocks-v1",
        "pypdf-text-layout-fallback-v1",
    }
    assert all(block.source_sha256 and block.text_sha256 for block in blocks_before)
    assert all(block.reading_order > 0 for block in blocks_before)
    assert all(
        block.location_status in {"available", "location_unavailable"} for block in blocks_before
    )
    page = db.scalar(
        select(EngineeringDocumentPage).where(
            EngineeringDocumentPage.id == blocks_before[0].document_page_id
        )
    )
    assert page is not None
    assert page.native_text_quality is not None
    assert page.ocr_status in {"not_needed", "eligible_opt_in", "OCR_DEFERRED"}

    second = _seed(db, operator)
    blocks_after = list(db.scalars(select(EngineeringDocumentBlock)).all())
    assert second["created_documents"] == 0
    assert len(blocks_after) == len(blocks_before)
    assert {block.id for block in blocks_after} == {block.id for block in blocks_before}

    material_id = _material_id(db, "PORT-CAN-MCP2562FD")
    result = EvidenceRetrievalService(db).search_material_evidence(
        material_ids=[material_id], query="current Pin 5 function", limit=3
    )
    assert result["retrieval"]["strategy"] == "fts+structured+layout"
    assert result["retrieval"]["vector_status"] == "VECTOR_BLOCKED_BY_ENVIRONMENT"
    assert result["citations"][0]["layout_blocks"]
    assert result["citations"][0]["ranking"]["signals"]


def test_numeric_or_pin_claim_without_anchor_fact_is_insufficient(evidence_db):
    db, operator, _mapping = evidence_db
    _seed(db, operator)
    material_id = _material_id(db, "PORT-CAN-TCAN1044")
    result = EvidenceRetrievalService(db).search_material_evidence(
        material_ids=[material_id],
        query="TCAN1044 能直接接 1.8V VIO 吗？",
        limit=5,
    )
    assert result["citations"]
    assert result["evidence_coverage"] == "insufficient"
    assert not any(
        fact.get("field") in {"supply_voltage", "pin", "pin_5"}
        for fact in result["facts"]
    )


def test_exact_vio_pin_query_includes_same_anchor_grounded_companion_facts(evidence_db):
    db, operator, _mapping = evidence_db
    _seed(db, operator)
    material_id = _material_id(db, "PORT-CAN-MCP2562FD")
    anchor = db.scalar(
        select(EvidenceAnchor)
        .join(EngineeringDocumentPage)
        .join(EngineeringDocument)
        .where(
            EngineeringDocument.material_id == material_id,
            EngineeringDocument.status == "current",
        )
    )
    assert anchor is not None
    anchor.structured_fact = {
        "facts": [
            {"field": "interface", "values": ["CAN-FD"]},
            {"field": "pin", "number": 5, "name": "VIO"},
            {"field": "supply_voltage", "min": 1.8, "max": 5.5, "unit": "V", "rail": "VIO"},
            {
                "field": "purpose",
                "value": "VIO 为数字 I/O 供电并提供内部电平转换",
                "variant": "MCP2562FD",
            },
        ]
    }
    db.commit()

    result = EvidenceRetrievalService(db).search_material_evidence(
        material_ids=[material_id],
        query="MCP2562FD 的 5 脚到底是什么？",
        limit=6,
    )

    fields = {fact["field"] for fact in result["facts"]}
    assert {"pin", "interface", "supply_voltage", "purpose"}.issubset(fields)
    assert any(fact.get("name") == "VIO" for fact in result["facts"])
    assert result["evidence_coverage"] == "supported"


def test_variant_scoped_pin_query_excludes_sibling_variant_but_preserves_raw_audit(evidence_db):
    db, operator, _mapping = evidence_db
    _seed(db, operator)
    material_id = _material_id(db, "PORT-CAN-MCP2562FD")
    anchors = list(
        db.scalars(
            select(EvidenceAnchor)
        .join(EngineeringDocumentPage)
        .join(EngineeringDocument)
        .where(
            EngineeringDocument.material_id == material_id,
            EngineeringDocument.status == "current",
        )
        )
    )
    assert anchors
    variant_facts = [
        {"field": "pin", "number": 5, "name": "SPLIT", "variant": "MCP2561FD"},
        {"field": "pin", "number": 5, "name": "VIO", "variant": "MCP2562FD"},
        {"field": "package", "value": "SOIC-8L", "variant": "MCP2561FD"},
        {"field": "package", "value": "SOIC-8L", "variant": "MCP2562FD"},
        {
            "field": "output_voltage",
            "value": 1.8,
            "unit": "V",
            "variant": "MCP2561FD",
            "source_context": "cover typical application",
        },
    ]
    for anchor in anchors:
        anchor.structured_fact = {"facts": variant_facts}
    db.commit()

    result = EvidenceRetrievalService(db).search_material_evidence(
        material_ids=[material_id], query="MCP2562FD Pin 5", limit=6
    )

    pin_names = [fact.get("name") for fact in result["facts"] if fact.get("field") == "pin"]
    assert pin_names
    assert set(pin_names) == {"VIO"}
    assert any(fact.get("name") == "SPLIT" for fact in result["raw_facts"])
    assert result["evidence_coverage"] == "supported"

    explicit_sibling = EvidenceRetrievalService(db).search_material_evidence(
        material_ids=[material_id],
        query="MCP2561FD 的 Pin 5 和封装是什么？",
        limit=6,
    )
    explicit_pins = [
        fact for fact in explicit_sibling["facts"] if fact.get("field") == "pin"
    ]
    assert {fact.get("name") for fact in explicit_pins} == {"SPLIT"}
    assert any(
        fact.get("field") == "package"
        and fact.get("variant") == "MCP2561FD"
        and fact.get("value") == "SOIC-8L"
        for fact in explicit_sibling["facts"]
    )

    negative_sibling = EvidenceRetrievalService(db).search_material_evidence(
        material_ids=[material_id],
        query="MCP2562FD 的 Pin 5 是什么？不要把 MCP2561FD 的资料混用进来。",
        limit=6,
    )
    negative_pins = [
        fact for fact in negative_sibling["facts"] if fact.get("field") == "pin"
    ]
    assert {fact.get("name") for fact in negative_pins} == {"VIO"}

    cover = EvidenceRetrievalService(db).search_material_evidence(
        material_ids=[material_id],
        query="MCP2562FD 封面典型应用的型号和输出电压是多少？",
        limit=6,
    )
    assert any(
        fact.get("field") == "output_voltage" and fact.get("variant") == "MCP2561FD"
        for fact in cover["facts"]
    )
    assert cover["evidence_coverage"] == "supported"

    comparison = EvidenceRetrievalService(db).search_material_evidence(
        material_ids=[material_id],
        query="MCP2561FD 与 MCP2562FD 的 Pin 5 分别是什么？",
        limit=6,
    )
    comparison_pins = {
        (fact.get("variant"), fact.get("name"))
        for fact in comparison["facts"]
        if fact.get("field") == "pin"
    }
    assert comparison_pins == {
        ("MCP2561FD", "SPLIT"),
        ("MCP2562FD", "VIO"),
    }
    assert comparison["evidence_coverage"] == "supported"


def test_explicit_absolute_maximum_query_stays_insufficient_without_distinct_fact(evidence_db):
    db, operator, _mapping = evidence_db
    _seed(db, operator)
    material_id = _material_id(db, "PORT-BUCK-LM5164")

    result = EvidenceRetrievalService(db).search_material_evidence(
        material_ids=[material_id],
        query="LM5164 推荐输入电压和绝对最大输入电压是多少？",
        limit=6,
    )

    assert result["evidence_coverage"] == "insufficient"
    assert "input_voltage_absolute_max" in result["evidence_coverage_details"]["missing_fields"]


def test_peripheral_evidence_keeps_bst_and_cot_topics_separate(evidence_db):
    db, operator, _mapping = evidence_db
    _seed(db, operator)
    material_id = _material_id(db, "PORT-BUCK-LM5164")
    anchors = list(
        db.scalars(
            select(EvidenceAnchor)
            .join(EngineeringDocumentPage)
            .join(EngineeringDocument)
            .where(
                EngineeringDocument.material_id == material_id,
                EngineeringDocument.status == "current",
            )
            .order_by(EvidenceAnchor.id)
        )
        .all()
    )
    assert len(anchors) >= 2
    for index, anchor in enumerate(anchors):
        anchor.structured_fact = {
            "facts": (
                [
                    {
                        "field": "peripheral",
                        "value": "BST to SW requires 2.2 nF 50 V X7R",
                        "variant": "LM5164",
                    }
                ]
                if index == 0
                else [
                    {
                        "field": "peripheral",
                        "value": "COT feedback comparator requires at least 20 mV in-phase ripple",
                        "variant": "LM5164",
                    }
                ]
                if index == 1
                else []
            )
        }
    db.commit()
    retrieval = EvidenceRetrievalService(db)

    bst = retrieval.search_material_evidence(
        material_ids=[material_id], query="LM5164 的 BST 电容外围要求是什么？", limit=6
    )
    bst_values = [str(fact.get("value")) for fact in bst["facts"]]
    assert bst_values == ["BST to SW requires 2.2 nF 50 V X7R"]
    assert all("COT" not in value and "ripple" not in value for value in bst_values)

    ripple = retrieval.search_material_evidence(
        material_ids=[material_id],
        query="LM5164 的 COT 纹波注入要求是什么？不要混入 BST。",
        limit=6,
    )
    ripple_values = [str(fact.get("value")) for fact in ripple["facts"]]
    assert ripple_values == ["COT feedback comparator requires at least 20 mV in-phase ripple"]
    assert all("BST" not in value for value in ripple_values)


def test_exact_ldo_loss_uses_matching_vendor_conditions_and_labels_thermal_packages():
    raw_facts = [
        {"material_id": 700, "anchor_id": 1, "field": "topology", "value": "linear"},
        {
            "material_id": 700,
            "anchor_id": 2,
            "field": "power_dissipation",
            "variant": "TLV761",
            "conditions": {"vin_v": 12, "vout_v": 3.3, "load_current_a": 0.8},
        },
    ]
    derived = _deterministic_linear_power_fact(
        "TLV761 用 12V 输入、3.3V 输出、800mA 负载时的功耗是多少？",
        raw_facts,
        700,
    )

    assert derived is not None
    assert derived["value"] == 6.96
    assert derived["fact_type"] == "derived_calculation"
    assert "6.96 W" in derived["calculation"]

    package = _thermal_package_fact(
        {
            "material_id": 700,
            "anchor_id": 3,
            "field": "thermal_resistance",
            "variant": "TLV761",
            "value": {"DCY_SOT223": 95.4, "KVU_TO252": 67.2},
        }
    )
    assert package is not None
    assert package["value"] == "DCY (SOT-223) / KVU (TO-252)"


def test_server_material_scope_and_citation_allowlist(evidence_db):
    db, operator, _mapping = evidence_db
    _seed(db, operator)
    adc_id = _material_id(db, "PORT-ADC-ADS1220")
    buck_id = _material_id(db, "PORT-BUCK-LM5164")
    retrieval = EvidenceRetrievalService(db)
    result = retrieval.search_material_evidence(
        material_ids=[adc_id], query="ADC resolution and interface", limit=5
    )
    assert {item["document_key"] for item in result["citations"]} == {"SYN-ADC-A-R1"}

    with pytest.raises(BusinessError) as outside_allowlist:
        retrieval.validate_citations(
            anchor_ids=[999999],
            allowed_anchor_ids=result["allowed_anchor_ids"],
            material_ids=[adc_id],
        )
    assert outside_allowlist.value.code == "EVIDENCE_CITATION_NOT_ALLOWED"

    buck_anchor = db.scalar(
        select(EvidenceAnchor.id)
        .join(EngineeringDocumentPage)
        .join(EngineeringDocument)
        .where(EngineeringDocument.material_id == buck_id)
    )
    with pytest.raises(BusinessError) as wrong_scope:
        retrieval.validate_citations(
            anchor_ids=[buck_anchor],
            allowed_anchor_ids=[buck_anchor],
            material_ids=[adc_id],
        )
    assert wrong_scope.value.code == "EVIDENCE_CITATION_SCOPE_INVALID"


def test_component_comparison_is_independent_and_unknown_is_preserved(evidence_db):
    db, operator, _mapping = evidence_db
    _seed(db, operator)
    can_a = _material_id(db, "PORT-CAN-TCAN1044")
    can_b = _material_id(db, "PORT-CAN-MCP2562FD")
    tof_a = _material_id(db, "C91199")
    tof_b = _material_id(db, "PORT-TOF-VL53L1CX")
    context = ToolContext(db=db, user=operator, request_id="evidence-test")

    can_result = compare_component_evidence(
        context,
        ComponentEvidenceCompareArgs(
            first_material_id=can_a,
            second_material_id=can_b,
            fields=["supply_voltage", "pin_5"],
        ),
    )
    outcomes = {item["field"]: item["result"] for item in can_result["comparisons"]}
    assert outcomes == {"supply_voltage": "same", "pin_5": "different"}
    assert can_result["pin_compatible_supported"] is False
    assert can_result["automatic_decision"] is False

    tof_result = compare_component_evidence(
        context,
        ComponentEvidenceCompareArgs(
            first_material_id=tof_a,
            second_material_id=tof_b,
            fields=["package"],
        ),
    )
    assert tof_result["comparisons"][0]["result"] == "unknown"
    assert tof_result["conclusion"] == "当前证据不足"


def test_relation_and_alternate_tools_return_traceable_current_evidence(evidence_db):
    db, operator, _mapping = evidence_db
    _seed(db, operator)
    can_a = _material_id(db, "PORT-CAN-TCAN1044")
    can_b = _material_id(db, "PORT-CAN-MCP2562FD")
    context = ToolContext(db=db, user=operator, request_id="evidence-links")

    relations = get_component_relations(
        context, ComponentRelationsArgs(material_ids=[can_a, can_b])
    )
    flagship = next(item for item in relations["items"] if item["relation_type"] == "similar_to")
    assert {item["document_key"] for item in flagship["evidence_citations"]} == {
        "SYN-CAN-A-R1",
        "SYN-CAN-B-RB",
    }
    assert "SYN-CAN-B-RA" not in {item["document_key"] for item in flagship["evidence_citations"]}
    assert relations["pin_compatible_validated"] is False

    alternate_link = db.scalar(select(ProductBomAlternateEvidenceLink))
    assert alternate_link is not None
    alternate_row = db.get(ProductBomAlternate, alternate_link.product_bom_alternate_id)
    assert alternate_row is not None
    alternates = get_product_bom_alternates(
        context,
        ProductBomAlternatesArgs(product_bom_item_id=alternate_row.product_bom_item_id),
    )
    approved = next(item for item in alternates["items"] if item["status"] == "approved")
    assert {item["document_key"] for item in approved["evidence_citations"]} == {
        "SYN-CAN-A-R1",
        "SYN-CAN-B-RB",
        "SYN-ATLAS-CAN-NOTE-R1",
    }
    assert approved["scope"] == "product_revision_bom_position"
    assert approved["automatic_substitution"] is False


def test_superseded_evidence_preserves_history_but_requires_review(evidence_db, monkeypatch):
    db, operator, _mapping = evidence_db
    monkeypatch.setattr(settings, "traceable_engineering_evidence_required", True)
    _seed(db, operator)
    relation = db.scalar(select(ComponentRelation).where(ComponentRelation.status == "validated"))
    alternate = db.scalar(
        select(ProductBomAlternate).where(ProductBomAlternate.status == "approved")
    )
    assert relation is not None and alternate is not None

    for document in db.scalars(
        select(EngineeringDocument).where(EngineeringDocument.status == "current")
    ).all():
        document.status = "superseded"
    db.commit()

    review = ComponentRelationReviewService(db, operator, "evidence-freshness")
    relation_data = review.relation_data(relation)
    alternate_data = review.alternate_data(alternate)
    assert relation.status == "validated"
    assert relation_data["historically_validated"] is True
    assert relation_data["current_evidence_complete"] is False
    assert relation_data["currently_usable"] is False
    assert relation_data["review_required"] is True
    assert alternate.status == "approved"
    assert alternate_data["historically_approved"] is True
    assert alternate_data["current_evidence_complete"] is False
    assert alternate_data["currently_usable"] is False
    assert alternate_data["review_required"] is True

    tool_result = get_component_relations(
        ToolContext(db=db, user=operator, request_id="stale-evidence-tool"),
        ComponentRelationsArgs(material_id=relation.source_material_id),
    )
    current = next(item for item in tool_result["items"] if item["id"] == relation.id)
    assert current["historically_validated"] is True
    assert current["current_evidence_complete"] is False
    assert current["currently_usable"] is False
    assert tool_result["pin_compatible_validated"] is False


def test_no_ocr_and_same_sha_cannot_cross_scope(evidence_db, tmp_path):
    db, operator, _mapping = evidence_db
    _seed(db, operator)
    blank = tmp_path / "blank.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    with blank.open("wb") as handle:
        writer.write(handle)
    blank_sha = hashlib.sha256(blank.read_bytes()).hexdigest()
    definition = {
        "document_key": "SYN-BLANK",
        "scope_type": "material",
        "material_code": "PORT-CAN-TCAN1044",
        "document_type": "synthetic_test",
        "title": "Blank synthetic fixture",
        "manufacturer": "MaterialBrain Synthetic Lab",
        "document_revision": "R1",
        "document_date": "2026-08-30",
        "filename": blank.name,
        "sha256": blank_sha,
        "page_count": 1,
        "status": "current",
        "expected_pages": [{"page": 1, "heading": "Blank", "expected_phrases": []}],
    }
    ingestion = EngineeringEvidenceIngestionService(db, operator, "no-ocr")
    with pytest.raises(BusinessError) as no_text:
        ingestion.ingest_fixture(definition, blank)
    assert no_text.value.code == "TEXT_EXTRACTION_UNAVAILABLE"

    manifest_path = (
        Path(__file__).resolve().parents[2]
        / "portfolio_demo_data"
        / "v2_2"
        / "synthetic_datasheet_manifest_v1.json"
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    conflicting = dict(manifest["documents"][0])
    conflicting["material_code"] = "PORT-CAN-SN65HVD230"
    fixture = (
        Path(__file__).resolve().parents[2]
        / "evals"
        / "evidence"
        / "fixtures"
        / "synthetic_datasheets"
        / conflicting["filename"]
    )
    with pytest.raises(BusinessError) as conflict:
        ingestion.ingest_fixture(conflicting, fixture)
    assert conflict.value.code == "EVIDENCE_SHA_SCOPE_CONFLICT"
