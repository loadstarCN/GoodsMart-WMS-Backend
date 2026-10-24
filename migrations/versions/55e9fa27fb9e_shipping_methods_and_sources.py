"""Shipping flow phase 1: carrier methods, waybill / pickup sources, delivery event sources

Revision ID: 55e9fa27fb9e
Revises: e4a1b3c5d7f9
Create Date: 2026-10-25

设计见 doc/shipping-flow.md（§2.1 / §4）。
- carriers：每项能力的对接方式 waybill_method（api / file / manual，默认 manual）、pickup_method（api / manual /
  scheduled / dropoff / none，默认 none）、tracking_method（api / link / none，默认 link），定期集荷安排
  pickup_schedule（json）、约揽收的联系方式 pickup_contact、公开查询页 tracking_url_template、自动视为签收的天数
  auto_sign_days（1–90，空 = 关）。取值用 CHECK 约束兜底。
  回填：有启用的 FedEx 在线对接的承运商三项都设 api（与升级前的行为一致：建单、预约揽收、跟踪都走 FedEx）；
  对接停用了的只把 tracking_method 设 api（升级前停用的对接照样查已建运单的跟踪，跟到底）。
  其它插件（第三方 entry point）不回填，升级后在承运商编辑页选。
- dn_carrier_shipments：source（api / file / manual，默认 api —— 已有记录都是插件建的）；carrier（插件代码）改为可空
  （手填运单号的承运商可能没有在线对接）；carrier_data（json，插件 / 文件模板的附加数据，第二期用）。
- carrier_pickups：source（api / manual，默认 api）；carrier 改为可空（手工记的揽收可能没有在线对接）。
- delivery_tasks：completed_source / signed_source（warehouse / carrier / rule，空 = 旧数据，按 warehouse）；
  索引 idx_delivery_task_dn (dn_id, is_active)：DN 列表的发货阶段（shipping_stage）逐行查 DN 的当前发货任务。
- PostgreSQL 上 ADD COLUMN ... NOT NULL DEFAULT 常量不重写表（11+），DROP NOT NULL 只改目录；
  SQLite 走 batch（重建表），部分唯一索引 uq_dn_carrier_shipment_open 先删后建（batch 重建时不一定带得上 WHERE）。
- downgrade：旧代码把 dn_carrier_shipments / carrier_pickups 的每条记录都当成插件建的 —— 先删掉 manual / file 运单
  记录和 manual 揽收记录（运单号仍在发货任务上），再去掉新列、恢复 NOT NULL。
"""
from alembic import op
import sqlalchemy as sa


revision = '55e9fa27fb9e'
down_revision = 'e4a1b3c5d7f9'
branch_labels = None
depends_on = None

SHIPMENTS = 'dn_carrier_shipments'
OPEN_INDEX = 'uq_dn_carrier_shipment_open'
OPEN_WHERE = "status IN ('pending','active','unknown','cancelling')"

CARRIER_CHECKS = (
    ('chk_carrier_waybill_method', "waybill_method IN ('api','file','manual')"),
    ('chk_carrier_pickup_method', "pickup_method IN ('api','manual','scheduled','dropoff','none')"),
    ('chk_carrier_tracking_method', "tracking_method IN ('api','link','none')"),
    ('chk_carrier_auto_sign_days', 'auto_sign_days IS NULL OR (auto_sign_days >= 1 AND auto_sign_days <= 90)'),
)
DELIVERY_DN_INDEX = 'idx_delivery_task_dn'
DELIVERY_CHECKS = (
    ('chk_delivery_completed_source', "completed_source IN ('warehouse','carrier','rule')"),
    ('chk_delivery_signed_source', "signed_source IN ('warehouse','carrier','rule')"),
)


def _drop_open_index():
    op.drop_index(OPEN_INDEX, table_name=SHIPMENTS)


def _create_open_index():
    op.create_index(
        OPEN_INDEX, SHIPMENTS, ['dn_id'], unique=True,
        postgresql_where=sa.text(OPEN_WHERE),
        sqlite_where=sa.text(OPEN_WHERE),
    )


