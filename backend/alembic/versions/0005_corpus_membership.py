"""Track membership in the frozen comparison corpus."""
from alembic import op
import sqlalchemy as sa

revision = "0005_corpus_membership"
down_revision = "0004_screening_decisions"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("papers", sa.Column("in_corpus", sa.Boolean(), nullable=False, server_default=sa.true()))


def downgrade():
    op.drop_column("papers", "in_corpus")
