"""Goods import / export: import records (with files), imported goods, export jobs

Revision ID: a3f9c2e7d415
Revises: 5b8e2d7c4a91
Create Date: 2026-10-25

商品导入导出（warehouse/goods/importer.py、exporter.py）：
- goods_imports：每次执行的商品导入（文件名、格式、编码、工作表、方式、结果计数、认出 / 忽略的列、失败原因、前 200 行错误）；
  原文件与错误行文件（bytea）保留 30 天（files_expire_at），到期由定时任务清空内容、记 files_purged_at
- goods_import_items：一次导入新增 / 更新的商品（主键 (import_id, goods_id)；导入记录 / 商品删除时级联删除）
- goods_exports：商品导出任务（条件、列、语言、格式、编码、库存范围、状态 pending / running / completed / failed /
  expired、进度、文件内容 bytea、保留到 expires_at（7 天）、重试次数、按哪条记录重新导出）
全是新表，不动已有表；时刻列 timestamptz。downgrade 删这三张表（导入导出记录随之丢失，旧代码不读）。
"""
from alembic import op
import sqlalchemy as sa


revision = 'a3f9c2e7d415'
down_revision = '5b8e2d7c4a91'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'goods_imports',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('company_id', sa.Integer(), nullable=False),
        sa.Column('file_name', sa.String(length=255), nullable=True),
        sa.Column('file_format', sa.String(length=10), nullable=True),
        sa.Column('encoding', sa.String(length=20), nullable=True),
        sa.Column('encoding_detected', sa.Boolean(), nullable=True),
        sa.Column('sheet_name', sa.String(length=100), nullable=True),
        sa.Column('mode', sa.String(length=10), nullable=False),
        sa.Column('status', sa.String(length=10), nullable=False),
        sa.Column('row_count', sa.Integer(), nullable=False),
        sa.Column('created_count', sa.Integer(), nullable=False),
        sa.Column('updated_count', sa.Integer(), nullable=False),
        sa.Column('unchanged_count', sa.Integer(), nullable=False),
        sa.Column('skipped_count', sa.Integer(), nullable=False),
        sa.Column('error_count', sa.Integer(), nullable=False),
        sa.Column('columns', sa.JSON(), nullable=True),
        sa.Column('ignored_columns', sa.JSON(), nullable=True),
        sa.Column('failure', sa.JSON(), nullable=True),
        sa.Column('errors', sa.JSON(), nullable=True),
        sa.Column('original_file', sa.LargeBinary(), nullable=True),
        sa.Column('original_size', sa.Integer(), nullable=True),
        sa.Column('error_file', sa.LargeBinary(), nullable=True),
        sa.Column('error_file_name', sa.String(length=255), nullable=True),
        sa.Column('files_expire_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('files_purged_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_by', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['company_id'], ['companies.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
        sa.CheckConstraint("status IN ('completed','partial','failed')", name='chk_goods_imports_status'),
    )
    op.create_index('idx_goods_imports_company_created', 'goods_imports', ['company_id', 'created_at'])
    op.create_index('idx_goods_imports_files_expire', 'goods_imports', ['files_expire_at'])

    op.create_table(
        'goods_import_items',
        sa.Column('import_id', sa.Integer(), nullable=False),
        sa.Column('goods_id', sa.Integer(), nullable=False),
        sa.Column('action', sa.String(length=10), nullable=False),
        sa.ForeignKeyConstraint(['import_id'], ['goods_imports.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['goods_id'], ['goods.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('import_id', 'goods_id'),
        sa.CheckConstraint("action IN ('create','update')", name='chk_goods_import_items_action'),
    )
    op.create_index('idx_goods_import_items_goods', 'goods_import_items', ['goods_id'])

    op.create_table(
        'goods_exports',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('company_id', sa.Integer(), nullable=True),
        sa.Column('warehouse_id', sa.Integer(), nullable=True),
        sa.Column('scope_warehouse_ids', sa.JSON(), nullable=True),
        sa.Column('uses_stock', sa.Boolean(), nullable=False),
        sa.Column('filters', sa.JSON(), nullable=True),
        sa.Column('columns', sa.JSON(), nullable=False),
        sa.Column('preset', sa.String(length=20), nullable=False),
        sa.Column('lang', sa.String(length=10), nullable=False),
        sa.Column('file_format', sa.String(length=10), nullable=False),
        sa.Column('encoding', sa.String(length=20), nullable=True),
        sa.Column('status', sa.String(length=10), nullable=False),
        sa.Column('background', sa.Boolean(), nullable=False),
        sa.Column('total', sa.Integer(), nullable=False),
        sa.Column('processed', sa.Integer(), nullable=False),
        sa.Column('row_count', sa.Integer(), nullable=True),
        sa.Column('unencodable_count', sa.Integer(), nullable=True),
        sa.Column('file_name', sa.String(length=150), nullable=True),
        sa.Column('file_size', sa.Integer(), nullable=True),
        sa.Column('content', sa.LargeBinary(), nullable=True),
        sa.Column('error', sa.String(length=500), nullable=True),
        sa.Column('attempts', sa.Integer(), nullable=False),
        sa.Column('source_export_id', sa.Integer(), nullable=True),
        sa.Column('created_by', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('heartbeat_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['company_id'], ['companies.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['warehouse_id'], ['warehouses.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['source_export_id'], ['goods_exports.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
        sa.CheckConstraint("status IN ('pending','running','completed','failed','expired')",
                           name='chk_goods_exports_status'),
    )
    op.create_index('idx_goods_exports_company_created', 'goods_exports', ['company_id', 'created_at'])
    op.create_index('idx_goods_exports_status', 'goods_exports', ['status', 'created_at'])


def downgrade():
    op.drop_index('idx_goods_exports_status', table_name='goods_exports')
    op.drop_index('idx_goods_exports_company_created', table_name='goods_exports')
    op.drop_table('goods_exports')
    op.drop_index('idx_goods_import_items_goods', table_name='goods_import_items')
    op.drop_table('goods_import_items')
    op.drop_index('idx_goods_imports_files_expire', table_name='goods_imports')
    op.drop_index('idx_goods_imports_company_created', table_name='goods_imports')
    op.drop_table('goods_imports')
