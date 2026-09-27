"""Add engineering evidence and hardened review lifecycle.

Revision ID: 0011_engineering_evidence
Revises: 0010_component_relations
"""

import sqlalchemy as sa

from alembic import op

revision = "0011_engineering_evidence"
down_revision = "0010_component_relations"
branch_labels = None
depends_on = None


def _upgrade_relation_lifecycle() -> None:
    existing_columns = {
        column["name"]
        for column in sa.inspect(op.get_bind()).get_columns("component_relations")
    }
    if "reviewed_by_id" in existing_columns:
        return
    op.add_column(
        "component_relations", sa.Column("reviewed_by_id", sa.Integer(), nullable=True)
    )
    op.add_column(
        "component_relations",
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "component_relations", sa.Column("revoked_by_id", sa.Integer(), nullable=True)
    )
    op.add_column(
        "component_relations",
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "component_relations",
        sa.Column("revoked_reason", sa.Text(), nullable=False, server_default=""),
    )
    if op.get_bind().dialect.name == "postgresql":
        op.create_foreign_key(
            "fk_relation_reviewed_by",
            "component_relations",
            "users",
            ["reviewed_by_id"],
            ["id"],
        )
        op.create_foreign_key(
            "fk_relation_revoked_by",
            "component_relations",
            "users",
            ["revoked_by_id"],
            ["id"],
        )
        op.drop_constraint(
            "ck_component_relation_status", "component_relations", type_="check"
        )
        op.create_check_constraint(
            "ck_component_relation_status",
            "component_relations",
            "status IN ('candidate', 'validated', 'rejected', 'revoked')",
        )
    else:
        with op.batch_alter_table("component_relations", recreate="always") as batch:
            batch.drop_constraint("ck_component_relation_status", type_="check")
            batch.create_check_constraint(
                "ck_component_relation_status",
                "status IN ('candidate', 'validated', 'rejected', 'revoked')",
            )
    op.create_index(
        "ix_component_relations_reviewed_by_id",
        "component_relations",
        ["reviewed_by_id"],
    )
    op.create_index(
        "ix_component_relations_revoked_by_id",
        "component_relations",
        ["revoked_by_id"],
    )
    op.execute(
        sa.text(
            "UPDATE component_relations "
            "SET reviewed_by_id = validated_by_id, reviewed_at = validated_at "
            "WHERE status IN ('validated', 'rejected')"
        )
    )
    op.execute(
        sa.text(
            "UPDATE component_relations "
            "SET validated_by_id = NULL, validated_at = NULL "
            "WHERE status = 'rejected'"
        )
    )


def _upgrade_alternate_lifecycle() -> None:
    existing_columns = {
        column["name"]
        for column in sa.inspect(op.get_bind()).get_columns("product_bom_alternates")
    }
    if "reviewed_by_id" in existing_columns:
        return
    op.add_column(
        "product_bom_alternates",
        sa.Column("reviewed_by_id", sa.Integer(), nullable=True),
    )
    op.add_column(
        "product_bom_alternates",
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "product_bom_alternates",
        sa.Column("revoked_by_id", sa.Integer(), nullable=True),
    )
    op.add_column(
        "product_bom_alternates",
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "product_bom_alternates",
        sa.Column("revoked_reason", sa.Text(), nullable=False, server_default=""),
    )
    if op.get_bind().dialect.name == "postgresql":
        op.create_foreign_key(
            "fk_alternate_reviewed_by",
            "product_bom_alternates",
            "users",
            ["reviewed_by_id"],
            ["id"],
        )
        op.create_foreign_key(
            "fk_alternate_revoked_by",
            "product_bom_alternates",
            "users",
            ["revoked_by_id"],
            ["id"],
        )
        op.drop_constraint(
            "ck_product_bom_alternate_status",
            "product_bom_alternates",
            type_="check",
        )
        op.create_check_constraint(
            "ck_product_bom_alternate_status",
            "product_bom_alternates",
            "status IN ('candidate', 'approved', 'rejected', 'revoked')",
        )
    else:
        with op.batch_alter_table("product_bom_alternates", recreate="always") as batch:
            batch.drop_constraint("ck_product_bom_alternate_status", type_="check")
            batch.create_check_constraint(
                "ck_product_bom_alternate_status",
                "status IN ('candidate', 'approved', 'rejected', 'revoked')",
            )
    op.create_index(
        "ix_product_bom_alternates_reviewed_by_id",
        "product_bom_alternates",
        ["reviewed_by_id"],
    )
    op.create_index(
        "ix_product_bom_alternates_revoked_by_id",
        "product_bom_alternates",
        ["revoked_by_id"],
    )
    op.execute(
        sa.text(
            "UPDATE product_bom_alternates "
            "SET reviewed_by_id = approved_by_id, reviewed_at = approved_at "
            "WHERE status IN ('approved', 'rejected')"
        )
    )
    op.execute(
        sa.text(
            "UPDATE product_bom_alternates "
            "SET approved_by_id = NULL, approved_at = NULL "
            "WHERE status = 'rejected'"
        )
    )


