"""Carrier integrations (plugin settings per carrier) and carrier_id on DN carrier shipments / carrier pickups

Revision ID: c7e1a9d3f5b2
Revises: c7a1e5d3b902
Create Date: 2026-10-22

- carrier_integrations：承运商的在线对接设置（插件代码 adapter、启用、设置 JSON、加密后的密钥），一个承运商至多一条。
  取代环境变量 FEDEX_*（凭证、公司白名单）：升级后在承运商编辑页设置，或执行
  `flask carrier import-fedex-env` 把旧的 FEDEX_* 环境变量导入到对应承运商。
- dn_carrier_shipments.carrier_id：建单用的承运商（取消时用它的对接设置）。已有记录按 DN 当前有效发货任务的承运商、
  没有则按 DN 的承运商回填。
- carrier_pickups.carrier_id：预约用的承运商（揽收按承运商分开约，取消时用它的对接设置）。已有记录按所带的第一张运单的
  carrier_id 回填（shipments 快照里的 shipment_id）。
"""
import json

from alembic import op
import sqlalchemy as sa


revision = 'c7e1a9d3f5b2'
down_revision = 'c7a1e5d3b902'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'carrier_integrations',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('carrier_id', sa.Integer(), nullable=False),
        sa.Column('adapter', sa.String(length=30), nullable=False),
        sa.Column('enabled', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('settings', sa.JSON(), nullable=True),
        sa.Column('secrets', sa.Text(), nullable=True),
        sa.Column('updated_by', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['carrier_id'], ['carriers.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['updated_by'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('carrier_id'),
    )

    with op.batch_alter_table('dn_carrier_shipments', schema=None) as batch_op:
        batch_op.add_column(sa.Column('carrier_id', sa.Integer(), nullable=True))
        batch_op.create_foreign_key('fk_dn_carrier_shipments_carrier_id', 'carriers', ['carrier_id'], ['id'],
                                    ondelete='SET NULL')

    op.execute(
        """
        UPDATE dn_carrier_shipments SET carrier_id = COALESCE(
            (SELECT t.carrier_id FROM delivery_tasks t
              WHERE t.dn_id = dn_carrier_shipments.dn_id AND t.is_active = true AND t.carrier_id IS NOT NULL
              ORDER BY t.id DESC LIMIT 1),
            (SELECT d.carrier_id FROM dn d WHERE d.id = dn_carrier_shipments.dn_id)
        )
        WHERE carrier_id IS NULL
        """
    )

    with op.batch_alter_table('carrier_pickups', schema=None) as batch_op:
        batch_op.add_column(sa.Column('carrier_id', sa.Integer(), nullable=True))
        batch_op.create_foreign_key('fk_carrier_pickups_carrier_id', 'carriers', ['carrier_id'], ['id'],
                                    ondelete='SET NULL')

    conn = op.get_bind()
    rows = conn.execute(sa.text("SELECT id, shipments FROM carrier_pickups WHERE carrier_id IS NULL")).fetchall()
    for pickup_id, shipments in rows:
        if isinstance(shipments, str):
            try:
                shipments = json.loads(shipments)
            except ValueError:
                shipments = None
        ids = [s.get('shipment_id') for s in shipments or [] if isinstance(s, dict) and s.get('shipment_id')]
        carrier_id = None
        for shipment_id in ids:
            carrier_id = conn.execute(sa.text("SELECT carrier_id FROM dn_carrier_shipments WHERE id = :id"),
                                      {'id': shipment_id}).scalar()
            if carrier_id is not None:
                break
        if carrier_id is not None:
            conn.execute(sa.text("UPDATE carrier_pickups SET carrier_id = :carrier_id WHERE id = :id"),
                         {'carrier_id': carrier_id, 'id': pickup_id})


def downgrade():
    with op.batch_alter_table('carrier_pickups', schema=None) as batch_op:
        batch_op.drop_constraint('fk_carrier_pickups_carrier_id', type_='foreignkey')
        batch_op.drop_column('carrier_id')
    with op.batch_alter_table('dn_carrier_shipments', schema=None) as batch_op:
        batch_op.drop_constraint('fk_dn_carrier_shipments_carrier_id', type_='foreignkey')
        batch_op.drop_column('carrier_id')
    op.drop_table('carrier_integrations')
