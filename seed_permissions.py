"""
权限数据初始化脚本
从后端代码的 @permission_required 装饰器中提取的所有权限标识，
确保数据库中存在对应记录。已存在的权限会跳过，不会重复插入。

用法: python seed_permissions.py
"""

from app import app
from extensions import db
from system.user.models import Permission

# 从代码中提取的所有权限及描述
PERMISSIONS = [
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
    ("adjustment_delete", "删除库存调整"),

    # 仓库 - 盘点
    ("cycle_count_read", "查看盘点计划列表和详情"),
    ("cycle_count_edit", "创建或编辑盘点计划"),
    ("cycle_count_delete", "删除盘点计划"),
]


def seed():
    with app.app_context():
        existing = {p.name for p in Permission.query.all()}
        added = 0
        skipped = 0

        for name, description in PERMISSIONS:
            if name in existing:
                skipped += 1
                continue
            perm = Permission(name=name, description=description)
            db.session.add(perm)
            added += 1
            print(f"  + {name}")

        db.session.commit()
        print(f"\n完成: 新增 {added} 个权限, 跳过 {skipped} 个已存在的权限")
        print(f"数据库中共有 {Permission.query.count()} 个权限")


if __name__ == "__main__":
    seed()
