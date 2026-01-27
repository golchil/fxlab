"""Initial migration

Revision ID: 001
Revises:
Create Date: 2024-01-01 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '001'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create TimescaleDB extension
    op.execute("CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE")

    # Create datasets table
    op.create_table(
        'datasets',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('timezone', sa.String(64), nullable=False, server_default='Asia/Tokyo'),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.func.now(), onupdate=sa.func.now()),
    )

    # Create instruments table
    op.create_table(
        'instruments',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('symbol', sa.String(32), unique=True, nullable=False),
        sa.Column('name', sa.String(255), nullable=True),
    )

    # Create timeframes table
    op.create_table(
        'timeframes',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('name', sa.String(32), unique=True, nullable=False),
        sa.Column('minutes', sa.Integer(), nullable=False),
    )

    # Create bars table (composite primary key including ts for TimescaleDB hypertable)
    op.create_table(
        'bars',
        sa.Column('dataset_id', sa.Integer(), sa.ForeignKey('datasets.id', ondelete='CASCADE'), nullable=False),
        sa.Column('instrument_id', sa.Integer(), sa.ForeignKey('instruments.id'), nullable=False),
        sa.Column('timeframe_id', sa.Integer(), sa.ForeignKey('timeframes.id'), nullable=False),
        sa.Column('ts', sa.DateTime(timezone=True), nullable=False),
        sa.Column('open', sa.Float(), nullable=False),
        sa.Column('high', sa.Float(), nullable=False),
        sa.Column('low', sa.Float(), nullable=False),
        sa.Column('close', sa.Float(), nullable=False),
        sa.Column('volume', sa.Float(), server_default='0'),
        sa.PrimaryKeyConstraint('dataset_id', 'instrument_id', 'timeframe_id', 'ts', name='pk_bars'),
    )
    op.create_index('ix_bars_dataset_ts', 'bars', ['dataset_id', 'ts'])

    # Convert bars to hypertable
    op.execute("SELECT create_hypertable('bars', 'ts', if_not_exists => true)")

    # Create jobs table
    op.create_table(
        'jobs',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('dataset_id', sa.Integer(), sa.ForeignKey('datasets.id', ondelete='CASCADE'), nullable=True),
        sa.Column('job_type', sa.String(64), nullable=False),
        sa.Column('status', sa.String(32), server_default='pending'),
        sa.Column('celery_task_id', sa.String(255), nullable=True),
        sa.Column('params', sa.Text(), nullable=True),
        sa.Column('result', sa.Text(), nullable=True),
        sa.Column('error', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.func.now(), onupdate=sa.func.now()),
    )

    # Create windows table
    op.create_table(
        'windows',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('dataset_id', sa.Integer(), sa.ForeignKey('datasets.id', ondelete='CASCADE'), nullable=False),
        sa.Column('instrument_id', sa.Integer(), sa.ForeignKey('instruments.id'), nullable=False),
        sa.Column('timeframe_id', sa.Integer(), sa.ForeignKey('timeframes.id'), nullable=False),
        sa.Column('start_ts', sa.DateTime(timezone=True), nullable=False),
        sa.Column('end_ts', sa.DateTime(timezone=True), nullable=False),
        sa.Column('lookback_n', sa.Integer(), nullable=False),
        sa.Column('bar_count', sa.Integer(), nullable=False),
    )
    op.create_index('ix_windows_dataset', 'windows', ['dataset_id'])

    # Create labels table
    op.create_table(
        'labels',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('dataset_id', sa.Integer(), sa.ForeignKey('datasets.id', ondelete='CASCADE'), nullable=False),
        sa.Column('instrument_id', sa.Integer(), sa.ForeignKey('instruments.id'), nullable=False),
        sa.Column('timeframe_id', sa.Integer(), sa.ForeignKey('timeframes.id'), nullable=False),
        sa.Column('bar_ts', sa.DateTime(timezone=True), nullable=False),
        sa.Column('label_type', sa.String(32), nullable=False),
        sa.Column('lookahead_m', sa.Integer(), nullable=False),
        sa.Column('tp_r', sa.Float(), nullable=False),
        sa.Column('sl_r', sa.Float(), nullable=False),
        sa.Column('result', sa.String(16), nullable=False),
    )
    op.create_index('ix_labels_dataset', 'labels', ['dataset_id'])


def downgrade() -> None:
    op.drop_table('labels')
    op.drop_table('windows')
    op.drop_table('jobs')
    op.drop_table('bars')
    op.drop_table('timeframes')
    op.drop_table('instruments')
    op.drop_table('datasets')
