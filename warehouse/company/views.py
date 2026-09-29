from flask import g
from flask_restx import Resource, abort
from extensions.error import ForbiddenException
from system.common import paginate, permission_required
from system.common.permissions import get_actor_company_id
from extensions import oss
from .schemas import api_ns, company_model, company_input_model, pagination_parser, pagination_model,upload_parser
from .services import CompanyService

# 租户（公司）的生命周期字段只有平台管理员能改：员工即使是 company_admin 也不能给自己续期 / 启停
_PLATFORM_ONLY_FIELDS = ('expired_at', 'is_active', 'created_by')


def _enforce_company_scope(company_id: int, action: str):
    actor_company_id = get_actor_company_id()
    if actor_company_id is not None and actor_company_id != company_id:
        raise ForbiddenException(f"You do not have permission to {action} this company.", 12001)


@api_ns.doc(security="jsonWebToken")
@api_ns.route('/')
class CompanyList(Resource):

    @permission_required(["all_access", "company_all_access", "company_read"])
    @api_ns.expect(pagination_parser)
    @api_ns.marshal_with(pagination_model)
    def get(self):
        """获取公司列表（分页；员工只能看到自己的公司）"""
        args = pagination_parser.parse_args()
        page = args.get('page')
        per_page = args.get('per_page')
        get_all = args.get('all', False)  # 获取是否返回所有数据的标志

        filters = {
            'is_active': args.get('is_active'),
            'name': args.get('name') or args.get('keyword'),
            'expired_at_start': args.get('expired_at_start'),
            'expired_at_end': args.get('expired_at_end'),
            'company_id': get_actor_company_id(),
        }

        # 使用 CompanyService 获取过滤后的查询
        query = CompanyService.list_companies(filters)

        return paginate(query, page, per_page, get_all)

    @permission_required(["all_access", "company_all_access", "company_edit"])
    @api_ns.expect(company_input_model)
    @api_ns.marshal_with(company_model)
    def post(self):
        """创建一个新的公司（仅平台管理员）"""
        if get_actor_company_id() is not None:
            raise ForbiddenException("Only platform administrators can create companies.", 12001)

        data = api_ns.payload
        actor = g.get('current_user')

        # 使用 CompanyService 创建公司
        new_company = CompanyService.create_company(data, actor.id if actor else None)

        return new_company, 201


@api_ns.doc(security="jsonWebToken")
@api_ns.route('/<int:company_id>')
class CompanyDetail(Resource):

    @permission_required(["all_access", "company_all_access", "company_read"])
    @api_ns.marshal_with(company_model)
    def get(self, company_id):
        """获取公司详情"""
        _enforce_company_scope(company_id, 'view')
        company = CompanyService.get_company(company_id)
        return company

    @permission_required(["all_access", "company_all_access", "company_edit"])
    @api_ns.expect(company_input_model)
    @api_ns.marshal_with(company_model)
    def put(self, company_id):
        """更新公司信息（员工不能修改 expired_at / is_active）"""
        data = api_ns.payload

        _enforce_company_scope(company_id, 'update')
        if get_actor_company_id() is not None:
            for field in _PLATFORM_ONLY_FIELDS:
                data.pop(field, None)

        updated_company = CompanyService.update_company(company_id, data)

        return updated_company

    @permission_required(["all_access", "company_all_access", "company_delete"])
    def delete(self, company_id):
        """删除公司（仅平台管理员；仍有关联数据时返回 409）"""
        if get_actor_company_id() is not None:
            raise ForbiddenException("Only platform administrators can delete companies.", 12001)
        CompanyService.delete_company(company_id)

        return {"message": "Company deleted successfully"}, 200

@api_ns.route('/image/upload')
class CompanyLogoUpload(Resource):
    @permission_required(["all_access","company_all_access","company_edit"])
    @api_ns.expect(upload_parser)
    def post(self):
        """Upload an image"""
        args = upload_parser.parse_args()
        uploaded_file = args['file']
        file_url = oss.upload_file(uploaded_file, "company/images/")
        return {"file_url": file_url}, 200

