"""Add lightweight content fields for organizer bins."""

import sqlalchemy as sa

from alembic import op

revision = "0003_bin_lightweight_content"
down_revision = "0002_organizer_layout"
branch_labels = None
depends_on = None


def upgrade():
    existing = {
        column["name"] for column in sa.inspect(op.get_bind()).get_columns("locations")
    }
    if "bin_material_name" not in existing:
        op.add_column(
            "locations",
            sa.Column(
                "bin_material_name",
                sa.String(length=200),
                nullable=False,
                server_default="",
            ),
        )
    if "bin_quantity" not in existing:
        op.add_column("locations", sa.Column("bin_quantity", sa.Integer(), nullable=True))
    if "bin_content_notes" not in existing:
        op.add_column(
            "locations",
            sa.Column(
                "bin_content_notes",
                sa.Text(),
                nullable=False,
                server_default="",
            ),
        )


def downgrade():
    existing = {
        column["name"] for column in sa.inspect(op.get_bind()).get_columns("locations")
    }
    for column_name in ("bin_content_notes", "bin_quantity", "bin_material_name"):
        if column_name in existing:
            op.drop_column("locations", column_name)
