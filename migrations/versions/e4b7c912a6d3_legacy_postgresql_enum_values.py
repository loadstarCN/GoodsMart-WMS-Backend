"""Allow existing DN and payment states on older PostgreSQL installations."""
from alembic import op

revision = 'e4b7c912a6d3'
down_revision = 'c3f7a1b8d590'
branch_labels = None
depends_on = None


def upgrade():
    if op.get_bind().dialect.name == 'postgresql':
        op.execute("ALTER TYPE dn_type_enum ADD VALUE IF NOT EXISTS 'damage_to_supplier'")
        op.execute("ALTER TYPE payment_status_enum ADD VALUE IF NOT EXISTS 'failed'")


def downgrade():
    # The preceding application version accepts both values. Keep them and any rows that use them.
    pass
