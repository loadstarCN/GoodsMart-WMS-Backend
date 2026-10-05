from flask_restx import Resource
from extensions.error import BadRequestException, ForbiddenException
from system.common import paginate, permission_required
from system.common.permissions import get_actor_company_id
from warehouse.common import get_company_owned, require_actor_user_id
from .models import Department
from .schemas import api_ns, department_model, department_input_model, pagination_parser, pagination_model
from .services import DepartmentService

# 公司归属统一走 get_actor_context（JWT 员工 / 平台用户 / 绑定或未绑定用户的 API Key 都适用），
# 不再直接读 g.current_user（未绑定用户的 API Key 下它是 None，以前会 500）


@api_ns.doc(security="jsonWebToken")
@api_ns.route('/')
class DepartmentList(Resource):

    @permission_required(["all_access", "company_all_access", "department_read"])
    @api_ns.expect(pagination_parser)
    @api_ns.marshal_with(pagination_model)
    def get(self):
        """Get a paginated list of departments"""
        args = pagination_parser.parse_args()
        page = args.get('page')
        per_page = args.get('per_page')
        get_all = args.get('all', False)  # Flag to get all data

        filters = {
            'is_active': args.get('is_active'),
            'name': args.get('name'),
            # 员工 / 公司级 API Key 只能看本公司；平台管理员可按参数筛选
            'company_id': get_actor_company_id() or args.get('company_id'),
        }

        query = DepartmentService.list_departments(filters)

        return paginate(query, page, per_page, get_all)

    @permission_required(["all_access", "company_all_access", "department_edit"])
    @api_ns.expect(department_input_model)
    @api_ns.marshal_with(department_model)
    def post(self):
        """Create a new department"""
        data = api_ns.payload
        created_by = require_actor_user_id()

        # 有归属公司的调用方只能在本公司建部门：请求体里写了别的公司 → 403；没写就落在本公司
        actor_company_id = get_actor_company_id()
        if actor_company_id is not None:
            if data.get('company_id') not in (None, actor_company_id):
                raise ForbiddenException("You do not have permission to create a department in this company.", 12001)
            data['company_id'] = actor_company_id
        elif not data.get('company_id'):
            raise BadRequestException("company_id is required", 14015)

        new_department = DepartmentService.create_department(data, created_by)

        return new_department, 201


@api_ns.doc(security="jsonWebToken")
@api_ns.route('/<int:department_id>')
class DepartmentDetail(Resource):

    @permission_required(["all_access", "company_all_access", "department_read"])
    @api_ns.marshal_with(department_model)
    def get(self, department_id):
        """Get department details"""
        return get_company_owned(Department, department_id, 'department')

    @permission_required(["all_access", "company_all_access", "department_edit"])
    @api_ns.expect(department_input_model)
    @api_ns.marshal_with(department_model)
    def put(self, department_id):
        """Update department details"""
        data = api_ns.payload
        get_company_owned(Department, department_id, 'department')
        # 归属公司不允许通过更新接口迁移（service 也不改 company_id）
        data.pop('company_id', None)

        return DepartmentService.update_department(department_id, data)

    @permission_required(["all_access", "company_all_access", "department_delete"])
    def delete(self, department_id):
        """Delete a department"""
        get_company_owned(Department, department_id, 'department')
        DepartmentService.delete_department(department_id)
        return {"message": "Department deleted successfully"}, 200