def _create_evidence_tables() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if inspector.has_table("engineering_documents"):
        if bind.dialect.name == "postgresql" and not any(
            index["name"] == "ix_engineering_document_pages_fts"
            for index in inspector.get_indexes("engineering_document_pages")
        ):
            op.execute(
                "CREATE INDEX ix_engineering_document_pages_fts "
                "ON engineering_document_pages USING gin "
                "(to_tsvector('simple', coalesce(text_content, '')))"
            )
        return
    op.create_table(
        "engineering_documents",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("document_key", sa.String(length=100), nullable=False),
        sa.Column("scope_type", sa.String(length=32), nullable=False),
        sa.Column("material_id", sa.Integer(), nullable=True),
        sa.Column("product_revision_id", sa.Integer(), nullable=True),
        sa.Column("document_type", sa.String(length=32), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("manufacturer", sa.String(length=200), nullable=False, server_default=""),
        sa.Column("document_revision", sa.String(length=80), nullable=False, server_default=""),
        sa.Column("document_date", sa.Date(), nullable=True),
        sa.Column("source_type", sa.String(length=32), nullable=False, server_default="upload"),
        sa.Column("source_url", sa.String(length=1000), nullable=False, server_default=""),
        sa.Column("original_filename", sa.String(length=300), nullable=False, server_default=""),
        sa.Column("storage_key", sa.String(length=500), nullable=False, server_default=""),
        sa.Column("file_sha256", sa.String(length=64), nullable=False),
        sa.Column("page_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="current"),
        sa.Column("supersedes_document_id", sa.Integer(), nullable=True),
        sa.Column("ingest_status", sa.String(length=24), nullable=False, server_default="pending"),
        sa.Column("ingest_error", sa.Text(), nullable=False, server_default=""),
        sa.Column("extraction_version", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("created_by_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "scope_type IN ('material', 'product_revision')",
            name="ck_engineering_document_scope_type",
        ),
        sa.CheckConstraint(
            "document_type IN ('datasheet', 'errata', 'application_note', "
            "'engineering_note', 'synthetic_test')",
            name="ck_engineering_document_type",
        ),
        sa.CheckConstraint(
            "status IN ('current', 'superseded', 'withdrawn')",
            name="ck_engineering_document_status",
        ),
        sa.CheckConstraint(
            "ingest_status IN ('pending', 'ready', 'failed')",
            name="ck_engineering_document_ingest_status",
        ),
        sa.CheckConstraint(
            "((scope_type = 'material' AND material_id IS NOT NULL "
            "AND product_revision_id IS NULL) OR "
            "(scope_type = 'product_revision' AND product_revision_id IS NOT NULL "
            "AND material_id IS NULL))",
            name="ck_engineering_document_scope_owner",
        ),
        sa.ForeignKeyConstraint(["material_id"], ["materials.id"]),
        sa.ForeignKeyConstraint(["product_revision_id"], ["product_revisions.id"]),
        sa.ForeignKeyConstraint(
            ["supersedes_document_id"], ["engineering_documents.id"]
        ),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("document_key", name="uq_engineering_document_key"),
        sa.UniqueConstraint("file_sha256", name="uq_engineering_document_file_sha256"),
    )
    for name, columns in (
        ("ix_engineering_documents_document_key", ["document_key"]),
        ("ix_engineering_documents_material_id", ["material_id"]),
        ("ix_engineering_documents_product_revision_id", ["product_revision_id"]),
        ("ix_engineering_documents_file_sha256", ["file_sha256"]),
        ("ix_engineering_documents_status", ["status"]),
        ("ix_engineering_documents_ingest_status", ["ingest_status"]),
        ("ix_engineering_documents_created_by_id", ["created_by_id"]),
        (
            "ix_engineering_document_material_status",
            ["material_id", "status", "ingest_status"],
        ),
        (
            "ix_engineering_document_revision_status",
            ["product_revision_id", "status", "ingest_status"],
        ),
    ):
        op.create_index(name, "engineering_documents", columns)

    op.create_table(
        "engineering_document_pages",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("document_id", sa.Integer(), nullable=False),
        sa.Column("page_number", sa.Integer(), nullable=False),
        sa.Column("text_content", sa.Text(), nullable=False, server_default=""),
        sa.Column("text_sha256", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "page_number > 0", name="ck_engineering_document_page_positive"
        ),
        sa.ForeignKeyConstraint(
            ["document_id"], ["engineering_documents.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "document_id", "page_number", name="uq_engineering_document_page"
        ),
    )
    op.create_index(
        "ix_engineering_document_pages_document_id",
        "engineering_document_pages",
        ["document_id"],
    )
    op.create_index(
        "ix_engineering_document_pages_text_sha256",
        "engineering_document_pages",
        ["text_sha256"],
    )
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            "CREATE INDEX ix_engineering_document_pages_fts "
            "ON engineering_document_pages USING gin "
            "(to_tsvector('simple', coalesce(text_content, '')))"
        )

    op.create_table(
        "evidence_anchors",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("document_page_id", sa.Integer(), nullable=False),
        sa.Column("section_title", sa.String(length=300), nullable=False, server_default=""),
        sa.Column("excerpt_text", sa.Text(), nullable=False),
        sa.Column("excerpt_sha256", sa.String(length=64), nullable=False),
        sa.Column("structured_fact", sa.JSON(), nullable=True),
        sa.Column("anchor_source", sa.String(length=32), nullable=False, server_default="extracted"),
        sa.Column("created_by_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "anchor_source IN ('extracted', 'deterministic_parser', 'human')",
            name="ck_evidence_anchor_source",
        ),
        sa.ForeignKeyConstraint(
            ["document_page_id"], ["engineering_document_pages.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "document_page_id",
            "excerpt_sha256",
            name="uq_evidence_anchor_page_excerpt",
        ),
    )
    op.create_index(
        "ix_evidence_anchors_document_page_id", "evidence_anchors", ["document_page_id"]
    )
    op.create_index(
        "ix_evidence_anchors_excerpt_sha256", "evidence_anchors", ["excerpt_sha256"]
    )

    op.create_table(
        "component_relation_evidence_links",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("component_relation_id", sa.Integer(), nullable=False),
        sa.Column("evidence_anchor_id", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=24), nullable=False, server_default="supporting"),
        sa.Column("review_note", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "role IN ('supporting', 'contradicting', 'context')",
            name="ck_component_relation_evidence_role",
        ),
        sa.ForeignKeyConstraint(
            ["component_relation_id"], ["component_relations.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["evidence_anchor_id"], ["evidence_anchors.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "component_relation_id",
            "evidence_anchor_id",
            name="uq_component_relation_evidence",
        ),
    )
    op.create_index(
        "ix_crel_evidence_relation_id",
        "component_relation_evidence_links",
        ["component_relation_id"],
    )
    op.create_index(
        "ix_crel_evidence_anchor_id",
        "component_relation_evidence_links",
        ["evidence_anchor_id"],
    )

    op.create_table(
        "product_bom_alternate_evidence_links",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("product_bom_alternate_id", sa.Integer(), nullable=False),
        sa.Column("evidence_anchor_id", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=24), nullable=False, server_default="supporting"),
        sa.Column("review_note", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "role IN ('supporting', 'contradicting', 'context')",
            name="ck_product_bom_alternate_evidence_role",
        ),
        sa.ForeignKeyConstraint(
            ["product_bom_alternate_id"],
            ["product_bom_alternates.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["evidence_anchor_id"], ["evidence_anchors.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "product_bom_alternate_id",
            "evidence_anchor_id",
            name="uq_product_bom_alternate_evidence",
        ),
    )
    op.create_index(
        "ix_pba_evidence_alternate_id",
        "product_bom_alternate_evidence_links",
        ["product_bom_alternate_id"],
    )
    op.create_index(
        "ix_pba_evidence_anchor_id",
        "product_bom_alternate_evidence_links",
        ["evidence_anchor_id"],
    )


def upgrade() -> None:
    _upgrade_relation_lifecycle()
    _upgrade_alternate_lifecycle()
    _create_evidence_tables()


def downgrade() -> None:
    bind = op.get_bind()
    revoked_relations = bind.execute(
        sa.text("SELECT count(*) FROM component_relations WHERE status = 'revoked'")
    ).scalar_one()
    revoked_alternates = bind.execute(
        sa.text("SELECT count(*) FROM product_bom_alternates WHERE status = 'revoked'")
    ).scalar_one()
    if revoked_relations or revoked_alternates:
        raise RuntimeError(
            "0011 downgrade refused: revoked engineering decisions cannot be represented by 0010"
        )

    for table in (
        "product_bom_alternate_evidence_links",
        "component_relation_evidence_links",
        "evidence_anchors",
    ):
        op.drop_table(table)
    if bind.dialect.name == "postgresql":
        op.execute("DROP INDEX IF EXISTS ix_engineering_document_pages_fts")
    op.drop_table("engineering_document_pages")
    op.drop_table("engineering_documents")

    sqlite_fk_names = {
        "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s"
    }
    alternate_fks = {
        tuple(item["constrained_columns"])
        for item in sa.inspect(bind).get_foreign_keys("product_bom_alternates")
    }
    alternate_batch_options = (
        {"recreate": "always", "naming_convention": sqlite_fk_names}
        if bind.dialect.name == "sqlite"
        else {}
    )
    with op.batch_alter_table(
        "product_bom_alternates", **alternate_batch_options
    ) as batch:
        batch.drop_index("ix_product_bom_alternates_revoked_by_id")
        batch.drop_index("ix_product_bom_alternates_reviewed_by_id")
        batch.drop_constraint("ck_product_bom_alternate_status", type_="check")
        batch.create_check_constraint(
            "ck_product_bom_alternate_status",
            "status IN ('candidate', 'approved', 'rejected')",
        )
        if bind.dialect.name == "postgresql":
            batch.drop_constraint("fk_alternate_revoked_by", type_="foreignkey")
            batch.drop_constraint("fk_alternate_reviewed_by", type_="foreignkey")
        else:
            if ("revoked_by_id",) in alternate_fks:
                batch.drop_constraint(
                    "fk_product_bom_alternates_revoked_by_id_users",
                    type_="foreignkey",
                )
            if ("reviewed_by_id",) in alternate_fks:
                batch.drop_constraint(
                    "fk_product_bom_alternates_reviewed_by_id_users",
                    type_="foreignkey",
                )
        batch.drop_column("revoked_reason")
        batch.drop_column("revoked_at")
        batch.drop_column("revoked_by_id")
        batch.drop_column("reviewed_at")
        batch.drop_column("reviewed_by_id")

    relation_fks = {
        tuple(item["constrained_columns"])
        for item in sa.inspect(bind).get_foreign_keys("component_relations")
    }
    relation_batch_options = (
        {"recreate": "always", "naming_convention": sqlite_fk_names}
        if bind.dialect.name == "sqlite"
        else {}
    )
    with op.batch_alter_table(
        "component_relations", **relation_batch_options
    ) as batch:
        batch.drop_index("ix_component_relations_revoked_by_id")
        batch.drop_index("ix_component_relations_reviewed_by_id")
        batch.drop_constraint("ck_component_relation_status", type_="check")
        batch.create_check_constraint(
            "ck_component_relation_status",
            "status IN ('candidate', 'validated', 'rejected')",
        )
        if bind.dialect.name == "postgresql":
            batch.drop_constraint("fk_relation_revoked_by", type_="foreignkey")
            batch.drop_constraint("fk_relation_reviewed_by", type_="foreignkey")
        else:
            if ("revoked_by_id",) in relation_fks:
                batch.drop_constraint(
                    "fk_component_relations_revoked_by_id_users",
                    type_="foreignkey",
                )
            if ("reviewed_by_id",) in relation_fks:
                batch.drop_constraint(
                    "fk_component_relations_reviewed_by_id_users",
                    type_="foreignkey",
                )
        batch.drop_column("revoked_reason")
        batch.drop_column("revoked_at")
        batch.drop_column("revoked_by_id")
        batch.drop_column("reviewed_at")
        batch.drop_column("reviewed_by_id")
