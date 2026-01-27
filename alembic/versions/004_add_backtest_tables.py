"""Add backtest tables (strategies, backtest_runs, trades)

Revision ID: 004
Revises: 003
Create Date: 2024-01-27

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '004'
down_revision = '003'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'strategies',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('dataset_id', sa.Integer(), nullable=False),
        sa.Column('instrument_id', sa.Integer(), nullable=False),
        sa.Column('timeframe_id', sa.Integer(), nullable=False),
        sa.Column('side', sa.String(16), nullable=False),
        sa.Column('session_start', sa.String(8), nullable=False),
        sa.Column('session_end', sa.String(8), nullable=False),
        sa.Column('weekdays', sa.Text(), nullable=False, server_default='0,1,2,3,4'),
        sa.Column('entry_timing', sa.String(16), nullable=False, server_default='close'),
        sa.Column('rule_json', sa.Text(), nullable=False),
        sa.Column('tp_type', sa.String(16), nullable=False, server_default='atr'),
        sa.Column('tp_value', sa.Float(), nullable=False),
        sa.Column('sl_type', sa.String(16), nullable=False, server_default='atr'),
        sa.Column('sl_value', sa.Float(), nullable=False),
        sa.Column('max_hold_bars', sa.Integer(), nullable=False, server_default='100'),
        sa.Column('cooldown_bars', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('fee_pips', sa.Float(), nullable=False, server_default='0'),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['dataset_id'], ['datasets.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['instrument_id'], ['instruments.id']),
        sa.ForeignKeyConstraint(['timeframe_id'], ['timeframes.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_strategies_dataset', 'strategies', ['dataset_id'])

    op.create_table(
        'backtest_runs',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('strategy_id', sa.Integer(), nullable=False),
        sa.Column('job_id', sa.Integer(), nullable=True),
        sa.Column('start_ts', sa.DateTime(timezone=True), nullable=True),
        sa.Column('end_ts', sa.DateTime(timezone=True), nullable=True),
        sa.Column('status', sa.String(32), nullable=False, server_default='pending'),
        sa.Column('params_json', sa.Text(), nullable=True),
        sa.Column('result_json', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['strategy_id'], ['strategies.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['job_id'], ['jobs.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_backtest_runs_strategy', 'backtest_runs', ['strategy_id'])

    op.create_table(
        'trades',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('run_id', sa.Integer(), nullable=False),
        sa.Column('entry_ts', sa.DateTime(timezone=True), nullable=False),
        sa.Column('entry_price', sa.Float(), nullable=False),
        sa.Column('exit_ts', sa.DateTime(timezone=True), nullable=True),
        sa.Column('exit_price', sa.Float(), nullable=True),
        sa.Column('side', sa.String(16), nullable=False),
        sa.Column('pnl_pips', sa.Float(), nullable=True),
        sa.Column('r_multiple', sa.Float(), nullable=True),
        sa.Column('exit_reason', sa.String(32), nullable=True),
        sa.Column('meta_json', sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(['run_id'], ['backtest_runs.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_trades_run', 'trades', ['run_id'])


def downgrade() -> None:
    op.drop_index('ix_trades_run', table_name='trades')
    op.drop_table('trades')
    op.drop_index('ix_backtest_runs_strategy', table_name='backtest_runs')
    op.drop_table('backtest_runs')
    op.drop_index('ix_strategies_dataset', table_name='strategies')
    op.drop_table('strategies')
