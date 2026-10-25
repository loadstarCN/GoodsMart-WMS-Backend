"""内置权限与内置角色（代码里按名字判断的那些：不能改名 / 删除，见 PermissionService / RoleService）

- 内置权限：BUILTIN_PERMISSIONS，即 @permission_required 等代码里按字符串校验的权限（seed_permissions.py 按它初始化）。
  不能改权限代码（name）、不能删除（409 11011，details {permission, reason}）；描述可以改
- 内置角色：BUILTIN_ROLE_NAMES —— admin（平台超管，持 all_access）、company_admin（代码里按名字判断：
  warehouse/common/decorators.py、warehouse/staff/services.py、warehouse/goods/views.py 等）、warehouse_admin /
  warehouse_operator（Web 员工角色选项 staffRolesOptions 按名字列出）。不能改名、不能停用、不能删除
  （409 11012，details {role, reason}）；分配 / 取消权限照常
- 权限 / 角色的输出带只读字段 is_builtin，界面据此置灰。自建的权限和角色不受影响
"""

# 从代码中提取的所有权限及描述（新增 @permission_required 用到的权限时在这里登记）
BUILTIN_PERMISSIONS = [
    # 全局权限
    ("all_access", "超级管理员，允许所有操作"),
    ("company_all_access", "公司管理员，允许公司范围内的所有操作"),

    # 系统 - 用户管理
    ("user_read", "查看用户列表和详情"),
    ("user_edit", "创建或编辑用户"),
    ("user_delete", "删除用户"),

    # 系统 - 角色管理
    ("role_read", "查看角色列表和详情"),
    ("role_edit", "创建或编辑角色"),
    ("role_delete", "删除角色"),

    # 系统 - 权限管理
    ("permission_read", "查看权限列表和详情"),
    ("permission_edit", "创建或编辑权限"),
    ("permission_delete", "删除权限"),

    # 系统 - 操作日志
    ("logs_read", "查看操作日志"),
    ("logs_create", "创建日志记录"),
    ("logs_edit", "编辑日志记录"),
    ("logs_delete", "删除日志记录"),

    # 系统 - 速率限制
    ("limiter_read", "查看速率限制规则"),
    ("limiter_edit", "创建或编辑速率限制规则"),
    ("limiter_delete", "删除速率限制规则"),

    # 系统 - API 密钥
    ("api_keys_read", "查看 API 密钥列表和详情"),
    ("api_keys_edit", "创建或编辑 API 密钥"),
    ("api_keys_delete", "删除 API 密钥"),

    # 系统 - 任务
    ("tasks_execute", "执行后台任务"),

    # 系统 - Webhook 推送日志
    ("webhook_read", "查看 Webhook 推送日志"),
    ("webhook_edit", "手动重试 Webhook 推送"),

    # 仓库 - 公司
    ("company_read", "查看公司列表和详情"),
    ("company_edit", "创建或编辑公司"),
    ("company_delete", "删除公司"),

    # 仓库 - 供应商
    ("supplier_read", "查看供应商列表和详情"),
    ("supplier_edit", "创建或编辑供应商"),
    ("supplier_delete", "删除供应商"),

    # 仓库 - 承运商
    ("carrier_read", "查看承运商列表和详情"),
    ("carrier_edit", "创建或编辑承运商"),
    ("carrier_delete", "删除承运商"),

    # 仓库 - 收货人
    ("recipient_read", "查看收货人列表和详情"),
    ("recipient_edit", "创建或编辑收货人"),
    ("recipient_delete", "删除收货人"),

    # 仓库 - 员工
    ("staff_read", "查看员工列表和详情"),
    ("staff_edit", "创建或编辑员工"),
    ("staff_delete", "删除员工"),

    # 仓库 - 部门
    ("department_read", "查看部门列表和详情"),
    ("department_edit", "创建或编辑部门"),
    ("department_delete", "删除部门"),

    # 仓库 - 仓库
    ("warehouse_read", "查看仓库列表和详情"),
    ("warehouse_edit", "创建或编辑仓库"),
    ("warehouse_delete", "删除仓库"),

    # 仓库 - 库位
    ("location_read", "查看库位列表和详情"),
    ("location_edit", "创建或编辑库位"),
    ("location_delete", "删除库位"),

    # 仓库 - 商品
    ("goods_read", "查看商品列表和详情"),
    ("goods_edit", "创建或编辑商品"),
    ("goods_delete", "删除商品"),
    ("goods_add", "导入或批量添加商品"),

    # 仓库 - 入库通知 (ASN)
    ("asn_read", "查看入库通知列表和详情"),
    ("asn_edit", "创建或编辑入库通知"),
    ("asn_delete", "删除入库通知"),

    # 仓库 - 出库通知 (DN)
    ("dn_read", "查看出库通知列表和详情"),
    ("dn_edit", "创建或编辑出库通知"),
    ("dn_delete", "删除出库通知"),

    # 仓库 - 库存
    ("inventory_read", "查看库存列表和详情"),
    ("inventory_edit", "创建或编辑库存记录"),
    ("inventory_delete", "删除库存记录"),

    # 仓库 - 上架
    ("putaway_read", "查看上架任务列表和详情"),
    ("putaway_edit", "创建或编辑上架任务"),

    # 仓库 - 拣货
    ("picking_read", "查看拣货单列表和详情"),
    ("picking_edit", "创建或编辑拣货单"),
    ("picking_delete", "删除拣货单"),

    # 仓库 - 分拣
    ("sorting_read", "查看分拣任务列表和详情"),
    ("sorting_edit", "创建或编辑分拣任务"),
    ("sorting_delete", "删除分拣任务"),

    # 仓库 - 打包
    ("packing_read", "查看打包单列表和详情"),
    ("packing_edit", "创建或编辑打包单"),
    ("packing_delete", "删除打包单"),

    # 仓库 - 配送
    ("delivery_read", "查看配送任务列表和详情"),
    ("delivery_edit", "创建或编辑配送任务"),
    ("delivery_delete", "删除配送任务"),

    # 仓库 - 收款
    ("payment_read", "查看收款记录列表和详情"),
    ("payment_edit", "创建或编辑收款记录"),
    ("payment_delete", "删除收款记录"),

    # 仓库 - 库存调拨
    ("transfer_read", "查看库存调拨列表和详情"),
    ("transfer_edit", "创建或编辑库存调拨"),

    # 仓库 - 库存移除
    ("removal_read", "查看库存移除任务列表和详情"),
    ("removal_edit", "创建或编辑库存移除任务"),

    # 仓库 - 库存调整
    ("adjustment_read", "查看库存调整列表和详情"),
    ("adjustment_edit", "创建或编辑库存调整"),
    ("adjustment_approve", "审批库存调整"),
    ("adjustment_delete", "删除库存调整"),

    # 仓库 - 盘点
    ("cycle_count_read", "查看盘点计划列表和详情"),
    ("cycle_count_edit", "创建或编辑盘点计划"),
    ("cycle_count_delete", "删除盘点计划"),
]

