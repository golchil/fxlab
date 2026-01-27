"""Add window_images table

Revision ID: 002_add_window_images
Revises: 001_initial
Create Date: 2024-01-27

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '002'
down_revision = '001'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'window_images',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('dataset_id', sa.Integer(), nullable=False),
        sa.Column('window_id', sa.Integer(), nullable=False),
        sa.Column('image_key', sa.String(512), nullable=False),
        sa.Column('width', sa.Integer(), nullable=False),
        sa.Column('height', sa.Integer(), nullable=False),
        sa.Column('ma_periods', sa.String(128), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['dataset_id'], ['datasets.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['window_id'], ['windows.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('window_id'),
    )
    op.create_index('ix_window_images_dataset_created', 'window_images', ['dataset_id', 'created_at'])
    op.create_index('ix_window_images_dataset_window', 'window_images', ['dataset_id', 'window_id'])


def downgrade() -> None:
    op.drop_index('ix_window_images_dataset_window', table_name='window_images')
    op.drop_index('ix_window_images_dataset_created', table_name='window_images')
    op.drop_table('window_images')
