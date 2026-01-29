"""Add ml_models and ml_scores tables for machine learning pipeline

Revision ID: 007
Revises: 006
Create Date: 2024-01-29

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '007'
down_revision = '006'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ml_models table
    op.create_table(
        'ml_models',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('model_type', sa.String(32), nullable=False),  # "entry" or "profit"
        sa.Column('dataset_id', sa.Integer(), nullable=False),
        sa.Column('timeframe_id', sa.Integer(), nullable=False),
        sa.Column('label_source', sa.String(32), nullable=False),  # "entry_points" or "tp_sl"
        sa.Column('config_json', sa.Text(), nullable=True),
        sa.Column('metrics_json', sa.Text(), nullable=True),
        sa.Column('artifact_key', sa.String(512), nullable=True),  # MinIO path
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(['dataset_id'], ['datasets.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['timeframe_id'], ['timeframes.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_ml_models_dataset', 'ml_models', ['dataset_id'])
    op.create_index('ix_ml_models_type', 'ml_models', ['model_type'])

    # ml_scores table
    op.create_table(
        'ml_scores',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('dataset_id', sa.Integer(), nullable=False),
        sa.Column('timeframe_id', sa.Integer(), nullable=False),
        sa.Column('window_id', sa.Integer(), nullable=False),
        sa.Column('model_id', sa.Integer(), nullable=False),
        sa.Column('score', sa.Float(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(['dataset_id'], ['datasets.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['timeframe_id'], ['timeframes.id']),
        sa.ForeignKeyConstraint(['window_id'], ['windows.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['model_id'], ['ml_models.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'ix_ml_scores_lookup',
        'ml_scores',
        ['dataset_id', 'timeframe_id', 'model_id', 'score']
    )
    op.create_index('ix_ml_scores_window', 'ml_scores', ['window_id'])


def downgrade() -> None:
    op.drop_index('ix_ml_scores_window', table_name='ml_scores')
    op.drop_index('ix_ml_scores_lookup', table_name='ml_scores')
    op.drop_table('ml_scores')
    op.drop_index('ix_ml_models_type', table_name='ml_models')
    op.drop_index('ix_ml_models_dataset', table_name='ml_models')
    op.drop_table('ml_models')
