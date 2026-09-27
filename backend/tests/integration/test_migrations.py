import os
import subprocess
import sys
from pathlib import Path

from sqlalchemy import create_engine, inspect

BACKEND_DIR = Path(__file__).resolve().parents[2]


def _run_alembic(database_url: str, *args: str) -> None:
    environment = os.environ.copy()
    environment["DATABASE_URL"] = database_url
    result = subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=BACKEND_DIR,
        env=environment,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_fresh_upgrade_full_downgrade_and_reupgrade(tmp_path):
    database_path = tmp_path / "migration-roundtrip.db"
    database_url = f"sqlite:///{database_path.as_posix()}"

    _run_alembic(database_url, "upgrade", "head")
    inspector = inspect(create_engine(database_url))
    columns = {column["name"] for column in inspector.get_columns("agent_action_proposals")}
    assert {"client_operation_id", "payload_hash"}.issubset(columns)
    assert "uq_agent_proposal_business_operation" in {
        constraint["name"]
        for constraint in inspector.get_unique_constraints("agent_action_proposals")
    }
    conversation_columns = {
        column["name"] for column in inspector.get_columns("agent_conversation_contexts")
    }
    assert {
        "context_version",
        "selected_material_id",
        "selected_project_id",
        "selected_product_id",
        "selected_product_revision_id",
        "product_candidate_ids",
        "pending_disambiguation",
        "expires_at",
    }.issubset(conversation_columns)
    conversation_indexes = {
        index["name"] for index in inspector.get_indexes("agent_conversation_contexts")
    }
    assert {
        "ix_agent_conversation_contexts_user_id",
        "ix_agent_conversation_contexts_expires_at",
        "ix_agent_conversation_owner_status",
        "ix_agent_conversation_owner_lookup",
    }.issubset(conversation_indexes)

    assert {"products", "product_revisions", "product_bom_items"}.issubset(
        inspector.get_table_names()
    )
    assert "product_revision_id" in {column["name"] for column in inspector.get_columns("projects")}
    assert {"build_plans", "build_plan_items"}.issubset(inspector.get_table_names())
    assert {"component_relations", "product_bom_alternates"}.issubset(inspector.get_table_names())
    assert {
        "engineering_documents",
        "engineering_document_pages",
        "evidence_anchors",
        "component_relation_evidence_links",
        "product_bom_alternate_evidence_links",
    }.issubset(inspector.get_table_names())
    assert "agent_episodes" in inspector.get_table_names()
    assert {
        "task_contract_hash",
        "tool_schema_version",
        "steps",
        "grounded_facts",
        "final_result",
        "redaction_version",
    }.issubset({column["name"] for column in inspector.get_columns("agent_episodes")})
    relation_columns = {column["name"] for column in inspector.get_columns("component_relations")}
    alternate_columns = {
        column["name"] for column in inspector.get_columns("product_bom_alternates")
    }
    assert {
        "reviewed_by_id",
        "reviewed_at",
        "revoked_by_id",
        "revoked_at",
        "revoked_reason",
    }.issubset(relation_columns)
    assert {
        "reviewed_by_id",
        "reviewed_at",
        "revoked_by_id",
        "revoked_at",
        "revoked_reason",
    }.issubset(alternate_columns)
    assert {"bom_hash", "released_by_id"}.issubset(
        {column["name"] for column in inspector.get_columns("product_revisions")}
    )
    assert "last_build_quantity" in {
        column["name"] for column in inspector.get_columns("agent_conversation_contexts")
    }
    assert "uq_product_revisions_one_default" in {
        index["name"] for index in inspector.get_indexes("product_revisions")
    }

    _run_alembic(database_url, "downgrade", "0010_component_relations")
    phase_23 = inspect(create_engine(database_url))
    assert "engineering_documents" not in phase_23.get_table_names()
    assert "reviewed_by_id" not in {
        column["name"] for column in phase_23.get_columns("component_relations")
    }
    _run_alembic(database_url, "upgrade", "0011_engineering_evidence")

    _run_alembic(database_url, "downgrade", "0009_build_plan_governance")
    phase_22 = inspect(create_engine(database_url))
    assert "component_relations" not in phase_22.get_table_names()
    assert "product_bom_alternates" not in phase_22.get_table_names()
    _run_alembic(database_url, "upgrade", "0010_component_relations")
    phase_23 = inspect(create_engine(database_url))
    assert {"component_relations", "product_bom_alternates"}.issubset(phase_23.get_table_names())

    _run_alembic(database_url, "downgrade", "0008_product_revision_bom")
    phase_21 = inspect(create_engine(database_url))
    assert "build_plans" not in phase_21.get_table_names()
    assert "bom_hash" not in {
        column["name"] for column in phase_21.get_columns("product_revisions")
    }
    _run_alembic(database_url, "upgrade", "0009_build_plan_governance")
    assert "build_plans" in inspect(create_engine(database_url)).get_table_names()

    _run_alembic(database_url, "downgrade", "0007_agent_conversation_context")
    downgraded = inspect(create_engine(database_url))
    assert "products" not in downgraded.get_table_names()
    assert "product_revision_id" not in {
        column["name"] for column in downgraded.get_columns("projects")
    }
    assert "selected_product_id" not in {
        column["name"] for column in downgraded.get_columns("agent_conversation_contexts")
    }
    _run_alembic(database_url, "upgrade", "head")
    assert "products" in inspect(create_engine(database_url)).get_table_names()

    _run_alembic(database_url, "downgrade", "0006_agent_phase_1_5_hardening")
    assert (
        "agent_conversation_contexts" not in inspect(create_engine(database_url)).get_table_names()
    )
    _run_alembic(database_url, "upgrade", "head")
    assert "agent_conversation_contexts" in inspect(create_engine(database_url)).get_table_names()

    _run_alembic(database_url, "downgrade", "base")
    assert inspect(create_engine(database_url)).get_table_names() == ["alembic_version"]

    _run_alembic(database_url, "upgrade", "head")
    assert "agent_action_proposals" in inspect(create_engine(database_url)).get_table_names()
