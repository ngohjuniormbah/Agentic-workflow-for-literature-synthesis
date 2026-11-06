"""Add reference annotations, independent evaluations, and error labels."""
from alembic import op
import sqlalchemy as sa

revision = "0003_research_evaluation"
down_revision = "0002_persistent_reviews"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "reference_annotations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("paper_id", sa.String(36), sa.ForeignKey("papers.id", ondelete="CASCADE"), nullable=False),
        sa.Column("annotator_id", sa.String(240), nullable=False),
        sa.Column("rubric_version", sa.String(120), nullable=False),
        sa.Column("annotations", sa.JSON(), nullable=False),
        sa.Column("comments", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_reference_annotations_project_id", "reference_annotations", ["project_id"])
    op.create_index("ix_reference_annotations_paper_id", "reference_annotations", ["paper_id"])

    op.create_table(
        "evaluation_records",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("artifact_id", sa.String(36), sa.ForeignKey("artifacts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("stage", sa.String(32), nullable=False),
        sa.Column("evaluator_id", sa.String(240), nullable=False),
        sa.Column("rubric_version", sa.String(120), nullable=False),
        sa.Column("scores", sa.JSON(), nullable=False),
        sa.Column("comments", sa.Text(), nullable=False, server_default=""),
        sa.Column("evidence_references", sa.JSON(), nullable=False),
        sa.Column("reference_paper_ids", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_evaluation_records_run_id", "evaluation_records", ["run_id"])
    op.create_index("ix_evaluation_records_artifact_id", "evaluation_records", ["artifact_id"])
    op.create_index("ix_evaluation_records_stage", "evaluation_records", ["stage"])

    op.create_table(
        "error_annotations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("artifact_id", sa.String(36), sa.ForeignKey("artifacts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("stage", sa.String(32), nullable=False),
        sa.Column("evaluator_id", sa.String(240), nullable=False),
        sa.Column("category", sa.String(64), nullable=False),
        sa.Column("severity", sa.String(24), nullable=False),
        sa.Column("source_evidence", sa.JSON(), nullable=False),
        sa.Column("human_correction", sa.Text(), nullable=False, server_default=""),
        sa.Column("corrected", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("propagated", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("comments", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_error_annotations_run_id", "error_annotations", ["run_id"])
    op.create_index("ix_error_annotations_artifact_id", "error_annotations", ["artifact_id"])
    op.create_index("ix_error_annotations_stage", "error_annotations", ["stage"])
    op.create_index("ix_error_annotations_category", "error_annotations", ["category"])


def downgrade():
    for index, table in (
        ("ix_error_annotations_category", "error_annotations"),
        ("ix_error_annotations_stage", "error_annotations"),
        ("ix_error_annotations_artifact_id", "error_annotations"),
        ("ix_error_annotations_run_id", "error_annotations"),
        ("ix_evaluation_records_stage", "evaluation_records"),
        ("ix_evaluation_records_artifact_id", "evaluation_records"),
        ("ix_evaluation_records_run_id", "evaluation_records"),
        ("ix_reference_annotations_paper_id", "reference_annotations"),
        ("ix_reference_annotations_project_id", "reference_annotations"),
    ):
        op.drop_index(index, table_name=table)
    op.drop_table("error_annotations")
    op.drop_table("evaluation_records")
    op.drop_table("reference_annotations")
