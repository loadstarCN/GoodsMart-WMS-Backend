"""Stock foundation (phase 1): unified stock service tables — ledger, lot stock, daily balances, metrics

Revision ID: 5b8e2d7c4a91
Revises: c4e8a1f6b2d9
Create Date: 2026-10-25

库存底座一期（doc/库存数值系统说明.md 第九、十节）：
- goods_lots：生产批次（批号）主数据。一期只建表（空），库存与流水的 lot_id 一律为空，二期启用
- inventory_lot_stock：仓内、不在库位上的库存（待上架良品 / 破损、已拣、已打包、已发未签），仓库 × 商品 × 批次；
  唯一索引 (warehouse_id, goods_id, coalesce(lot_id, 0))；各列 ≥ 0、破损 ≤ 待上架。
  按 inventory 的计数器回填无批次行（线上约 1,700 行以内，一条 INSERT … SELECT）。部署窗口里旧进程只改 inventory，
  部署后 flask stock reconcile --apply --sync-legacy-counters 再从计数器校正一次（.github/workflows/deploy.yml）
- stock_ledger：库存流水（只增不改）；stock_daily_balances：日终结存；inventory_metrics_daily：库存分析夜间汇总
- goods_locations 加 lot_id（可空，外键 goods_lots，RESTRICT，带索引）；唯一约束 uix_goods_location (goods_id, location_id)
  换成函数唯一索引 uix_goods_location_lot (goods_id, location_id, coalesce(lot_id, 0))（lot 为空时与原约束等价；
  不用 NULLS NOT DISTINCT：SQLite 不支持）
期初流水不写在迁移里：迁移之后由 flask stock init-ledger 写（部署流水线里自动执行，流水为空时才写）。
PostgreSQL：新表 / 新列可空无默认，不重写表；goods_locations 线上约 200 行，换索引瞬间完成。SQLite 走 batch（重建表）。
downgrade：删表与列，恢复原唯一约束（一期没有带批次的库存行，恢复不会冲突）。
"""
from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa


revision = '5b8e2d7c4a91'
down_revision = 'c4e8a1f6b2d9'
branch_labels = None
depends_on = None

GL_LOT_FK = 'fk_goods_locations_lot_id'
GL_LOT_INDEX = 'idx_goods_location_lot'
GL_UNIQUE_OLD = 'uix_goods_location'
GL_UNIQUE_NEW = 'uix_goods_location_lot'


def _bigint_pk():
    return sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False)


