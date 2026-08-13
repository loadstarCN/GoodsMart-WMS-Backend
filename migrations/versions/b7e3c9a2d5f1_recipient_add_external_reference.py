"""Recipient add external reference

Revision ID: b7e3c9a2d5f1
Revises: fa204ac6548c
Create Date: 2026-08-13
"""
from alembic import op
import sqlalchemy as sa


revision = 'b7e3c9a2d5f1'
down_revision = 'fa204ac6548c'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('recipients', schema=None) as batch_op:
        batch_op.add_column(sa.Column(
            'external_reference', sa.String(length=100), nullable=True
        ))
        batch_op.drop_constraint('uq_company_recipient', type_='unique')
        batch_op.drop_constraint('recipients_email_key', type_='unique')
        batch_op.create_unique_constraint(
            'uq_company_recipient_external_reference',
            ['company_id', 'external_reference'],
        )


def downgrade():
    with op.batch_alter_table('recipients', schema=None) as batch_op:
        batch_op.drop_constraint(
            'uq_company_recipient_external_reference', type_='unique'
        )
        batch_op.create_unique_constraint(
            'uq_company_recipient', ['company_id', 'name']
        )
        batch_op.create_unique_constraint('recipients_email_key', ['email'])
        batch_op.drop_column('external_reference')
