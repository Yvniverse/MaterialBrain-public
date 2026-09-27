"""Add configurable organizer layout fields."""

import sqlalchemy as sa

from alembic import op

revision = "0002_organizer_layout"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade():
    existing = {
        column["name"] for column in sa.inspect(op.get_bind()).get_columns("locations")
    }
    if "organizer_style" not in existing:
        op.add_column(
            "locations", sa.Column("organizer_style", sa.String(length=32), nullable=True)
        )
    if "organizer_left_module" not in existing:
        op.add_column(
            "locations",
            sa.Column("organizer_left_module", sa.String(length=16), nullable=True),
        )
    if "organizer_right_module" not in existing:
        op.add_column(
            "locations",
            sa.Column("organizer_right_module", sa.String(length=16), nullable=True),
        )
    op.execute(
        "UPDATE locations SET organizer_style = 'standard_56', "
        "organizer_left_module = 'small', organizer_right_module = 'small' "
        "WHERE type = 'box'"
    )


def downgrade():
    existing = {
        column["name"] for column in sa.inspect(op.get_bind()).get_columns("locations")
    }
    for column_name in (
        "organizer_right_module",
        "organizer_left_module",
        "organizer_style",
    ):
        if column_name in existing:
            op.drop_column("locations", column_name)
