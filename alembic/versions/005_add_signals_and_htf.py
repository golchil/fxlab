"""Add signals table and HTF columns to strategies/trades

Revision ID: 005
Revises: 004
Create Date: 2024-01-28

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '005'
down_revision = '004'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # signals table
    op.create_table(
        'signals',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('dataset_id', sa.Integer(), nullable=False),
        sa.Column('instrument_id', sa.Integer(), nullable=False),
        sa.Column('timeframe_id', sa.Integer(), nullable=False),
        sa.Column('ts', sa.DateTime(timezone=True), nullable=False),
        sa.Column('signal_type', sa.String(64), nullable=False),
        sa.Column('params_json', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['dataset_id'], ['datasets.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['instrument_id'], ['instruments.id']),
        sa.ForeignKeyConstraint(['timeframe_id'], ['timeframes.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_signals_dataset_tf_ts', 'signals', ['dataset_id', 'instrument_id', 'timeframe_id', 'ts'])
    op.create_index('ix_signals_type', 'signals', ['signal_type'])

    # HTF columns on strategies
    op.add_column('strategies', sa.Column('htf_timeframe_id', sa.Integer(), nullable=True))
    op.add_column('strategies', sa.Column('htf_signal_type', sa.String(64), nullable=True))
    op.add_column('strategies', sa.Column('htf_lookback_hours', sa.Integer(), nullable=True, server_default='24'))
    op.add_column('strategies', sa.Column('htf_confirmed_only', sa.Boolean(), nullable=True, server_default='true'))
    op.add_column('strategies', sa.Column('require_htf_signal', sa.Boolean(), nullable=True, server_default='false'))
    op.create_foreign_key('fk_strategies_htf_timeframe', 'strategies', 'timeframes', ['htf_timeframe_id'], ['id'])

    # signal_ts on trades (meta_json already exists, but dedicated column is cleaner)
    op.add_column('trades', sa.Column('signal_ts', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column('trades', 'signal_ts')
    op.drop_constraint('fk_strategies_htf_timeframe', 'strategies', type_='foreignkey')
    op.drop_column('strategies', 'require_htf_signal')
    op.drop_column('strategies', 'htf_confirmed_only')
    op.drop_column('strategies', 'htf_lookback_hours')
    op.drop_column('strategies', 'htf_signal_type')
    op.drop_column('strategies', 'htf_timeframe_id')
    op.drop_index('ix_signals_type', table_name='signals')
    op.drop_index('ix_signals_dataset_tf_ts', table_name='signals')
    op.drop_table('signals')
