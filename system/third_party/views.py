from flask_restx import Resource, abort
from flask_jwt_extended import current_user
from system.common import paginate, permission_required
from extensions.error import ForbiddenException
from .schemas import api_ns, api_key_model, api_key_create_model, pagination_parser, api_key_pagination_model
from .services import APIKeyService


def _get_user_company_id():
    """获取当前用户的 company_id（仅 Staff 用户有）"""
    if current_user and hasattr(current_user, 'company_id'):
        return current_user.company_id
    return None


def _is_super_admin():
    """检查当前用户是否为超级管理员（拥有 all_access 权限）"""
    if not current_user:
        return False
    user_permissions = {p.name for role in current_user.roles for p in role.permissions}
    return 'all_access' in user_permissions


def _enforce_company_scope(api_key):
    """确保非超管用户只能操作自己公司的 API Key"""
    if _is_super_admin():
        return
    company_id = _get_user_company_id()
    if company_id and api_key.company_id != company_id:
        raise ForbiddenException("Permission denied: cannot access API keys of other companies", 12001)


@api_ns.doc(security="jsonWebToken")
@api_ns.route('/api-keys')
class APIKeyList(Resource):

    @permission_required(["all_access", "company_all_access", "api_keys_read"])
    @api_ns.expect(pagination_parser)
    @api_ns.marshal_with(api_key_pagination_model)
    def get(self):
        """获取 API Key 列表（分页）"""
        args = pagination_parser.parse_args()
        page = args.get('page', 1)
        per_page = args.get('per_page', 10)
        filters = {
            'is_active': args.get('is_active'),
            'system_name': args.get('system_name'),
            'company_id': args.get('company_id')
        }

        # 非超管用户强制只查自己公司的数据
        if not _is_super_admin():
            company_id = _get_user_company_id()
            if company_id:
                filters['company_id'] = company_id

        query = APIKeyService.list_api_keys(filters)
        return paginate(query, page, per_page)

    @permission_required(["all_access", "company_all_access", "api_keys_edit"])
    @api_ns.expect(api_key_create_model)
    @api_ns.marshal_with(api_key_model)
    def post(self):
        """创建新的 API Key"""
        data = api_ns.payload

        # 非超管用户强制绑定自己公司
        if not _is_super_admin():
            company_id = _get_user_company_id()
            if company_id:
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
        """更新 API Key 信息"""
        api_key = APIKeyService.get_api_key(api_key_id)
        _enforce_company_scope(api_key)

        data = api_ns.payload
        updated_api_key = APIKeyService.update_api_key(api_key_id, data)
        return updated_api_key

    @permission_required(["all_access", "company_all_access", "api_keys_delete"])
    def delete(self, api_key_id):
        """删除 API Key"""
        api_key = APIKeyService.get_api_key(api_key_id)
        _enforce_company_scope(api_key)

        APIKeyService.delete_api_key(api_key_id)
        return {"message": "API key deleted successfully"}, 200
