"""Add missing indexes for performance

Revision ID: 016
Revises: 015
Create Date: 2024-01-31

"""
from alembic import op


# revision identifiers, used by Alembic.
revision = '016'
down_revision = '015'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # pattern_labels: index on pattern_id for FK joins and CASCADE deletes
    op.create_index(
        'ix_pattern_labels_pattern_id',
        'pattern_labels',
        ['pattern_id']
    )

    # pattern_labels: composite index for label filtering per pattern
    op.create_index(
        'ix_pattern_labels_pattern_label',
        'pattern_labels',
        ['pattern_id', 'label']
    )

    # pattern_instances: index on ml_score for sorting (DESC queries)
    op.create_index(
        'ix_pattern_instances_ml_score',
        'pattern_instances',
        ['ml_score'],
        postgresql_using='btree'
    )

    # pattern_instances: composite for pattern_type + ml_score filtering/sorting
    op.create_index(
        'ix_pattern_instances_type_ml_score',
        'pattern_instances',
        ['pattern_type', 'ml_score']
    )

    # trade_images: index on htf_timeframe_id FK
    op.create_index(
        'ix_trade_images_htf_timeframe',
        'trade_images',
        ['htf_timeframe_id']
    )


def downgrade() -> None:
    op.drop_index('ix_trade_images_htf_timeframe', table_name='trade_images')
    op.drop_index('ix_pattern_instances_type_ml_score', table_name='pattern_instances')
    op.drop_index('ix_pattern_instances_ml_score', table_name='pattern_instances')
    op.drop_index('ix_pattern_labels_pattern_label', table_name='pattern_labels')
    op.drop_index('ix_pattern_labels_pattern_id', table_name='pattern_labels')
