"""Freeze physical count scopes and preserve linked recount resolutions."""
from alembic import op
import sqlalchemy as sa

revision = 'b2e6f0a7c489'
down_revision = 'a1d5e9f6b378'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('cycle_count_tasks') as b:
        b.add_column(sa.Column('control_state', sa.String(16), nullable=False, server_default='draft'))
        b.add_column(sa.Column('recount_of_id', sa.Integer()))
        b.add_column(sa.Column('resolution_reason', sa.String(255)))
        b.add_column(sa.Column('resolved_by', sa.Integer()))
        b.add_column(sa.Column('resolved_at', sa.DateTime(timezone=True)))
        b.create_foreign_key('fk_count_recount', 'cycle_count_tasks', ['recount_of_id'], ['id'], ondelete='RESTRICT')
        b.create_foreign_key('fk_count_resolver', 'users', ['resolved_by'], ['id'], ondelete='RESTRICT')
        b.create_index('ix_cycle_count_tasks_control_state', ['control_state'])
    # Never retroactively freeze or trust a count performed without scope protection.
    op.execute("UPDATE cycle_count_tasks SET control_state = 'legacy' WHERE status != 'pending' OR EXISTS (SELECT 1 FROM cycle_count_details d WHERE d.task_id = cycle_count_tasks.id AND (d.operator_id IS NOT NULL OR d.status = 'completed'))")


def downgrade():
    with op.batch_alter_table('cycle_count_tasks') as b:
        b.drop_index('ix_cycle_count_tasks_control_state')
        b.drop_constraint('fk_count_recount', type_='foreignkey')
        b.drop_constraint('fk_count_resolver', type_='foreignkey')
        for field in ('resolved_at', 'resolved_by', 'resolution_reason', 'recount_of_id', 'control_state'):
            b.drop_column(field)
