"""Add versioned layout blocks and native text quality metadata.

Revision ID: 0015_evidence_layout_index
Revises: 0014_agent_episode_trace
"""

import sqlalchemy as sa

from alembic import op

revision = "0015_evidence_layout_index"
down_revision = "0014_agent_episode_trace"
branch_labels = None
depends_on = None


def _index_names(inspector: sa.Inspector, table_name: str) -> set[str]:
    return {str(index["name"]) for index in inspector.get_indexes(table_name) if index.get("name")}


def _create_block_table() -> None:
    op.create_table(
        "engineering_document_blocks",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("document_page_id", sa.Integer(), nullable=False),
        sa.Column("block_index", sa.Integer(), nullable=False),
        sa.Column("block_type", sa.String(length=32), nullable=False),
        sa.Column("reading_order", sa.Integer(), nullable=False),
        sa.Column("text_content", sa.Text(), nullable=False, server_default=""),
        sa.Column("text_sha256", sa.String(length=64), nullable=False),
        sa.Column("bbox", sa.JSON(), nullable=True),
        sa.Column(
            "location_status",
            sa.String(length=32),
            nullable=False,
            server_default="location_unavailable",
        ),
        sa.Column("provenance", sa.JSON(), nullable=True),
        sa.Column("extractor_version", sa.String(length=64), nullable=False),
        sa.Column("source_sha256", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "block_type IN ('heading', 'paragraph', 'table', 'pin_description', "
            "'application_circuit', 'unparsed')",
            name="ck_engineering_document_block_type",
        ),
        sa.CheckConstraint(
            "reading_order > 0",
            name="ck_engineering_document_block_reading_order",
        ),
        sa.CheckConstraint(
            "location_status IN ('available', 'location_unavailable')",
            name="ck_engineering_document_block_location_status",
        ),
        sa.ForeignKeyConstraint(
            ["document_page_id"],
            ["engineering_document_pages.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "document_page_id",
            "block_index",
            "extractor_version",
            name="uq_engineering_document_block_version",
        ),
    )


def _create_block_indexes(inspector: sa.Inspector) -> None:
    index_names = _index_names(inspector, "engineering_document_blocks")
    indexes = (
        (
            "ix_engineering_document_blocks_document_page_id",
            ["document_page_id"],
        ),
        ("ix_engineering_document_blocks_text_sha256", ["text_sha256"]),
        ("ix_engineering_document_blocks_source_sha", ["source_sha256"]),
        (
            "ix_engineering_document_blocks_page_order",
            ["document_page_id", "reading_order"],
        ),
    )
    for name, columns in indexes:
        if name not in index_names:
            op.create_index(name, "engineering_document_blocks", columns)


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    page_columns = {
        str(column["name"]) for column in inspector.get_columns("engineering_document_pages")
    }
    if "native_text_quality" not in page_columns:
        op.add_column(
            "engineering_document_pages",
            sa.Column("native_text_quality", sa.JSON(), nullable=True),
        )
    if "ocr_status" not in page_columns:
        op.add_column(
            "engineering_document_pages",
            sa.Column(
                "ocr_status",
                sa.String(length=32),
                nullable=False,
                server_default="not_assessed",
            ),
        )
    inspector = sa.inspect(bind)
    page_indexes = _index_names(inspector, "engineering_document_pages")
    if "ix_engineering_document_pages_ocr_status" not in page_indexes:
        op.create_index(
            "ix_engineering_document_pages_ocr_status",
            "engineering_document_pages",
            ["ocr_status"],
        )

    inspector = sa.inspect(bind)
    if not inspector.has_table("engineering_document_blocks"):
        _create_block_table()
    inspector = sa.inspect(bind)
    _create_block_indexes(inspector)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if inspector.has_table("engineering_document_blocks"):
        index_names = _index_names(inspector, "engineering_document_blocks")
        for name in (
            "ix_engineering_document_blocks_page_order",
            "ix_engineering_document_blocks_source_sha",
            "ix_engineering_document_blocks_source_sha256",
            "ix_engineering_document_blocks_text_sha256",
            "ix_engineering_document_blocks_document_page_id",
        ):
            if name in index_names:
                op.drop_index(name, table_name="engineering_document_blocks")
        op.drop_table("engineering_document_blocks")

    inspector = sa.inspect(bind)
    page_indexes = _index_names(inspector, "engineering_document_pages")
    if "ix_engineering_document_pages_ocr_status" in page_indexes:
        op.drop_index(
            "ix_engineering_document_pages_ocr_status",
            table_name="engineering_document_pages",
        )
    page_columns = {
        str(column["name"]) for column in inspector.get_columns("engineering_document_pages")
    }
    if "ocr_status" in page_columns:
        op.drop_column("engineering_document_pages", "ocr_status")
    if "native_text_quality" in page_columns:
        op.drop_column("engineering_document_pages", "native_text_quality")
