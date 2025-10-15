from flask_restx import Resource, abort
from extensions import limiter  # 导入 Limiter 扩展
from system.common import paginate, permission_required
from .schemas import api_ns, whitelist_model, blacklist_model, whitelist_input_model, blacklist_input_model, pagination_parser, whitelist_pagination_model, blacklist_pagination_model
from .services import LimiterService

from extensions import mqtt_client

@api_ns.route('/test')
class LimiterResource(Resource):
    @limiter.limit("1 per 3 seconds")
    # @limiter.limit("1/second")
    def get(self):
        """Test Limiter"""
        return {"message": "Limiter API"}, 200
    
@api_ns.route('/blacklist')
class BlacklistResource(Resource):

    @permission_required(["all_access", "limiter_read"])
    @api_ns.expect(pagination_parser)
    @api_ns.marshal_with(blacklist_pagination_model)
    def get(self):
        """获取黑名单列表（分页）"""
        args = pagination_parser.parse_args()
        page = args.get('page')
        per_page = args.get('per_page')
        filters = {'ip_address': args.get('ip_address')}
        
        # 使用 LimiterService 获取黑名单列表
        query = LimiterService.list_blacklist(filters)

        return paginate(query, page, per_page)

    @permission_required(["all_access", "limiter_edit"])
    @api_ns.expect(blacklist_input_model)
    @api_ns.marshal_with(blacklist_model)
    def post(self):
        """添加 IP 到黑名单"""
        data = api_ns.payload
        
        # 使用 LimiterService 创建黑名单条目
        blacklist_entry = LimiterService.create_blacklist(data)

        return blacklist_entry, 201

@api_ns.route('/blacklist/<int:id>')
class BlacklistItemResource(Resource):

    @permission_required(["all_access", "limiter_delete"])
    def delete(self, id):
        """删除黑名单条目"""
        LimiterService.delete_blacklist_item(id)

        return {"message": "Blacklist entry deleted"}, 200

@api_ns.route('/whitelist')
class WhitelistResource(Resource):

    @permission_required(["all_access", "limiter_read"])
    @api_ns.expect(pagination_parser)
    @api_ns.marshal_with(whitelist_pagination_model)
    def get(self):
        """获取白名单列表（分页）"""
        args = pagination_parser.parse_args()
        page = args.get('page')
        per_page = args.get('per_page')
        filters = {'ip_address': args.get('ip_address')}

        # 使用 LimiterService 获取白名单列表
        query = LimiterService.list_whitelist(filters)

        return paginate(query, page, per_page)

    @permission_required(["all_access", "limiter_edit"])
    @api_ns.expect(whitelist_input_model)
    @api_ns.marshal_with(whitelist_model)
    def post(self):
        """添加 IP 到白名单"""
        data = api_ns.payload
        
        # 使用 LimiterService 创建白名单条目
        whitelist_entry = LimiterService.create_whitelist(data)

        return whitelist_entry, 201

@api_ns.route('/whitelist/<int:id>')
class WhitelistItemResource(Resource):

    @permission_required(["all_access", "limiter_delete"])
    def delete(self, id):
        """删除白名单条目"""
        LimiterService.delete_whitelist_item(id)

        return {"message": "Whitelist entry deleted"}, 200
