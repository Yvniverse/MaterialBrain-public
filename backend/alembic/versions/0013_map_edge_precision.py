"""Preserve the routing oracle's four-decimal edge distances.

Revision ID: 0013_map_edge_precision
Revises: 0012_picking_core
"""

import sqlalchemy as sa
from alembic import op

revision = "0013_map_edge_precision"
down_revision = "0012_picking_core"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("warehouse_map_edges") as batch:
        batch.alter_column(
            "distance_m",
            existing_type=sa.Numeric(10, 3),
            type_=sa.Numeric(11, 4),
            existing_nullable=False,
        )


def downgrade():
    connection = op.get_bind()
    if connection.execute(
        sa.text(
            "SELECT 1 FROM warehouse_map_edges WHERE distance_m != round(distance_m, 3) LIMIT 1"
        )
    ).first():
        raise RuntimeError("Cannot discard four-decimal map distances during downgrade")
    with op.batch_alter_table("warehouse_map_edges") as batch:
        batch.alter_column(
            "distance_m",
            existing_type=sa.Numeric(11, 4),
            type_=sa.Numeric(10, 3),
            existing_nullable=False,
        )
