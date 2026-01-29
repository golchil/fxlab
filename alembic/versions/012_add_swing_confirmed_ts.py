"""Add confirmed_ts to swing_points for look-ahead bias elimination

Revision ID: 012
Revises: 011
Create Date: 2024-01-29

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '012'
down_revision = '011'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # confirmed_ts: スイングが確定した時刻（閾値反転を確認したバー）
    # 既存データは ts と同じ値で埋める（再生成推奨）
    op.add_column(
        'swing_points',
        sa.Column('confirmed_ts', sa.DateTime(timezone=True), nullable=True)
    )

    # 既存データを ts で埋める
    op.execute("UPDATE swing_points SET confirmed_ts = ts WHERE confirmed_ts IS NULL")

    # NOT NULL制約を追加
    op.alter_column('swing_points', 'confirmed_ts', nullable=False)

    # confirmed_ts用のインデックス追加（dow_trend判定の高速化）
    op.create_index(
        'ix_swing_points_confirmed_lookup',
        'swing_points',
        ['dataset_id', 'instrument_id', 'timeframe_id', 'kind', 'confirmed_ts'],
    )


def downgrade() -> None:
    op.drop_index('ix_swing_points_confirmed_lookup', table_name='swing_points')
    op.drop_column('swing_points', 'confirmed_ts')
