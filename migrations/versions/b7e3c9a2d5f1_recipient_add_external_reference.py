"""Recipient external reference mapping

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
    op.create_table(
        'recipient_external_mappings',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('company_id', sa.Integer(), nullable=False),
        sa.Column('external_reference', sa.String(length=100), nullable=False),
        sa.Column('recipient_id', sa.Integer(), nullable=False),
        sa.Column('display_name', sa.String(length=255), nullable=False),
        sa.Column('email', sa.String(length=255), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ['recipient_id'], ['recipients.id'], ondelete='CASCADE'
        ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'company_id', 'external_reference',
            name='uq_recipient_external_mapping_company_reference',
        ),
        sa.UniqueConstraint(
            'recipient_id', name='uq_recipient_external_mapping_recipient'
        ),
    )
    op.create_index(
        'ix_recipient_external_mappings_company_id',
        'recipient_external_mappings', ['company_id'], unique=False,
    )


def downgrade():
    op.drop_index(
        'ix_recipient_external_mappings_company_id',
        table_name='recipient_external_mappings',
    )
    op.drop_table('recipient_external_mappings')
