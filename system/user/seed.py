"""内置权限 / 内置角色的初始化（seed_permissions.py 部署时运行；须在 app context 里调用）

- 权限：BUILTIN_PERMISSIONS 里没有的新建，已有的不动
- 内置角色的默认权限（BUILTIN_ROLE_DEFAULT_PERMISSIONS）：只补缺、不删除 —— 管理员手工加的权限保留；
  不改角色的启用状态。warehouse_admin / warehouse_operator 没有时新建；admin / company_admin 只在已有时补缺
可重复运行：第二次运行什么都不改。
"""
from extensions import db

from .builtin import BUILTIN_PERMISSIONS, BUILTIN_ROLE_DEFAULT_PERMISSIONS, BUILTIN_ROLES_CREATED_BY_SEED
from .models import Permission, Role


def seed_builtin(commit: bool = True) -> dict:
    """→ {added_permissions: [...], created_roles: [...], granted: {角色: [补上的权限]}}"""
    permissions = {permission.name: permission for permission in Permission.query.all()}
    added_permissions = []
    for name, description in BUILTIN_PERMISSIONS:
        if name not in permissions:
            permission = Permission(name=name, description=description)
            db.session.add(permission)
            permissions[name] = permission
            added_permissions.append(name)
    db.session.flush()

    roles = {role.name: role for role in Role.query.filter(Role.name.in_(list(BUILTIN_ROLE_DEFAULT_PERMISSIONS))).all()}
    created_roles = []
    granted = {}
    for role_name, defaults in BUILTIN_ROLE_DEFAULT_PERMISSIONS.items():
        role = roles.get(role_name)
        if role is None:
            if role_name not in BUILTIN_ROLES_CREATED_BY_SEED:
                continue
            role = Role(name=role_name, description=BUILTIN_ROLES_CREATED_BY_SEED[role_name], is_active=True)
            db.session.add(role)
            created_roles.append(role_name)
        held = {permission.name for permission in role.permissions}
        missing = [name for name in defaults if name not in held]
        for name in missing:
            role.permissions.append(permissions[name])
        if missing:
            granted[role_name] = missing
    if commit:
        db.session.commit()
    else:
        db.session.flush()
    return {'added_permissions': added_permissions, 'created_roles': created_roles, 'granted': granted}
