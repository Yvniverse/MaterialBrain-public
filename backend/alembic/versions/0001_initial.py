"""Initial complete schema."""
from alembic import op
from app import models  # noqa: F401
from app.core.database import Base

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    # Phase 2.7 owns these tables in its explicit migration. The legacy bootstrap
    # imports live metadata; exclude later domains rather than creating them early.
    phase27 = {"pick_tasks", "pick_task_items", "pick_allocations", "warehouse_maps",
               "warehouse_map_nodes", "warehouse_map_edges", "location_map_bindings"}
    Base.metadata.create_all(bind=op.get_bind(), tables=[
        table for table in Base.metadata.sorted_tables if table.name not in phase27
    ])


def downgrade():
    Base.metadata.drop_all(bind=op.get_bind())
