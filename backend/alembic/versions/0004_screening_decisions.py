"""Keep reviewer screening decisions distinct from frozen comparison corpus."""
from alembic import op
import sqlalchemy as sa

revision = "0004_screening_decisions"
down_revision = "0003_research_evaluation"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("review_records", sa.Column("screening_decisions", sa.JSON(), nullable=False, server_default="{}"))


def downgrade():
    op.drop_column("review_records", "screening_decisions")
