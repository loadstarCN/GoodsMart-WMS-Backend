"""
权限数据初始化脚本（部署时自动运行，.github/workflows/deploy.yml）
- 内置权限（system/user/builtin.py 的 BUILTIN_PERMISSIONS，即代码里 @permission_required 校验的权限）：没有的新建
- 内置角色的默认权限（BUILTIN_ROLE_DEFAULT_PERMISSIONS）：只补缺、不删除，管理员手工加的权限保留；
  warehouse_admin / warehouse_operator 没有时新建，admin / company_admin 只在已有时补缺
已存在的不会重复插入，可重复运行（逻辑在 system/user/seed.py）。

用法: python seed_permissions.py
"""

from app import app
from system.user.models import Permission
from system.user.seed import seed_builtin


def seed():
    with app.app_context():
        result = seed_builtin()
        for name in result['added_permissions']:
            print(f"  + {name}")
        for role_name in result['created_roles']:
            print(f"  + role {role_name}")
        for role_name, names in result['granted'].items():
            print(f"  + {role_name}: {', '.join(names)}")
        print()
        print(f"完成: 新增 {len(result['added_permissions'])} 个权限, 新建 {len(result['created_roles'])} 个内置角色, "
              f"给 {len(result['granted'])} 个内置角色补了默认权限")
        print(f"数据库中共有 {Permission.query.count()} 个权限")


if __name__ == "__main__":
    seed()
