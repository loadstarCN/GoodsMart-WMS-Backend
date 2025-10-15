from flask import g
from flask_restx import Resource, abort
from extensions.error import ForbiddenException
from system.common import paginate, permission_required
from extensions import oss
from .schemas import api_ns, company_model, company_input_model, pagination_parser, pagination_model,upload_parser
from .services import CompanyService

@api_ns.doc(security="jsonWebToken")
@api_ns.route('/')
class CompanyList(Resource):

    @permission_required(["all_access", "company_all_access", "company_read"])
    @api_ns.expect(pagination_parser)
    @api_ns.marshal_with(pagination_model)
    def get(self):
        """获取公司列表（分页）"""
        args = pagination_parser.parse_args()
        page = args.get('page')
        per_page = args.get('per_page')
        get_all = args.get('all', False)  # 获取是否返回所有数据的标志

        filters = {
            'is_active': args.get('is_active'),
            'name': args.get('name'),
            'expired_at_start': args.get('expired_at_start'),
            'expired_at_end': args.get('expired_at_end')
        }

        # 使用 CompanyService 获取过滤后的查询
        query = CompanyService.list_companies(filters)

        return paginate(query, page, per_page, get_all)

    @permission_required(["all_access", "company_all_access", "company_edit"])
    @api_ns.expect(company_input_model)
    @api_ns.marshal_with(company_model)
    def post(self):
        """创建一个新的公司"""
        data = api_ns.payload
        created_by = g.current_user.id
        
        # 使用 CompanyService 创建公司
        new_company = CompanyService.create_company(data, created_by)
        
        return new_company, 201


@api_ns.doc(security="jsonWebToken")
@api_ns.route('/<int:company_id>')
class CompanyDetail(Resource):

    @permission_required(["all_access", "company_all_access", "company_read"])
    @api_ns.marshal_with(company_model)
    def get(self, company_id):
        """获取公司详情"""        
        user = g.current_user
        if user.type == 'staff':
            # 员工只能访问自己的公司信息
            if user.company_id != company_id:
                raise ForbiddenException("You do not have permission to view this company.", 12001)
            
        company = CompanyService.get_company(company_id)
        return company

    @permission_required(["all_access", "company_all_access", "company_edit"])
    @api_ns.expect(company_input_model)
    @api_ns.marshal_with(company_model)
    def put(self, company_id):
        """更新公司信息"""
        data = api_ns.payload

        user = g.current_user
        if user.type == 'staff':
            # 员工只能更新自己的公司信息
            if user.company_id != company_id:
                raise ForbiddenException("You do not have permission to update this company.", 12001)

        updated_company = CompanyService.update_company(company_id, data)

        return updated_company

    @permission_required(["all_access", "company_all_access", "company_delete"])
    def delete(self, company_id):
        """删除公司"""
        user = g.current_user
        if user.type == 'staff':
            # 员工只能更新自己的公司信息
            if user.company_id != company_id:
                raise ForbiddenException("You do not have permission to delete this company.", 12001)
        CompanyService.delete_company(company_id)

        return {"message": "Company deleted successfully"}, 200

@api_ns.route('/image/upload')
class CompanyLogoUpload(Resource):
    @permission_required(["all_access","company_all_access","goods_add","goods_edit"])
    @api_ns.expect(upload_parser)
    def post(self):
        """Upload an image"""
        args = upload_parser.parse_args()
        uploaded_file = args['file']
        file_url = oss.upload_file(uploaded_file, "company/images/")
        return {"file_url": file_url}, 200
            