"""Keep FedEx's per-shipment document and paper-copy requirements.

Revision ID: e4a1b3c5d7f9
Revises: d8b2f4a6c1e3
"""
from alembic import op
import sqlalchemy as sa


revision = 'e4a1b3c5d7f9'
down_revision = 'd8b2f4a6c1e3'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('dn_carrier_shipments', sa.Column('trade_documents', sa.JSON(), nullable=True))


def downgrade():
    op.drop_column('dn_carrier_shipments', 'trade_documents')
