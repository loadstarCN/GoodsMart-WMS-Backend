from flask_restx import Resource, abort
from system.common import paginate, permission_required
from .schemas import api_ns, api_key_model, api_key_create_model, pagination_parser, api_key_pagination_model
from .services import APIKeyService

@api_ns.doc(security="jsonWebToken")
@api_ns.route('/api-keys')
class APIKeyList(Resource):

    @permission_required(["all_access", "api_keys_read"])
    @api_ns.expect(pagination_parser)
    @api_ns.marshal_with(api_key_pagination_model)
    def get(self):
        """获取 API Key 列表（分页）"""
        args = pagination_parser.parse_args()
        page = args.get('page', 1)
        per_page = args.get('per_page', 10)
        filters = {
            'is_active': args.get('is_active'),
            'system_name': args.get('system_name')
        }

        query = APIKeyService.list_api_keys(filters)
        return paginate(query, page, per_page)

    @permission_required(["all_access", "api_keys_edit"])
    @api_ns.expect(api_key_create_model)
    @api_ns.marshal_with(api_key_model)
    def post(self):
        """创建新的 API Key"""
        data = api_ns.payload
        new_api_key = APIKeyService.create_api_key(data)
        return new_api_key, 201

@api_ns.doc(security="jsonWebToken")
@api_ns.route('/api-keys/<int:api_key_id>')
class APIKeyDetail(Resource):

    @permission_required(["all_access", "api_keys_read"])
    @api_ns.marshal_with(api_key_model)
    def get(self, api_key_id):
        """获取单个 API Key 详情"""
        api_key = APIKeyService.get_api_key(api_key_id)
        return api_key

    @permission_required(["all_access", "api_keys_edit"])
    @api_ns.expect(api_key_create_model)
    @api_ns.marshal_with(api_key_model)
    def put(self, api_key_id):
        """更新 API Key 信息"""
        data = api_ns.payload
        updated_api_key = APIKeyService.update_api_key(api_key_id, data)

        return updated_api_key

    @permission_required(["all_access", "api_keys_delete"])
    def delete(self, api_key_id):
        """删除 API Key"""
        APIKeyService.delete_api_key(api_key_id)
        return {"message": "API key deleted successfully"}, 200
