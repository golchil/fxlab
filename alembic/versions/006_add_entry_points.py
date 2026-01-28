"""Add entry_points table for Lab feature

Revision ID: 006
Revises: 005
Create Date: 2024-01-28

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '006'
down_revision = '005'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'entry_points',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('dataset_id', sa.Integer(), nullable=False),
        sa.Column('instrument_id', sa.Integer(), nullable=False),
        sa.Column('timeframe_id', sa.Integer(), nullable=False),
        sa.Column('ts', sa.DateTime(timezone=True), nullable=False),
        sa.Column('side', sa.Text(), nullable=False),
        sa.Column('label', sa.Text(), nullable=False, server_default='unknown'),
        sa.Column('note', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(['dataset_id'], ['datasets.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['instrument_id'], ['instruments.id']),
        sa.ForeignKeyConstraint(['timeframe_id'], ['timeframes.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_entry_points_dataset', 'entry_points', ['dataset_id', 'instrument_id', 'timeframe_id'])
    op.create_unique_constraint('uq_entry_points_ts', 'entry_points', ['dataset_id', 'instrument_id', 'timeframe_id', 'ts', 'side'])


def downgrade() -> None:
    op.drop_constraint('uq_entry_points_ts', 'entry_points', type_='unique')
    op.drop_index('ix_entry_points_dataset', table_name='entry_points')
    op.drop_table('entry_points')
