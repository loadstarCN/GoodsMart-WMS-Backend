"""Versioned lot date correction audit, without physical stock movements."""
from alembic import op
import sqlalchemy as sa

revision = 'b6e0f4a1c823'
down_revision = 'a5d9e3f0b712'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('goods_lots', sa.Column('date_revision', sa.Integer(), nullable=False, server_default='0'))
    op.create_table('goods_lot_date_changes',
        sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), primary_key=True),
        sa.Column('lot_id', sa.Integer(), sa.ForeignKey('goods_lots.id', ondelete='RESTRICT'), nullable=False),
        sa.Column('revision', sa.Integer(), nullable=False),
        sa.Column('before_production_date', sa.Date()), sa.Column('before_expiry_date', sa.Date()),
        sa.Column('production_date', sa.Date()), sa.Column('expiry_date', sa.Date()),
        sa.Column('reason', sa.String(255), nullable=False),
        sa.Column('operator_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='SET NULL')),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('lot_id', 'revision', name='uq_lot_date_revision'))
    op.create_index('ix_goods_lot_date_changes_lot_id', 'goods_lot_date_changes', ['lot_id'])


def downgrade():
    op.drop_table('goods_lot_date_changes')
    with op.batch_alter_table('goods_lots') as batch:
        batch.drop_column('date_revision')
