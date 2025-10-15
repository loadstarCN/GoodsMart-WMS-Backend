from flask_restx import Resource, abort, marshal_with
from system.common import permission_required, paginate
from .models import ActivityLog
from .schemas import api_ns, log_model, pagination_parser, pagination_model
from .services import LogService


@api_ns.doc(security="jsonWebToken")
@api_ns.route('/')
class UserLogs(Resource):

    @permission_required(["all_access", "logs_read"])
    @api_ns.expect(pagination_parser)
    @api_ns.marshal_with(pagination_model, mask='*,items{id,actor,endpoint,method,status_code,ip_address,created_at}')
    def get(self):
        """获取用户日志（分页）"""
        args = pagination_parser.parse_args()
        page = args.get('page')
        per_page = args.get('per_page')

        filters = {
            'actor': args.get('actor'),
            'endpoint': args.get('endpoint'),
            'method': args.get('method'),
            'status_code': args.get('status_code'),
            'ip_address': args.get('ip_address'),
            'created_at': args.get('created_at'),
            'keyword': args.get('keyword')
        }

        # 使用 LogService 获取过滤后的查询
        query = LogService.list_logs(filters)

        return paginate(query, page, per_page)


    @permission_required(["all_access", "logs_create"])
    @api_ns.expect(log_model)
    @marshal_with(log_model)
    def post(self):
        """创建用户日志"""
        data = api_ns.payload

        # 使用 LogService 创建日志
        new_log = LogService.create_log(data)
        
        return new_log, 201


@api_ns.doc(security="jsonWebToken")
@api_ns.route('/<int:log_id>')
class UserLog(Resource):

    @permission_required(["all_access", "logs_read"])
    @marshal_with(log_model)
    def get(self, log_id):
        """获取单个用户日志"""
        log = LogService.get_log(log_id)
        return log


    @permission_required(["all_access", "logs_edit"])
    @api_ns.expect(log_model)
    @marshal_with(log_model)
    def put(self, log_id):
        """更新单个用户日志"""
        data = api_ns.payload
        updated_log = LogService.update_log(log_id, data)
        return updated_log


    @permission_required(["all_access", "logs_delete"])
    def delete(self, log_id):
        """删除用户日志"""
        LogService.delete_log(log_id)
        return {"message": "Log deleted successfully"}, 200
