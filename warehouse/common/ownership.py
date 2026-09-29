"""归属校验辅助：把"这条数据是不是当前调用方能碰的"收敛到一处。

两类作用域：
- 公司级（supplier / carrier / goods / recipient / department / payment ...）：按 company_id 比对
- 仓库级（asn / dn / 各类任务 / location / 库存记录 ...）：按 warehouse_id 比对，依赖
  @warehouse_required() 先把 g.warehouse_id / g.accessible_warehouses 准备好

调用方可能是 JWT 用户（平台管理员不受限、员工受限）或 API Key（绑定公司时受限）。
"""
from flask import g

from extensions.db import db
from extensions.error import BadRequestException, ForbiddenException, NotFoundException
from system.common.permissions import get_actor_company_id
from .permissions import check_warehouse_access


def require_actor_user_id():
    """写操作需要一个用户身份来记录 created_by / operator_id。

    JWT 用户与绑定了用户的 API Key 都有；未绑定用户的 API Key 走到这里返回 400，
    而不是在 g.current_user.id 上 AttributeError → 500。
    """
    user = g.get('current_user')
    if user is None:
        raise BadRequestException(
            "This operation requires a user identity; bind the API key to a user", 11008
        )
    return user.id


def require_company_scope(company_id, what='record'):
    """公司级校验：调用方有归属公司时，记录必须属于同一公司。返回调用方公司（平台管理员为 None）。"""
    actor_company_id = get_actor_company_id()
    if actor_company_id is not None and company_id != actor_company_id:
        raise ForbiddenException(f"Permission denied: {what} belongs to another company", 12001)
    return actor_company_id


def require_warehouse_scope(warehouse_id, what='record'):
    """仓库级校验：warehouse_id 必须在调用方可访问范围内（须在 @warehouse_required() 之后调用）。"""
    if warehouse_id is None or not check_warehouse_access(warehouse_id):
        raise ForbiddenException(f"Permission denied: {what} belongs to another warehouse", 12001)


def _resolve_attr(obj, path):
    """支持点号路径，如 'dn.warehouse_id'、'location.warehouse_id'"""
    for part in path.split('.'):
        obj = getattr(obj, part, None)
        if obj is None:
            return None
    return obj


def get_company_owned(model, object_id, what=None):
    """按 id 取公司级记录并校验归属；不存在 404、越权 403"""
    obj = db.session.get(model, object_id)
    if not obj:
        raise NotFoundException(f"{model.__name__} with id {object_id} not found", 13001)
    require_company_scope(getattr(obj, 'company_id', None), what or model.__name__)
    return obj


def get_warehouse_owned(model, object_id, warehouse_attr='warehouse_id', what=None):
    """按 id 取仓库级记录并校验归属；warehouse_attr 可为点号路径（如任务 → 'dn.warehouse_id'）"""
    obj = db.session.get(model, object_id)
    if not obj:
        raise NotFoundException(f"{model.__name__} with id {object_id} not found", 13001)
    require_warehouse_scope(_resolve_attr(obj, warehouse_attr), what or model.__name__)
    return obj


def require_same_warehouse(expected_warehouse_id, actual_warehouse_id, what='record'):
    """请求体里的外键（库位 / 单据）必须与主单据同仓库"""
    if expected_warehouse_id != actual_warehouse_id:
        raise ForbiddenException(f"Permission denied: {what} belongs to a different warehouse", 12001)
