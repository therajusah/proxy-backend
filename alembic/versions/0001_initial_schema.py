"""Create the durable RelayNorth control-plane schema."""

from alembic import op
import sqlalchemy as sa


revision = "0001_initial_schema"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table("roles", sa.Column("id", sa.String(40), primary_key=True), sa.Column("name", sa.String(40), nullable=False, unique=True), if_not_exists=True)
    op.create_table("users", sa.Column("id", sa.String(64), primary_key=True), sa.Column("email", sa.String(320), nullable=False, unique=True), sa.Column("password_hash", sa.String(255), nullable=False), sa.Column("role_id", sa.String(40), sa.ForeignKey("roles.id"), nullable=False), sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()), sa.Column("created_at", sa.DateTime(), nullable=False), if_not_exists=True)
    op.create_table("regions", sa.Column("cell_id", sa.String(40), primary_key=True), sa.Column("display_name", sa.String(80), nullable=False), sa.Column("country", sa.String(80), nullable=False), sa.Column("health_state", sa.String(20), nullable=False, server_default="ready"), sa.Column("capacity", sa.Integer(), nullable=False, server_default="72"), sa.Column("egress_ip_pool", sa.JSON(), nullable=False), sa.Column("drain_deadline", sa.DateTime()), sa.Column("policy_version", sa.Integer(), nullable=False, server_default="1"), if_not_exists=True)
    op.create_table("sessions", sa.Column("id", sa.String(64), primary_key=True), sa.Column("cell_id", sa.String(40), sa.ForeignKey("regions.cell_id"), nullable=False), sa.Column("origin", sa.String(512), nullable=False), sa.Column("client_ip_hash", sa.String(128), nullable=False), sa.Column("created_at", sa.DateTime(), nullable=False), sa.Column("expires_at", sa.DateTime(), nullable=False), sa.Column("request_count", sa.Integer(), nullable=False, server_default="0"), sa.Column("bytes_out", sa.Integer(), nullable=False, server_default="0"), if_not_exists=True)
    op.create_table("blog_categories", sa.Column("id", sa.String(64), primary_key=True), sa.Column("name", sa.String(80), nullable=False, unique=True), if_not_exists=True)
    op.create_table("blog_posts", sa.Column("id", sa.String(64), primary_key=True), sa.Column("slug", sa.String(180), nullable=False, unique=True), sa.Column("title", sa.String(140), nullable=False), sa.Column("excerpt", sa.String(280), nullable=False), sa.Column("body_markdown", sa.Text(), nullable=False), sa.Column("status", sa.String(20), nullable=False), sa.Column("category_id", sa.String(64), sa.ForeignKey("blog_categories.id"), nullable=False), sa.Column("tags", sa.JSON(), nullable=False), sa.Column("seo_title", sa.String(180), nullable=False), sa.Column("seo_description", sa.String(320), nullable=False), sa.Column("author_id", sa.String(64), sa.ForeignKey("users.id"), nullable=False), sa.Column("created_at", sa.DateTime(), nullable=False), sa.Column("updated_at", sa.DateTime(), nullable=False), sa.Column("published_at", sa.DateTime()), if_not_exists=True)
    op.create_table("blog_revisions", sa.Column("id", sa.String(64), primary_key=True), sa.Column("post_id", sa.String(64), sa.ForeignKey("blog_posts.id"), nullable=False), sa.Column("body_markdown", sa.Text(), nullable=False), sa.Column("editor_id", sa.String(64), sa.ForeignKey("users.id"), nullable=False), sa.Column("created_at", sa.DateTime(), nullable=False), if_not_exists=True)
    op.create_table("blog_tags", sa.Column("id", sa.String(64), primary_key=True), sa.Column("name", sa.String(80), nullable=False, unique=True), if_not_exists=True)
    op.create_table("abuse_reports", sa.Column("id", sa.String(64), primary_key=True), sa.Column("category", sa.String(80), nullable=False), sa.Column("description", sa.Text(), nullable=False), sa.Column("status", sa.String(20), nullable=False, server_default="open"), sa.Column("created_at", sa.DateTime(), nullable=False), if_not_exists=True)
    op.create_table("blocked_destinations", sa.Column("id", sa.String(64), primary_key=True), sa.Column("origin", sa.String(512), nullable=False, unique=True), sa.Column("reason", sa.String(500), nullable=False), sa.Column("created_by", sa.String(64), sa.ForeignKey("users.id"), nullable=False), sa.Column("created_at", sa.DateTime(), nullable=False), if_not_exists=True)
    op.create_table("usage_aggregates", sa.Column("id", sa.String(64), primary_key=True), sa.Column("bucket_start", sa.DateTime(), nullable=False), sa.Column("cell_id", sa.String(40), sa.ForeignKey("regions.cell_id"), nullable=False), sa.Column("requests", sa.Integer(), nullable=False, server_default="0"), sa.Column("bytes_out", sa.Integer(), nullable=False, server_default="0"), sa.Column("blocked", sa.Integer(), nullable=False, server_default="0"), if_not_exists=True)
    op.create_table("audit_events", sa.Column("id", sa.String(64), primary_key=True), sa.Column("actor_id", sa.String(320), nullable=False), sa.Column("action", sa.String(120), nullable=False), sa.Column("target", sa.String(512), nullable=False), sa.Column("request_metadata", sa.JSON(), nullable=False), sa.Column("created_at", sa.DateTime(), nullable=False), if_not_exists=True)


def downgrade() -> None:
    for table in ("audit_events", "usage_aggregates", "blocked_destinations", "abuse_reports", "blog_tags", "blog_revisions", "blog_posts", "blog_categories", "sessions", "regions", "users", "roles"):
        op.drop_table(table)
