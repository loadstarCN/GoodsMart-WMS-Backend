"""Auditable, idempotent picking print requests (not printer completion)."""
from alembic import op
import sqlalchemy as sa

revision = 'a5d9e3f0b712'
down_revision = 'f4c8d2e9b601'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('picking_print_requests',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('task_id', sa.Integer(), sa.ForeignKey('picking_tasks.id', ondelete='CASCADE'), nullable=False),
        sa.Column('request_id', sa.String(36), nullable=False),
        sa.Column('operator_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='RESTRICT'), nullable=False),
        sa.Column('language', sa.String(2), nullable=False),
        sa.Column('requested_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('task_id', 'request_id', name='uq_picking_print_request'))
    op.create_index('ix_picking_print_requests_task_id', 'picking_print_requests', ['task_id'])


def downgrade():
    if op.get_bind().execute(sa.text('SELECT 1 FROM picking_print_requests LIMIT 1')).first():
        raise RuntimeError('Cannot discard picking print audit history')
    op.drop_table('picking_print_requests')
