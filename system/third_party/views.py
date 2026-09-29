from flask_restx import Resource
from system.common import paginate, permission_required
from system.common.permissions import get_actor_context
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
        updated_api_key = APIKeyService.update_api_key(api_key_id, data)
        return updated_api_key

    @permission_required(["all_access", "company_all_access", "api_keys_delete"])
    def delete(self, api_key_id):
        """删除 API Key"""
        api_key = APIKeyService.get_api_key(api_key_id)
        _enforce_company_scope(api_key)

        APIKeyService.delete_api_key(api_key_id)
        return {"message": "API key deleted successfully"}, 200