BUILTIN_PERMISSION_NAMES = frozenset(name for name, _description in BUILTIN_PERMISSIONS)

BUILTIN_ROLE_NAMES = frozenset({'admin', 'company_admin', 'warehouse_admin', 'warehouse_operator'})

# 内置角色的默认权限（seed_permissions.py 部署时只补缺、不删除：管理员手工加的权限保留，可重复运行）
_OPERATOR_READ = (
    'goods_read', 'location_read', 'inventory_read', 'asn_read', 'sorting_read', 'putaway_read', 'dn_read',
    'picking_read', 'packing_read', 'delivery_read', 'cycle_count_read', 'removal_read', 'transfer_read',
    'carrier_read', 'recipient_read',
    'adjustment_read',  # PDA 商品详情的调整记录页签
)
# 现场 / PDA 作业：收货（PUT /asn/<id>/receive/ 要 asn_edit，后端没有单独的收货权限）、分拣、上架、拣货、打包、
# 配送（完成发货 / 签收 / 运单号 / 揽收登记都是 delivery_edit）、盘点录入（cycle_count_edit）、下架、移库
_OPERATOR_EXECUTE = (
    'asn_edit', 'sorting_edit', 'putaway_edit', 'picking_edit', 'packing_edit', 'delivery_edit', 'cycle_count_edit',
    'removal_edit', 'transfer_edit',
)
# 仓库经理：仓库范围内的全部单据与作业（含新建 / 编辑 / 取消 / 删除），库存调整审批，库位管理，
# 商品 / 承运商 / 收货方 / 供应商的查看与编辑，员工与仓库的查看
_WAREHOUSE_ADMIN = (
    'asn_read', 'asn_edit', 'asn_delete',
    'sorting_read', 'sorting_edit', 'sorting_delete',
    'putaway_read', 'putaway_edit',
    'dn_read', 'dn_edit', 'dn_delete',
    'picking_read', 'picking_edit', 'picking_delete',
    'packing_read', 'packing_edit', 'packing_delete',
    'delivery_read', 'delivery_edit', 'delivery_delete',
    'cycle_count_read', 'cycle_count_edit', 'cycle_count_delete',
    'adjustment_read', 'adjustment_edit', 'adjustment_delete', 'adjustment_approve',
    'removal_read', 'removal_edit',
    'transfer_read', 'transfer_edit',
    'inventory_read',
    'location_read', 'location_edit', 'location_delete',
    'goods_read', 'goods_add', 'goods_edit',
    'carrier_read', 'carrier_edit',
    'recipient_read', 'recipient_edit',
    'supplier_read', 'supplier_edit',
    'staff_read',
    'warehouse_read',
)
BUILTIN_ROLE_DEFAULT_PERMISSIONS = {
    'admin': ('all_access',),
    'company_admin': ('company_all_access',),
    'warehouse_admin': _WAREHOUSE_ADMIN,
    'warehouse_operator': _OPERATOR_READ + _OPERATOR_EXECUTE,
}
# 没有时种子会新建的内置角色（admin / company_admin 只在已有时补缺，不新建）
BUILTIN_ROLES_CREATED_BY_SEED = {
    'warehouse_admin': '仓库经理：仓库范围内的单据、作业、库存调整审批与库位管理',
    'warehouse_operator': '仓库作业员：现场 / PDA 的收货、分拣、上架、拣货、打包、发货、盘点、下架、移库',
}

PERMISSION_BUILTIN_CODE = 11011     # 409：改内置权限的代码 / 删除内置权限
ROLE_BUILTIN_CODE = 11012           # 409：改内置角色的名字 / 停用 / 删除


def is_builtin_permission(name) -> bool:
    return name in BUILTIN_PERMISSION_NAMES


def is_builtin_role(name) -> bool:
    return name in BUILTIN_ROLE_NAMES