def upgrade():
    with op.batch_alter_table('carriers', schema=None) as batch_op:
        batch_op.add_column(sa.Column('waybill_method', sa.String(length=10), nullable=False,
                                      server_default='manual'))
        batch_op.add_column(sa.Column('pickup_method', sa.String(length=10), nullable=False, server_default='none'))
        batch_op.add_column(sa.Column('pickup_schedule', sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column('pickup_contact', sa.String(length=255), nullable=True))
        batch_op.add_column(sa.Column('tracking_method', sa.String(length=10), nullable=False, server_default='link'))
        batch_op.add_column(sa.Column('tracking_url_template', sa.String(length=500), nullable=True))
        batch_op.add_column(sa.Column('auto_sign_days', sa.Integer(), nullable=True))
        for name, sql in CARRIER_CHECKS:
            batch_op.create_check_constraint(name, sql)

    # 有启用的 FedEx 对接：三项都走 FedEx（升级前的行为）；停用的：只保留跟踪（已建运单照样跟到底）
    op.execute(
        """
        UPDATE carriers SET waybill_method = 'api', pickup_method = 'api', tracking_method = 'api'
        WHERE id IN (SELECT carrier_id FROM carrier_integrations WHERE adapter = 'fedex' AND enabled = true)
        """
    )
    op.execute(
        """
        UPDATE carriers SET tracking_method = 'api'
        WHERE id IN (SELECT carrier_id FROM carrier_integrations WHERE adapter = 'fedex' AND enabled = false)
        """
    )

    _drop_open_index()
    with op.batch_alter_table(SHIPMENTS, schema=None) as batch_op:
        batch_op.add_column(sa.Column('source', sa.String(length=10), nullable=False, server_default='api'))
        batch_op.add_column(sa.Column('carrier_data', sa.JSON(), nullable=True))
        batch_op.alter_column('carrier', existing_type=sa.String(length=30), nullable=True)
        batch_op.create_check_constraint('chk_dn_carrier_shipment_source', "source IN ('api','file','manual')")
    _create_open_index()

    with op.batch_alter_table('carrier_pickups', schema=None) as batch_op:
        batch_op.add_column(sa.Column('source', sa.String(length=10), nullable=False, server_default='api'))
        batch_op.alter_column('carrier', existing_type=sa.String(length=30), nullable=True)
        batch_op.create_check_constraint('chk_carrier_pickup_source', "source IN ('api','manual')")

    with op.batch_alter_table('delivery_tasks', schema=None) as batch_op:
        batch_op.add_column(sa.Column('completed_source', sa.String(length=10), nullable=True))
        batch_op.add_column(sa.Column('signed_source', sa.String(length=10), nullable=True))
        for name, sql in DELIVERY_CHECKS:
            batch_op.create_check_constraint(name, sql)
    op.create_index(DELIVERY_DN_INDEX, 'delivery_tasks', ['dn_id', 'is_active'], unique=False)


def downgrade():
    op.drop_index(DELIVERY_DN_INDEX, table_name='delivery_tasks')
    with op.batch_alter_table('delivery_tasks', schema=None) as batch_op:
        for name, _sql in DELIVERY_CHECKS:
            batch_op.drop_constraint(name, type_='check')
        batch_op.drop_column('signed_source')
        batch_op.drop_column('completed_source')

    # 手工记的揽收：旧代码会当成插件预约的（取消时去调承运商），先删掉；所带运单的 pickup_id 一并清空
    op.execute(
        f"UPDATE {SHIPMENTS} SET pickup_id = NULL "
        "WHERE pickup_id IN (SELECT id FROM carrier_pickups WHERE source <> 'api' OR carrier IS NULL)"
    )
    op.execute("DELETE FROM carrier_pickups WHERE source <> 'api' OR carrier IS NULL")
    with op.batch_alter_table('carrier_pickups', schema=None) as batch_op:
        batch_op.drop_constraint('chk_carrier_pickup_source', type_='check')
        batch_op.alter_column('carrier', existing_type=sa.String(length=30), nullable=False)
        batch_op.drop_column('source')

    # 手填 / 文件导回的运单记录：旧代码会当成插件建的有效运单（锁运单号、拦取消 DN），先删掉（运单号仍在发货任务上）
    op.execute(f"DELETE FROM {SHIPMENTS} WHERE source <> 'api' OR carrier IS NULL")
    _drop_open_index()
    with op.batch_alter_table(SHIPMENTS, schema=None) as batch_op:
        batch_op.drop_constraint('chk_dn_carrier_shipment_source', type_='check')
        batch_op.alter_column('carrier', existing_type=sa.String(length=30), nullable=False)
        batch_op.drop_column('carrier_data')
        batch_op.drop_column('source')
    _create_open_index()

    with op.batch_alter_table('carriers', schema=None) as batch_op:
        for name, _sql in CARRIER_CHECKS:
            batch_op.drop_constraint(name, type_='check')
        batch_op.drop_column('auto_sign_days')
        batch_op.drop_column('tracking_url_template')
        batch_op.drop_column('tracking_method')
        batch_op.drop_column('pickup_contact')
        batch_op.drop_column('pickup_schedule')
        batch_op.drop_column('pickup_method')
        batch_op.drop_column('waybill_method')
