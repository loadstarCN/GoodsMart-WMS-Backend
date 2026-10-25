"""Built-in warehouse_operator role: asn_receive / cycle_count_execute replace asn_edit / cycle_count_edit

Revision ID: c4e8a1f6b2d9
Revises: b8d3f1a7c2e5
Create Date: 2026-10-25

权限拆分（system/user/builtin.py）：作业员只需要收货与盘点录入，不该能新建 / 修改收货通知、新建盘点。
- 新增内置权限 asn_receive（收货：PUT /asn/<id>/receive/、刷新到货跟踪）、cycle_count_execute（开始盘点、录入实盘数、
  完成明细 / 盘点单）；这些接口照旧也接受 asn_edit / cycle_count_edit
- 只动内置角色 warehouse_operator：持有 asn_edit 的，先授予 asn_receive，再移除 asn_edit；cycle_count_edit 同理换成
  cycle_count_execute。没有旧权限的不动（新权限由部署时的种子 seed_permissions.py 按默认权限补上）
- 其他角色（含 warehouse_admin、自建角色）一概不动；管理员如果是有意给作业员 asn_edit / cycle_count_edit 的（让他们也能
  新建收货通知 / 盘点），升级后要重新手工加上
全部是可移植的 SQL（PostgreSQL / SQLite 都能跑，也能离线生成 --sql），不读数据到 Python。可重复执行。
downgrade：给持有新权限的 warehouse_operator 加回 asn_edit / cycle_count_edit；新权限行保留（可能已分配给别的角色）。
"""
from alembic import op


revision = 'c4e8a1f6b2d9'
down_revision = 'b8d3f1a7c2e5'
branch_labels = None
depends_on = None

ROLE = 'warehouse_operator'
NEW_PERMISSIONS = (
    ('asn_receive', '入库通知收货（收货、刷新到货跟踪；不能新建 / 修改 / 关闭 / 取消入库通知）'),
    ('cycle_count_execute', '执行盘点（开始盘点、录入实盘数、完成明细 / 盘点单；不能新建 / 修改盘点计划）'),
)
# 旧权限 → 替代它的新权限
REPLACEMENTS = (('asn_edit', 'asn_receive'), ('cycle_count_edit', 'cycle_count_execute'))


def _grant_if_holding(held, granted):
    """给持有 held 的 warehouse_operator 授予 granted（已有的不重复）"""
    op.execute(f"""
        INSERT INTO role_permissions (role_id, permission_id)
        SELECT r.id, pg.id
        FROM roles r
        JOIN role_permissions rp ON rp.role_id = r.id
        JOIN permissions ph ON ph.id = rp.permission_id AND ph.name = '{held}'
        JOIN permissions pg ON pg.name = '{granted}'
        WHERE r.name = '{ROLE}'
          AND NOT EXISTS (SELECT 1 FROM role_permissions x WHERE x.role_id = r.id AND x.permission_id = pg.id)
    """)


def upgrade():
    for name, description in NEW_PERMISSIONS:
        op.execute(f"""
            INSERT INTO permissions (name, description)
            SELECT '{name}', '{description}'
            WHERE NOT EXISTS (SELECT 1 FROM permissions WHERE name = '{name}')
        """)
    for old, new in REPLACEMENTS:
        _grant_if_holding(old, new)
        # 只在新权限确实已授予时才移除旧权限
        op.execute(f"""
            DELETE FROM role_permissions
            WHERE role_id IN (SELECT id FROM roles WHERE name = '{ROLE}')
              AND permission_id IN (SELECT id FROM permissions WHERE name = '{old}')
              AND EXISTS (
                  SELECT 1 FROM role_permissions x JOIN permissions p ON p.id = x.permission_id
                  WHERE x.role_id = role_permissions.role_id AND p.name = '{new}'
              )
        """)


def downgrade():
    for old, new in REPLACEMENTS:
        _grant_if_holding(new, old)
