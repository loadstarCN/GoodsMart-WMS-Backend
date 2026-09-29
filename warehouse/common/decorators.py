from flask import g, request
from functools import wraps
from flask_restx import abort

from extensions import db
from extensions.error import BadRequestException, ForbiddenException, UnauthorizedException

# 注意：Warehouse / Staff 模型在函数内延迟导入。各业务模块的 views 会 `from warehouse.common import ...`，
# 若这里在模块顶层导入业务模型，会经由 warehouse/<模块>/__init__.py → views 反向引用 warehouse.common 形成循环导入。

def extract_warehouse_id():
    """
    从请求参数或 Headers 中提取 warehouse_id，并进行基本验证。
    - 如果 warehouse_id 存在且有效，则存入 g.warehouse_id 和 g.warehouse
    - 如果 warehouse_id 无效，则返回 400 错误
    - 如果 warehouse_id 为空，则不做处理（除非后续强制要求）
    """
    warehouse_id = request.args.get("warehouse_id") or request.headers.get("X-WAREHOUSE-ID")
    # print("Extracted warehouse_id:", warehouse_id)

    if not warehouse_id:
        return  # 如果 warehouse_id 为空，则不做任何处理
    try:
        warehouse_id = int(warehouse_id)
    except (TypeError, ValueError):
        raise BadRequestException("Invalid warehouse ID", 14009)
    # 验证 warehouse 是否存在且处于激活状态
    from warehouse.warehouse.models import Warehouse
    warehouse = db.session.get(Warehouse, warehouse_id)
    if not warehouse:
        raise BadRequestException("Invalid warehouse ID", 14009)
    if not warehouse.is_active:
        raise BadRequestException("Warehouse is not active", 14002)

    # 存入 g 供后续使用
    g.warehouse_id = warehouse_id
    g.warehouse = warehouse
    # print("Stored g.warehouse_id:", g.warehouse_id)

def warehouse_required(force_warehouse=False):
    """
    装饰器：
    1. 从请求中提取 warehouse_id，并进行基本验证
    2. 根据当前用户类型及 force_warehouse 参数，进行 warehouse_id 要求检查
    3. 检查 staff 用户的 warehouse 访问权限
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            # 显式提取 warehouse_id（不依赖外部 before_request）
            extract_warehouse_id()
            _enforce_warehouse_requirement(force_warehouse)
            _enforce_staff_warehouse_access()
            return func(*args, **kwargs)
        return wrapper
    return decorator

def _enforce_warehouse_requirement(force_warehouse=False):
    """
    根据当前用户类型以及 force_warehouse 参数检查 warehouse_id 的要求：
    
    - 对于普通用户 (user.type == "user"):
        - 如果 force_warehouse 为 True，则必须提供有效的 warehouse_id；
        - 否则不检查 warehouse_id。
    
    - 对于 staff 用户:
        - 加载该 staff 用户的可访问仓库（存入 g.accessible_warehouses）；
        - 如果该 staff 用户不具备 "company_admin" 角色，则必须提供 warehouse_id，
          且 warehouse 必须在 accessible_warehouses 中；
        - 如果具备 "company_admin" 角色，若提供了 warehouse_id，也要验证其在 accessible_warehouses 内。
    """
    # 全局强制验证
    if force_warehouse and not getattr(g, "warehouse_id", None):
        raise BadRequestException("User must provide a valid warehouse_id when force_warehouse is True", 14003)

    if not g.get("current_user"):
        _enforce_api_key_warehouse_access()
        return

    user = g.current_user
    user_roles = [getattr(role, "name", role) for role in getattr(user, "roles", [])]

    # print("User type:", user.type)
    # print("User roles:", user_roles)
    # print("Force warehouse:", force_warehouse)

    # 对于普通 user，不做检查
    if user.type == "user":
        return

    # 对于 staff 用户，加载其可访问仓库
    if user.type == "staff":
        from warehouse.staff.models import Staff
        staff = db.session.get(Staff, user.id)
        if not staff:
            raise ForbiddenException("Current user is not a staff member", 12005)

        # 如果该 staff 用户不具备 company_admin 角色，则必须提供 warehouse_id，并且该仓库必须在可访问仓库内
        if "company_admin" not in user_roles:
            g.accessible_warehouses = staff.warehouses
            # 过滤掉无效仓库
            g.accessible_warehouses = [w for w in g.accessible_warehouses if w.is_active]

            if not getattr(g, "warehouse_id",None):
                raise BadRequestException("Staff users (non-company_admin) must provide a valid warehouse_id", 14003)
            if g.warehouse not in g.accessible_warehouses:
                raise ForbiddenException("Warehouse ID is not associated with the current staff", 12001)
        else:
            # 获取该staff用户所属公司的所有仓库
            g.accessible_warehouses = staff.company.warehouses
            # 过滤掉无效仓库
            g.accessible_warehouses = [w for w in g.accessible_warehouses if w.is_active]
            # print("Accessible warehouses:", g.accessible_warehouses)

            # 对于具备 company_admin 的 staff，若提供了 warehouse_id，也需验证该仓库在可访问仓库内
            if getattr(g, "warehouse_id",None):
                if g.warehouse not in g.accessible_warehouses:
                    raise ForbiddenException("Warehouse ID is not accessible to the current company_admin staff", 12001)
    else:
        raise ForbiddenException("Unsupported user type", 12005)


def _enforce_api_key_warehouse_access():
    """
    未绑定用户的 API Key：
    - 绑定了公司 → 可访问仓库 = 该公司全部启用仓库；提供了 warehouse_id 时必须在其中
    - 未绑定公司（超级密钥）→ 不受限
    - 既无用户也无 API Key → 401
    """
    if not getattr(g, "current_system", None):
        raise UnauthorizedException("No current user found", 11004)

    api_company_id = getattr(g, "api_key_company_id", None)
    if not api_company_id:
        return

    from warehouse.company.models import Company
    company = db.session.get(Company, api_company_id)
    warehouses = company.warehouses if company else []
    g.accessible_warehouses = [w for w in warehouses if w.is_active]
    if getattr(g, "warehouse_id", None) and g.warehouse not in g.accessible_warehouses:
        raise ForbiddenException("Warehouse ID is not accessible to this API key", 12001)


def _enforce_staff_warehouse_access():
    """
    进一步确保：
    - 对于普通用户，不进行 warehouse 访问权限检查；
    - 对于 staff 用户，必须确保已加载 g.accessible_warehouses，并且若提供了 warehouse_id，
      则该仓库必须在 g.accessible_warehouses 内。
    """
    if not g.get("current_user"):
        return  # API Key 路径已在 _enforce_api_key_warehouse_access 处理

    user = g.current_user
    # 对于普通用户，不作检查
    if user.type != "staff":
        return

    if not hasattr(g, "accessible_warehouses") or not g.accessible_warehouses:
        raise ForbiddenException("Staff user has no accessible warehouses", 12001)
    if hasattr(g, "warehouse") and g.warehouse not in g.accessible_warehouses:
        raise ForbiddenException("Staff user cannot access this warehouse", 12001)