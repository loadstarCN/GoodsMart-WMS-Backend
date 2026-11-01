from flask import g
from flask_restx import Resource
from system.common import paginate, permission_required
from system.common.permissions import assert_grantable_permissions, get_actor_context
from extensions.error import ForbiddenException
from .schemas import api_ns, api_key_model, api_key_created_model, api_key_create_model, pagination_parser, api_key_pagination_model
from .services import APIKeyService


def _get_user_company_id():
    """当前调用方所属公司（JWT 员工或 API Key 绑定的公司）；平台管理员为 None"""
    return get_actor_context()[2]


def _is_super_admin():
    """当前调用方是否为超级管理员（拥有 all_access）"""
    return get_actor_context()[0]


def _require_company_scope():
    """非超管必须归属某个公司，否则拒绝（防止无公司主体绕过隔离）"""
    is_super, _, company_id = get_actor_context()
    if is_super:
        return None
    if company_id is None:
        raise ForbiddenException("Permission denied: caller is not bound to a company", 12001)
    return company_id


def _enforce_company_scope(api_key):
    """确保非超管用户只能操作自己公司的 API Key"""
    company_id = _require_company_scope()
    if company_id is not None and api_key.company_id != company_id:
        raise ForbiddenException("Permission denied: cannot access API keys of other companies", 12001)


def _restricted_key_owner_id():
    """API Key 写操作的范围规则（B-75）：

    - 平台管理员（all_access）或持 company_all_access：可以新建 / 修改 / 删除本公司任意 Key，返回 None；
    - 其他人（只有 api_keys_edit / api_keys_delete 的员工、绑定员工的 Key）：只能操作绑定到自己的 Key，
      且 Key 的权限必须是自己权限的子集（assert_grantable_permissions）。返回调用方用户 id。
      未绑定用户的 Key 覆盖全公司仓库，让他们建 / 改会把自己的仓库范围扩大到全公司。
    - 没有用户身份（未绑定用户、也没有 company_all_access 的 Key）→ 403 16105。
    """
    is_super, perms, _ = get_actor_context()
    if is_super or 'company_all_access' in perms:
        return None
    actor = g.get('current_user')
    if actor is None:
        raise ForbiddenException("Only API keys bound to yourself can be managed without company_all_access",
                                 16105)
    return actor.id


def _require_own_key(owner_id, user_id):
    if owner_id is not None and user_id != owner_id:
        raise ForbiddenException("Only API keys bound to yourself can be managed without company_all_access",
                                 16105, 'user_id')


@api_ns.doc(security="jsonWebToken")
@api_ns.route('/api-keys')
class APIKeyList(Resource):

    @permission_required(["all_access", "company_all_access", "api_keys_read"])
    @api_ns.expect(pagination_parser)
    @api_ns.marshal_with(api_key_pagination_model)
    def get(self):
        """获取 API Key 列表（分页；不含明文 key）"""
        args = pagination_parser.parse_args()
        page = args.get('page')
        per_page = args.get('per_page')
        filters = {
            'is_active': args.get('is_active'),
            'system_name': args.get('system_name'),
            'keyword': args.get('keyword'),
            'company_id': args.get('company_id')
        }

        # 非超管用户强制只查自己公司的数据
        company_id = _require_company_scope()
        if company_id is not None:
            filters['company_id'] = company_id

        query = APIKeyService.list_api_keys(filters)
        return paginate(query, page, per_page)

    @permission_required(["all_access", "company_all_access", "api_keys_edit"])
    @api_ns.expect(api_key_create_model)
    @api_ns.marshal_with(api_key_created_model)
    def post(self):
        """创建新的 API Key（明文 key 只在本次响应返回）"""
        data = api_ns.payload

        # 非超管用户强制绑定自己公司
        company_id = _require_company_scope()
        if company_id is not None:
            data['company_id'] = company_id

        # 没有 company_all_access：不传 user_id 时自动绑定到自己（Web 管理端新建时不传 user_id），
        # 传了别人 → 403 16105
        owner_id = _restricted_key_owner_id()
        if owner_id is not None:
            if data.get('user_id') is None:
                data['user_id'] = owner_id
            _require_own_key(owner_id, data.get('user_id'))

        new_api_key = APIKeyService.create_api_key(data)
        return new_api_key, 201

