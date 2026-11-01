"""Audit delivery-note print dialog requests."""
from alembic import op
import sqlalchemy as sa

revision = 'e9b3c7d4f156'
down_revision = 'd8a2b6c3e045'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('dn_print_requests',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('dn_id', sa.Integer(), sa.ForeignKey('dn.id', ondelete='CASCADE'), nullable=False),
        sa.Column('request_id', sa.String(36), nullable=False),
        sa.Column('operator_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='RESTRICT'), nullable=False),
        sa.Column('language', sa.String(2), nullable=False),
        sa.Column('requested_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('dn_id', 'request_id', name='uq_dn_print_request'))


def downgrade():
    op.drop_table('dn_print_requests')
