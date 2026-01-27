"""Add window_features table

Revision ID: 003
Revises: 002
Create Date: 2024-01-27

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '003'
down_revision = '002'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'window_features',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('dataset_id', sa.Integer(), nullable=False),
        sa.Column('window_id', sa.Integer(), nullable=False),
        sa.Column('sma5_slope_5', sa.Float(), nullable=False),
        sa.Column('sma20_slope_5', sa.Float(), nullable=False),
        sa.Column('sma20_slope_20', sa.Float(), nullable=False),
        sa.Column('sma60_slope_20', sa.Float(), nullable=False),
        sa.Column('close_to_sma20', sa.Float(), nullable=False),
        sa.Column('spread_5_20', sa.Float(), nullable=False),
        sa.Column('spread_20_60', sa.Float(), nullable=False),
        sa.Column('atr14', sa.Float(), nullable=False),
        sa.Column('vol_mean', sa.Float(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['dataset_id'], ['datasets.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['window_id'], ['windows.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('window_id'),
    )
    op.create_index('ix_window_features_dataset', 'window_features', ['dataset_id'])
    op.create_index('ix_window_features_dataset_window', 'window_features', ['dataset_id', 'window_id'])


def downgrade() -> None:
    op.drop_index('ix_window_features_dataset_window', table_name='window_features')
    op.drop_index('ix_window_features_dataset', table_name='window_features')
    op.drop_table('window_features')