@api_ns.doc(security="jsonWebToken")
@api_ns.route('/api-keys/<int:api_key_id>')
class APIKeyDetail(Resource):

    @permission_required(["all_access", "company_all_access", "api_keys_read"])
    @api_ns.marshal_with(api_key_model)
    def get(self, api_key_id):
        """获取单个 API Key 详情"""
        api_key = APIKeyService.get_api_key(api_key_id)
        _enforce_company_scope(api_key)
        return api_key

    @permission_required(["all_access", "company_all_access", "api_keys_edit"])
    @api_ns.expect(api_key_create_model)
    @api_ns.marshal_with(api_key_model)
    def put(self, api_key_id):
        """更新 API Key 信息（company_id 不可修改）"""
        api_key = APIKeyService.get_api_key(api_key_id)
        _enforce_company_scope(api_key)

        data = api_ns.payload
        data.pop('company_id', None)
        # 没有 company_all_access：只能改绑定到自己的 Key，不能改绑 / 解绑；
        # 改完后的权限也必须在自己权限之内（没传 permissions 时校验现有权限）
        owner_id = _restricted_key_owner_id()
        if owner_id is not None:
            _require_own_key(owner_id, api_key.user_id)
            if 'user_id' in data:
                _require_own_key(owner_id, data.get('user_id'))
            if 'permissions' not in data:
                assert_grantable_permissions(api_key.permissions or [])
        updated_api_key = APIKeyService.update_api_key(api_key_id, data)
        return updated_api_key

    @permission_required(["all_access", "company_all_access", "api_keys_delete"])
    def delete(self, api_key_id):
        """删除 API Key"""
        api_key = APIKeyService.get_api_key(api_key_id)
        _enforce_company_scope(api_key)
        _require_own_key(_restricted_key_owner_id(), api_key.user_id)

        APIKeyService.delete_api_key(api_key_id)
        return {"message": "API key deleted successfully"}, 200


@api_ns.route('/api-keys/<int:api_key_id>/calls')
class APIKeyCalls(Resource):
    @permission_required(["all_access", "company_all_access", "api_keys_read"])
    def get(self, api_key_id):
        from datetime import datetime, timezone
        from flask import request
        from flask_restx import abort
        from .models import APICallLog
        key = APIKeyService.get_api_key(api_key_id)
        _enforce_company_scope(key)
        query = APICallLog.query.filter_by(api_key_id=api_key_id)
        try:
            page = int(request.args.get('page', '1'))
            size = int(request.args.get('per_page', '20'))
            if page < 1 or page > 1000000 or not 1 <= size <= 100:
                raise ValueError()
            dates = {}
            for name in ('from', 'before'):
                raw = request.args.get(name)
                if raw:
                    value = datetime.fromisoformat(raw.replace('Z', '+00:00'))
                    dates[name] = value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
            if 'from' in dates and 'before' in dates and dates['from'] >= dates['before']:
                raise ValueError()
            if 'from' in dates:
                query = query.filter(APICallLog.called_at >= dates['from'])
            if 'before' in dates:
                query = query.filter(APICallLog.called_at < dates['before'])
            status = request.args.get('status')
            if status:
                if status not in ('2xx', '3xx', '4xx', '5xx'):
                    raise ValueError()
                lower = int(status[0]) * 100
                query = query.filter(APICallLog.status_code >= lower, APICallLog.status_code < lower + 100)
        except (ValueError, TypeError, OverflowError):
            abort(400, 'Invalid pagination, date range or status filter')
        total = query.count()
        rows = query.order_by(APICallLog.called_at.desc(), APICallLog.id.desc()).offset((page-1)*size).limit(size).all()
        return {'items': [dict(id=row.id, called_at=row.called_at.isoformat(), method=row.method,
                              route=row.route, status_code=row.status_code, duration_ms=row.duration_ms) for row in rows],
                'total': total, 'page': page, 'per_page': size}


@api_ns.route('/api-keys/call-summary')
class APIKeyCallSummary(Resource):
    @permission_required(["all_access", "company_all_access", "api_keys_read"])
    def get(self):
        from flask import request
        from flask_restx import abort
        from extensions import db
        from .models import APIKey, APICallLog
        parts = request.args.get('api_key_ids', '').split(',')
        if not 1 <= len(parts) <= 100 or any(not p.isdecimal() or len(p)>10 or not 0<int(p)<=2147483647 for p in parts):
            abort(400, 'Invalid API key IDs')
        keys = APIKey.query.filter(APIKey.id.in_([int(p) for p in parts]))
        company_id = _require_company_scope()
        if company_id is not None:
            keys = keys.filter_by(company_id=company_id)
        ids = [key.id for key in keys.all()]
        latest = dict(db.session.query(APICallLog.api_key_id, db.func.max(APICallLog.called_at))
                      .filter(APICallLog.api_key_id.in_(ids)).group_by(APICallLog.api_key_id).all())
        return {'items': [{'api_key_id': key_id, 'last_called_at': latest[key_id].isoformat() if key_id in latest else None} for key_id in ids]}
