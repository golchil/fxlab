"""Add spread, slippage, and intrabar_fill_mode to strategies

Revision ID: 010
Revises: 009
Create Date: 2024-01-29

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '010'
down_revision = '009'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # スプレッド (pips): その時点のBid/Askスプレッド幅
    op.add_column('strategies', sa.Column('spread_pips', sa.Float(), nullable=False, server_default='0.0'))
    # スリッページ (pips): 約定の滑り（片道）
    op.add_column('strategies', sa.Column('slippage_pips', sa.Float(), nullable=False, server_default='0.0'))
    # 同足TP/SL到達時の扱い: conservative (SL優先), optimistic (TP優先), ignore (次足へ)
    op.add_column('strategies', sa.Column('intrabar_fill_mode', sa.String(32), nullable=False, server_default='conservative'))


def downgrade() -> None:
    op.drop_column('strategies', 'intrabar_fill_mode')
    op.drop_column('strategies', 'slippage_pips')
    op.drop_column('strategies', 'spread_pips')
