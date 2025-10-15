from flask import g
from flask_restx import abort

from extensions.db import *
from extensions.error import UnauthorizedException

def _validate_staff_user() -> bool:
    """核心验证逻辑：存在有效用户且为员工类型时返回True，否则阻断请求或放行"""
    if not hasattr(g, "current_user") or not g.current_user:
        raise UnauthorizedException("No current user found", 11004)
        
    return g.current_user.type == "staff"

def check_warehouse_access(warehouse_id) -> bool:
    """
    根据 g 对象中的 warehouse 过滤条件，判断传入的 warehouse_id 是否满足要求：
    - 如果 g.warehouse_id 存在，则只有当 warehouse_id 等于 g.warehouse_id 时返回 True；
    - 否则，如果 g.accessible_warehouses 存在，则只有当 warehouse_id 在 accessible_warehouses 列表中时返回 True；
    - 如果两者都不存在，则默认返回 False。
    """
    # 非员工用户无需进一步验证
    if not _validate_staff_user():
        return True
    
    # 先判断是否有指定的仓库 ID
    warehouse_id_from_g = getattr(g, "warehouse_id", None)
    if warehouse_id_from_g is not None:
        return warehouse_id == warehouse_id_from_g

    # 如果没有单个仓库 ID，则判断 accessible_warehouses 列表
    accessible_warehouses = getattr(g, "accessible_warehouses", [])
    if accessible_warehouses:
        accessible_ids = [warehouse.id for warehouse in accessible_warehouses]
        return warehouse_id in accessible_ids

    # 若没有任何仓库，则返回 False
    return False


def check_location_access(location_id:int) -> bool:
    """
    验证员工用户对指定库位的访问权限:
    1. 必须存在已登录用户
    2. 员工用户需验证库位访问权限
    3. 其他用户类型默认放行

    :param location_id: 需要验证的库位ID
    :return: 通过验证返回True，否则返回False或终止请求
    :raises: HTTP 403 当无有效用户时
    """
    if not _validate_staff_user():
        return True

    # 员工用户库位权限验证
    from warehouse.location.models import Location
    location = db.session.get(Location,location_id)
    if not location:
        return False

    return check_warehouse_access(location.warehouse_id)

def check_goods_access(goods_id:int) -> bool:
    """
    验证员工用户对指定商品的访问权限:
    1. 必须存在已登录用户
    2. 员工用户需验证商品库存权限
    3. 其他用户类型默认放行

    :param goods_id: 需要验证的商品ID
    :return: 通过验证返回True，否则返回False或终止请求
    :raises: HTTP 403 当无有效用户时
    """

    # 非员工用户无需进一步验证
    if not _validate_staff_user():
        return True
    
    # 下面注释的代码，可能会导致某些情况下做多次查询，不如直接调用 InventoryService.get_inventory
    # 需要再探讨一下
    
    # # 获取商品对象
    # from warehouse.goods.models import Goods
    # goods = db.session.get(Goods, goods_id)
    # if not goods:
    #     return False  # 商品不存在时拒绝访问

    # # 验证商品所属公司
    # if goods.company_id != user.company_id:
    #     return False

    # # 检查公司级全局权限
    # if user.has_role('company_all'):
    #     return True
    
    # 员工用户库存权限验证
    from warehouse.inventory.services import InventoryService
    warehouse_id = getattr(g, "warehouse_id", None)
    if warehouse_id:
        try:
            InventoryService.get_inventory(goods_id, warehouse_id)
            return True
        except NotFoundException:
            # 指定仓库无库存
            return False

    # 检查可访问仓库列表
    accessible_warehouses = getattr(g, "accessible_warehouses", [])
    for warehouse in accessible_warehouses:
        try:
            InventoryService.get_inventory(goods_id, warehouse.id)
            return True
        except NotFoundException:
            continue
    
    # 所有仓库均无库存
    return False
    