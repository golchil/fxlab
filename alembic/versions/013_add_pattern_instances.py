"""Add pattern_instances and pattern_labels tables, extend strategies for MTF patterns

Revision ID: 013
Revises: 012
Create Date: 2024-01-30

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '013'
down_revision = '012'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # pattern_instances: ダブルボトム/トップなどのパターン
    op.create_table(
        'pattern_instances',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('dataset_id', sa.Integer(), nullable=False),
        sa.Column('instrument_id', sa.Integer(), nullable=False),
        sa.Column('timeframe_id', sa.Integer(), nullable=False),
        sa.Column('pattern_type', sa.String(32), nullable=False),  # "double_bottom", "double_top"
        sa.Column('left_ts', sa.DateTime(timezone=True), nullable=False),  # 左側の山/谷時刻
        sa.Column('right_ts', sa.DateTime(timezone=True), nullable=False),  # 右側の山/谷時刻
        sa.Column('neckline_ts', sa.DateTime(timezone=True), nullable=False),  # ネックライン時刻
        sa.Column('left_price', sa.Float(), nullable=False),
        sa.Column('right_price', sa.Float(), nullable=False),
        sa.Column('neckline_price', sa.Float(), nullable=False),
        sa.Column('confirmed_ts', sa.DateTime(timezone=True), nullable=False),  # 右側確定時刻（未来参照排除）
        sa.Column('params_json', sa.Text(), nullable=True),  # tol, atr_mult, min_bars, method等
        sa.Column('rule_score', sa.Float(), nullable=True),  # ルールベーススコア
        sa.Column('ml_score', sa.Float(), nullable=True),  # ML推論スコア
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(['dataset_id'], ['datasets.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['instrument_id'], ['instruments.id']),
        sa.ForeignKeyConstraint(['timeframe_id'], ['timeframes.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'ix_pattern_instances_lookup',
        'pattern_instances',
        ['dataset_id', 'instrument_id', 'timeframe_id', 'pattern_type', 'confirmed_ts'],
    )

    # pattern_labels: ユーザーによるパターン評価
    op.create_table(
        'pattern_labels',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('pattern_id', sa.Integer(), nullable=False),
        sa.Column('label', sa.String(16), nullable=False),  # "good", "bad"
        sa.Column('note', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(['pattern_id'], ['pattern_instances.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )

    # strategies: MTFパターン戦略用カラム追加
    # HTF (Higher TimeFrame) パターン設定
    op.add_column('strategies', sa.Column('htf_pattern_timeframe_id', sa.Integer(), nullable=True))
    op.add_column('strategies', sa.Column('htf_pattern_type', sa.String(32), nullable=True))
    op.add_column('strategies', sa.Column('htf_pattern_lookback_hours', sa.Integer(), nullable=True))

    # LTF (Lower TimeFrame) パターン設定
    op.add_column('strategies', sa.Column('ltf_pattern_timeframe_id', sa.Integer(), nullable=True))
    op.add_column('strategies', sa.Column('ltf_pattern_type', sa.String(32), nullable=True))
    op.add_column('strategies', sa.Column('ltf_pattern_lookback_minutes', sa.Integer(), nullable=True))
    op.add_column('strategies', sa.Column('require_ltf_breakout', sa.Boolean(), nullable=True, server_default='false'))

    # trades: 参照パターン情報 (既に存在する場合はスキップ)
    from sqlalchemy import inspect
    bind = op.get_bind()
    inspector = inspect(bind)
    existing_columns = [col['name'] for col in inspector.get_columns('trades')]
    if 'meta_json' not in existing_columns:
        op.add_column('trades', sa.Column('meta_json', sa.Text(), nullable=True))


def downgrade() -> None:
    # meta_json might not exist if it was already present before this migration
    from sqlalchemy import inspect
    bind = op.get_bind()
    inspector = inspect(bind)
    existing_columns = [col['name'] for col in inspector.get_columns('trades')]
    if 'meta_json' in existing_columns:
        op.drop_column('trades', 'meta_json')
    op.drop_column('strategies', 'require_ltf_breakout')
    op.drop_column('strategies', 'ltf_pattern_lookback_minutes')
    op.drop_column('strategies', 'ltf_pattern_type')
    op.drop_column('strategies', 'ltf_pattern_timeframe_id')
    op.drop_column('strategies', 'htf_pattern_lookback_hours')
    op.drop_column('strategies', 'htf_pattern_type')
    op.drop_column('strategies', 'htf_pattern_timeframe_id')
    op.drop_table('pattern_labels')
    op.drop_index('ix_pattern_instances_lookup', table_name='pattern_instances')
    op.drop_table('pattern_instances')
