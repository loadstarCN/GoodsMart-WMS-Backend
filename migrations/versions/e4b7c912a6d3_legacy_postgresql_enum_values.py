"""Allow existing DN and payment states on older PostgreSQL installations."""
from alembic import op

revision = 'e4b7c912a6d3'
down_revision = 'c3f7a1b8d590'
branch_labels = None
depends_on = None


def upgrade():
    if op.get_bind().dialect.name != 'postgresql':
        return

    # PostgreSQL checks ownership on ALTER TYPE even when the value already exists.
    # A database owner can add the labels before the application runs this migration.
    for enum_name, label in (
        ('dn_type_enum', 'damage_to_supplier'),
        ('payment_status_enum', 'failed'),
    ):
        op.execute(f"""
            DO $migration$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM pg_catalog.pg_enum
                    WHERE enumtypid = '{enum_name}'::regtype AND enumlabel = '{label}'
                ) THEN
                    ALTER TYPE {enum_name} ADD VALUE IF NOT EXISTS '{label}';
                END IF;
            END;
            $migration$;
        """)


def downgrade():
    # The preceding application version accepts both values. Keep them and any rows that use them.
    pass
