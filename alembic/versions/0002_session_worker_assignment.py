"""Persist the regional worker selected for each relay session."""

from alembic import op
import sqlalchemy as sa


revision = "0002_session_worker_assignment"
down_revision = "0001_initial_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("sessions", sa.Column("worker_id", sa.String(80), nullable=True))
    op.add_column("sessions", sa.Column("worker_url", sa.String(512), nullable=True))
    op.add_column("sessions", sa.Column("egress_ip", sa.String(80), nullable=True))


def downgrade() -> None:
    op.drop_column("sessions", "egress_ip")
    op.drop_column("sessions", "worker_url")
    op.drop_column("sessions", "worker_id")
