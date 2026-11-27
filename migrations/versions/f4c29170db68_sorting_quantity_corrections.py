"""Record sorting quantity correction requests and reviews."""
import sqlalchemy as sa
from alembic import op

from extensions.db import UTCDateTime

revision = 'f4c29170db68'
down_revision = 'e4b7c912a6d3'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'sorting_quantity_corrections',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('task_id', sa.Integer(), sa.ForeignKey('sorting_tasks.id', ondelete='RESTRICT'), nullable=False),
        sa.Column('goods_id', sa.Integer(), sa.ForeignKey('goods.id', ondelete='RESTRICT'), nullable=False),
        sa.Column('lot_id', sa.Integer(), sa.ForeignKey('goods_lots.id', ondelete='RESTRICT')),
        sa.Column('before', sa.JSON(), nullable=False),
        sa.Column('after', sa.JSON(), nullable=False),
        sa.Column('reason', sa.String(500), nullable=False),
        sa.Column('status', sa.String(8), nullable=False),
        sa.Column('requested_by', sa.Integer(), sa.ForeignKey('users.id', ondelete='RESTRICT'), nullable=False),
        sa.Column('requested_at', UTCDateTime(), nullable=False),
        sa.Column('reviewed_by', sa.Integer(), sa.ForeignKey('users.id', ondelete='RESTRICT')),
        sa.Column('reviewed_at', UTCDateTime()),
        sa.Column('review_note', sa.String(500)),
        sa.CheckConstraint("status IN ('pending','approved','rejected')", name='chk_sorting_correction_status'),
    )
    op.create_index('ix_sorting_quantity_corrections_task', 'sorting_quantity_corrections', ['task_id'])


def downgrade():
    op.drop_table('sorting_quantity_corrections')
