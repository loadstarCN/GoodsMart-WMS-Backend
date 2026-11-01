"""Capture rejection and shelf-life concession reasons without stocking rejected goods."""
from alembic import op
import sqlalchemy as sa
revision = 'b0e4f8a5d267'
down_revision = 'a9d3e7f4c156'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('sorting_task_details') as batch:
        batch.add_column(sa.Column('rejected_quantity',sa.Integer,nullable=False,server_default='0'))
        batch.add_column(sa.Column('rejection_reason',sa.String(500),nullable=True))
        batch.add_column(sa.Column('concession_reason',sa.String(500),nullable=True))
        batch.create_check_constraint('chk_rejected_qty','rejected_quantity >= 0')
    op.add_column('asn_details',sa.Column('rejected_quantity',sa.Integer,nullable=True))


def downgrade():
    bind = op.get_bind()
    if bind.execute(sa.text("SELECT 1 FROM sorting_task_details WHERE rejected_quantity > 0 OR rejection_reason IS NOT NULL OR concession_reason IS NOT NULL LIMIT 1")).first():
        raise RuntimeError('Cannot discard recorded receiving exceptions')
    with op.batch_alter_table('asn_details') as batch:
        batch.drop_column('rejected_quantity')
    with op.batch_alter_table('sorting_task_details') as batch:
        batch.drop_constraint('chk_rejected_qty',type_='check')
        for name in ('rejected_quantity','rejection_reason','concession_reason'):
            batch.drop_column(name)
