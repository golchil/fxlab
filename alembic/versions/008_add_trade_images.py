"""Add trade_images table for trade visualization cache

Revision ID: 008
Revises: 007
Create Date: 2024-01-29

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '008'
down_revision = '007'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'trade_images',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('trade_id', sa.Integer(), nullable=False),
        sa.Column('entry_image_key', sa.String(512), nullable=True),
        sa.Column('exit_image_key', sa.String(512), nullable=True),
        sa.Column('lookback_n', sa.Integer(), nullable=False),
        sa.Column('ma_periods', sa.String(64), nullable=True),  # e.g., "5,20,60"
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(['trade_id'], ['trades.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_trade_images_trade', 'trade_images', ['trade_id'], unique=True)


def downgrade() -> None:
    op.drop_index('ix_trade_images_trade', table_name='trade_images')
    op.drop_table('trade_images')
