"""Add durable human review and correction records."""
from alembic import op
import sqlalchemy as sa

revision = "0002_persistent_reviews"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "review_records",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("artifact_id", sa.String(36), sa.ForeignKey("artifacts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("corrected_artifact_id", sa.String(36), sa.ForeignKey("artifacts.id", ondelete="SET NULL"), nullable=True),
        sa.Column("stage", sa.String(32), nullable=False),
        sa.Column("revision_number", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("decision", sa.String(24), nullable=False),
        sa.Column("comments", sa.Text(), nullable=False, server_default=""),
        sa.Column("correction", sa.JSON(), nullable=True),
        sa.Column("ratings", sa.JSON(), nullable=False),
        sa.Column("review_duration_ms", sa.Integer(), nullable=True),
        sa.Column("original_content", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_review_records_run_id", "review_records", ["run_id"])
    op.create_index("ix_review_records_artifact_id", "review_records", ["artifact_id"])
    op.create_index("ix_review_records_stage", "review_records", ["stage"])


def downgrade():
    op.drop_index("ix_review_records_stage", table_name="review_records")
    op.drop_index("ix_review_records_artifact_id", table_name="review_records")
    op.drop_index("ix_review_records_run_id", table_name="review_records")
    op.drop_table("review_records")