def upgrade():
    op.create_table(
        'goods_lots',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('goods_id', sa.Integer(), nullable=False),
        sa.Column('lot_no', sa.String(length=40), nullable=False),
        sa.Column('production_date', sa.Date(), nullable=True),
        sa.Column('expiry_date', sa.Date(), nullable=True),
        sa.Column('status', sa.String(length=10), nullable=False, server_default='active'),
        sa.Column('source', sa.String(length=20), nullable=True),
        sa.Column('first_inbound_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('remark', sa.String(length=255), nullable=True),
        sa.Column('created_by', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['goods_id'], ['goods.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('goods_id', 'lot_no', name='uq_goods_lots_goods_lot_no'),
        sa.CheckConstraint('expiry_date IS NULL OR production_date IS NULL OR expiry_date >= production_date',
                           name='chk_goods_lots_dates'),
    )
    op.create_index('idx_goods_lots_goods_expiry', 'goods_lots', ['goods_id', 'expiry_date'])
    op.create_index('idx_goods_lots_expiry', 'goods_lots', ['expiry_date'])

    op.create_table(
        'inventory_lot_stock',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('warehouse_id', sa.Integer(), nullable=False),
        sa.Column('goods_id', sa.Integer(), nullable=False),
        sa.Column('lot_id', sa.Integer(), nullable=True),
        sa.Column('sorted_stock', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('sorted_damage_stock', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('picked_stock', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('packed_stock', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('delivered_stock', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['warehouse_id'], ['warehouses.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['goods_id'], ['goods.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['lot_id'], ['goods_lots.id'], ondelete='RESTRICT'),
        sa.PrimaryKeyConstraint('id'),
        sa.CheckConstraint('sorted_stock >= 0 AND sorted_damage_stock >= 0 AND picked_stock >= 0 '
                           'AND packed_stock >= 0 AND delivered_stock >= 0',
                           name='chk_inventory_lot_stock_non_negative'),
        sa.CheckConstraint('sorted_damage_stock <= sorted_stock', name='chk_inventory_lot_stock_damaged'),
    )
    op.create_index('uix_inventory_lot_stock', 'inventory_lot_stock',
                    ['warehouse_id', 'goods_id', sa.text('coalesce(lot_id, 0)')], unique=True)
    op.create_index('idx_inventory_lot_stock_goods', 'inventory_lot_stock', ['goods_id'])
    op.create_index('idx_inventory_lot_stock_lot', 'inventory_lot_stock', ['lot_id'])

    op.create_table(
        'stock_ledger',
        _bigint_pk(),
        sa.Column('posted_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('occurred_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('company_id', sa.Integer(), nullable=False),
        sa.Column('warehouse_id', sa.Integer(), nullable=False),
        sa.Column('goods_id', sa.Integer(), nullable=False),
        sa.Column('lot_id', sa.Integer(), nullable=True),
        sa.Column('location_id', sa.Integer(), nullable=True),
        sa.Column('stock_type', sa.String(length=16), nullable=False),
        sa.Column('quantity', sa.Integer(), nullable=False),
        sa.Column('balance_after', sa.Integer(), nullable=False),
        sa.Column('goods_total_after', sa.Integer(), nullable=False),
        sa.Column('counter_stock_type', sa.String(length=16), nullable=True),
        sa.Column('movement_type', sa.String(length=24), nullable=False),
        sa.Column('doc_type', sa.String(length=20), nullable=True),
        sa.Column('doc_id', sa.Integer(), nullable=True),
        sa.Column('doc_no', sa.String(length=100), nullable=True),
        sa.Column('doc_line_id', sa.Integer(), nullable=True),
        sa.Column('reason', sa.String(length=30), nullable=True),
        sa.Column('operator_id', sa.Integer(), nullable=True),
        sa.Column('api_key_id', sa.Integer(), nullable=True),
        sa.Column('source', sa.String(length=10), nullable=True),
        sa.Column('trace_id', sa.String(length=64), nullable=False),
        sa.Column('remark', sa.String(length=255), nullable=True),
        sa.ForeignKeyConstraint(['warehouse_id'], ['warehouses.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['goods_id'], ['goods.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['lot_id'], ['goods_lots.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['location_id'], ['locations.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['operator_id'], ['users.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['api_key_id'], ['api_keys.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
        sa.CheckConstraint('quantity <> 0', name='chk_stock_ledger_quantity'),
    )
    op.create_index('idx_stock_ledger_warehouse_posted', 'stock_ledger', ['warehouse_id', 'posted_at'])
    op.create_index('idx_stock_ledger_goods_posted', 'stock_ledger', ['warehouse_id', 'goods_id', 'posted_at'])
    op.create_index('idx_stock_ledger_lot_posted', 'stock_ledger', ['lot_id', 'posted_at'])
    op.create_index('idx_stock_ledger_location_posted', 'stock_ledger', ['location_id', 'posted_at'])
    op.create_index('idx_stock_ledger_doc', 'stock_ledger', ['doc_type', 'doc_id'])
    op.create_index('idx_stock_ledger_trace', 'stock_ledger', ['trace_id'])
    op.create_index('idx_stock_ledger_company_posted', 'stock_ledger', ['company_id', 'posted_at'])

    op.create_table(
        'stock_daily_balances',
        _bigint_pk(),
        sa.Column('balance_date', sa.Date(), nullable=False),
        sa.Column('warehouse_id', sa.Integer(), nullable=False),
        sa.Column('goods_id', sa.Integer(), nullable=False),
        sa.Column('lot_id', sa.Integer(), nullable=True),
        sa.Column('location_id', sa.Integer(), nullable=True),
        sa.Column('stock_type', sa.String(length=16), nullable=False),
        sa.Column('quantity', sa.Integer(), nullable=False),
        sa.Column('source', sa.String(length=16), nullable=False),
        sa.ForeignKeyConstraint(['warehouse_id'], ['warehouses.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['goods_id'], ['goods.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['lot_id'], ['goods_lots.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['location_id'], ['locations.id'], ondelete='RESTRICT'),
        sa.PrimaryKeyConstraint('id'),
        sa.CheckConstraint('quantity > 0', name='chk_stock_daily_balance_quantity'),
    )
    op.create_index('uix_stock_daily_balance', 'stock_daily_balances',
                    ['balance_date', 'warehouse_id', 'goods_id', sa.text('coalesce(lot_id, 0)'),
                     sa.text('coalesce(location_id, 0)'), 'stock_type'], unique=True)
    op.create_index('idx_stock_daily_balance_warehouse_date', 'stock_daily_balances', ['warehouse_id', 'balance_date'])
    op.create_index('idx_stock_daily_balance_goods_date', 'stock_daily_balances', ['goods_id', 'balance_date'])

    op.create_table(
        'inventory_metrics_daily',
        sa.Column('metric_date', sa.Date(), nullable=False),
        sa.Column('warehouse_id', sa.Integer(), nullable=False),
        sa.Column('goods_id', sa.Integer(), nullable=False),
        sa.Column('total_stock', sa.Integer(), nullable=False),
        sa.Column('available_stock_for_sale', sa.Integer(), nullable=True),
        sa.Column('out_qty_7d', sa.Integer(), nullable=False),
        sa.Column('out_qty_30d', sa.Integer(), nullable=False),
        sa.Column('out_qty_90d', sa.Integer(), nullable=False),
        sa.Column('sales_qty_30d', sa.Integer(), nullable=False),
        sa.Column('last_in_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_out_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('avg_stock_30d', sa.Float(), nullable=True),
        sa.Column('turnover_days_30d', sa.Float(), nullable=True),
        sa.Column('days_of_supply', sa.Float(), nullable=True),
        sa.Column('age_0_30', sa.Integer(), nullable=False),
        sa.Column('age_31_90', sa.Integer(), nullable=False),
        sa.Column('age_91_180', sa.Integer(), nullable=False),
        sa.Column('age_over_180', sa.Integer(), nullable=False),
        sa.Column('age_unknown', sa.Integer(), nullable=False),
        sa.Column('weighted_age_days', sa.Float(), nullable=True),
        sa.Column('oldest_in_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('out_qty_90d_rank', sa.Integer(), nullable=True),
        sa.Column('abc_class', sa.String(length=1), nullable=True),
        sa.Column('idle_days', sa.Integer(), nullable=True),
        sa.Column('status', sa.String(length=8), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['warehouse_id'], ['warehouses.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['goods_id'], ['goods.id'], ondelete='RESTRICT'),
        sa.PrimaryKeyConstraint('metric_date', 'warehouse_id', 'goods_id'),
    )
    op.create_index('idx_inventory_metrics_warehouse_date', 'inventory_metrics_daily', ['warehouse_id', 'metric_date'])

    with op.batch_alter_table('goods_locations', schema=None) as batch_op:
        batch_op.add_column(sa.Column('lot_id', sa.Integer(), nullable=True))
        batch_op.create_foreign_key(GL_LOT_FK, 'goods_lots', ['lot_id'], ['id'], ondelete='RESTRICT')
        batch_op.drop_constraint(GL_UNIQUE_OLD, type_='unique')
        batch_op.create_index(GL_LOT_INDEX, ['lot_id'], unique=False)
    op.create_index(GL_UNIQUE_NEW, 'goods_locations',
                    ['goods_id', 'location_id', sa.text('coalesce(lot_id, 0)')], unique=True)

    # 无批次的 inventory_lot_stock ← inventory 计数器（负数按 0、破损夹到 [0, 待上架]；对账会报出与计数器不一致的行）
    bind = op.get_bind()
    now = datetime.now(timezone.utc)
    if bind.dialect.name == 'sqlite':
        now = now.replace(tzinfo=None)
    bind.execute(sa.text("""
        INSERT INTO inventory_lot_stock (warehouse_id, goods_id, lot_id, sorted_stock, sorted_damage_stock,
                                         picked_stock, packed_stock, delivered_stock, updated_at)
        SELECT warehouse_id, goods_id, NULL,
               CASE WHEN sorted_stock > 0 THEN sorted_stock ELSE 0 END,
               CASE WHEN sorted_damage_stock <= 0 OR sorted_stock <= 0 THEN 0
                    WHEN sorted_damage_stock < sorted_stock THEN sorted_damage_stock ELSE sorted_stock END,
               CASE WHEN picked_stock > 0 THEN picked_stock ELSE 0 END,
               CASE WHEN packed_stock > 0 THEN packed_stock ELSE 0 END,
               CASE WHEN delivered_stock > 0 THEN delivered_stock ELSE 0 END,
               :now
        FROM inventory
        WHERE sorted_stock > 0 OR picked_stock > 0 OR packed_stock > 0 OR delivered_stock > 0
    """).bindparams(sa.bindparam('now', value=now, type_=sa.DateTime(timezone=True))))


def downgrade():
    op.drop_index(GL_UNIQUE_NEW, table_name='goods_locations')
    with op.batch_alter_table('goods_locations', schema=None) as batch_op:
        batch_op.drop_index(GL_LOT_INDEX)
        batch_op.drop_constraint(GL_LOT_FK, type_='foreignkey')
        batch_op.drop_column('lot_id')
        batch_op.create_unique_constraint(GL_UNIQUE_OLD, ['goods_id', 'location_id'])

    op.drop_index('idx_inventory_metrics_warehouse_date', table_name='inventory_metrics_daily')
    op.drop_table('inventory_metrics_daily')
    op.drop_index('idx_stock_daily_balance_goods_date', table_name='stock_daily_balances')
    op.drop_index('idx_stock_daily_balance_warehouse_date', table_name='stock_daily_balances')
    op.drop_index('uix_stock_daily_balance', table_name='stock_daily_balances')
    op.drop_table('stock_daily_balances')
    for name in ('idx_stock_ledger_company_posted', 'idx_stock_ledger_trace', 'idx_stock_ledger_doc',
                 'idx_stock_ledger_location_posted', 'idx_stock_ledger_lot_posted', 'idx_stock_ledger_goods_posted',
                 'idx_stock_ledger_warehouse_posted'):
        op.drop_index(name, table_name='stock_ledger')
    op.drop_table('stock_ledger')
    op.drop_index('idx_inventory_lot_stock_lot', table_name='inventory_lot_stock')
    op.drop_index('idx_inventory_lot_stock_goods', table_name='inventory_lot_stock')
    op.drop_index('uix_inventory_lot_stock', table_name='inventory_lot_stock')
    op.drop_table('inventory_lot_stock')
    op.drop_index('idx_goods_lots_expiry', table_name='goods_lots')
    op.drop_index('idx_goods_lots_goods_expiry', table_name='goods_lots')
    op.drop_table('goods_lots')
