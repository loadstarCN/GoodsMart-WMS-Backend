"""Authenticated API key call metadata, without historical inference."""
from alembic import op
import sqlalchemy as sa
revision = 'd2a6b0c7f489'
down_revision = 'c1f5a9b6e378'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('api_call_logs',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('api_key_id', sa.Integer, sa.ForeignKey('api_keys.id', ondelete='CASCADE'), nullable=False),
        sa.Column('called_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('method', sa.String(16), nullable=False),
        sa.Column('route', sa.String(512), nullable=False),
        sa.Column('status_code', sa.Integer, nullable=False),
        sa.Column('duration_ms', sa.Integer, nullable=False))
    op.create_index('ix_api_call_key_time', 'api_call_logs', ['api_key_id','called_at','id'])


def downgrade():
    if op.get_bind().execute(sa.text('SELECT 1 FROM api_call_logs LIMIT 1')).first():
        raise RuntimeError('Cannot discard API call history')
    op.drop_table('api_call_logs')
