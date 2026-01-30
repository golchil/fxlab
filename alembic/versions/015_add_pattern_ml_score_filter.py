"""Add min_pattern_ml_score to strategies

Revision ID: 015
Revises: 014
Create Date: 2024-01-30

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '015'
down_revision = '014'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add min_pattern_ml_score to strategies for filtering patterns by ML score
    op.add_column('strategies', sa.Column('min_pattern_ml_score', sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column('strategies', 'min_pattern_ml_score')
