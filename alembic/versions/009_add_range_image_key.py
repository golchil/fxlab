"""Add range_image_key to trade_images table

Revision ID: 009
Revises: 008
Create Date: 2024-01-29

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '009'
down_revision = '008'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('trade_images', sa.Column('range_image_key', sa.String(512), nullable=True))


def downgrade() -> None:
    op.drop_column('trade_images', 'range_image_key')
