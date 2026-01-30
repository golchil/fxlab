"""Add HTF (Higher TimeFrame) columns to trade_images

Revision ID: 014
Revises: 013
Create Date: 2024-01-30

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '014'
down_revision = '013'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # HTF image columns for trade_images
    op.add_column('trade_images', sa.Column('htf_timeframe_id', sa.Integer(), nullable=True))
    op.add_column('trade_images', sa.Column('htf_entry_image_key', sa.String(512), nullable=True))
    op.add_column('trade_images', sa.Column('htf_exit_image_key', sa.String(512), nullable=True))
    op.add_column('trade_images', sa.Column('htf_range_image_key', sa.String(512), nullable=True))
    op.add_column('trade_images', sa.Column('htf_lookback_n', sa.Integer(), nullable=True))

    # Add FK constraint
    op.create_foreign_key(
        'fk_trade_images_htf_timeframe',
        'trade_images', 'timeframes',
        ['htf_timeframe_id'], ['id']
    )


def downgrade() -> None:
    op.drop_constraint('fk_trade_images_htf_timeframe', 'trade_images', type_='foreignkey')
    op.drop_column('trade_images', 'htf_lookback_n')
    op.drop_column('trade_images', 'htf_range_image_key')
    op.drop_column('trade_images', 'htf_exit_image_key')
    op.drop_column('trade_images', 'htf_entry_image_key')
    op.drop_column('trade_images', 'htf_timeframe_id')
