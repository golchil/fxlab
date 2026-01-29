"""Add swing_points table and dow_timeframe_id to strategies

Revision ID: 011
Revises: 010
Create Date: 2024-01-29

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '011'
down_revision = '010'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # swing_points: ダウ理論の山谷（スイングハイ/ロー）
    op.create_table(
        'swing_points',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('dataset_id', sa.Integer(), nullable=False),
        sa.Column('instrument_id', sa.Integer(), nullable=False),
        sa.Column('timeframe_id', sa.Integer(), nullable=False),
        sa.Column('ts', sa.DateTime(timezone=True), nullable=False),
        sa.Column('kind', sa.String(16), nullable=False),  # "high" or "low"
        sa.Column('price', sa.Float(), nullable=False),
        sa.Column('method', sa.String(32), nullable=False),  # "zigzag"
        sa.Column('threshold_type', sa.String(16), nullable=False),  # "atr" or "pips"
        sa.Column('threshold_value', sa.Float(), nullable=False),
        sa.Column('min_bars', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(['dataset_id'], ['datasets.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['instrument_id'], ['instruments.id']),
        sa.ForeignKeyConstraint(['timeframe_id'], ['timeframes.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'ix_swing_points_lookup',
        'swing_points',
        ['dataset_id', 'instrument_id', 'timeframe_id', 'ts'],
    )

    # strategies に dow_timeframe_id を追加（ダウトレンド判定用の時間足）
    op.add_column(
        'strategies',
        sa.Column('dow_timeframe_id', sa.Integer(), sa.ForeignKey('timeframes.id'), nullable=True)
    )


def downgrade() -> None:
    op.drop_column('strategies', 'dow_timeframe_id')
    op.drop_index('ix_swing_points_lookup', table_name='swing_points')
    op.drop_table('swing_points')
