from flask import g
from flask_restx import Resource, abort
from extensions.error import ForbiddenException
from system.common import paginate, permission_required
from .schemas import api_ns, department_model, department_input_model, pagination_parser, pagination_model
from .services import DepartmentService

@api_ns.doc(security="jsonWebToken")
@api_ns.route('/')
class DepartmentList(Resource):

    @permission_required(["all_access", "company_all_access", "department_read"])
    @api_ns.expect(pagination_parser)
    @api_ns.marshal_with(pagination_model)
    def get(self):
        """Get a paginated list of departments"""
        args = pagination_parser.parse_args()
        # args = api_ns.payload
        page = args.get('page')
        per_page = args.get('per_page')
        get_all = args.get('all', False)  # Flag to get all data

        filters = {
            'is_active': args.get('is_active'),
            'name': args.get('name')
        }
        
        # Check user type and get the company_id if user type is 'staff'
        if g.current_user.type == 'staff':
            company_id = g.current_user.company_id
            filters['company_id'] = company_id
        else:
            filters['company_id'] = args.get('company_id')
        
        # Get the filtered query using DepartmentService
        query = DepartmentService.list_departments(filters)

        return paginate(query, page, per_page, get_all)

    @permission_required(["all_access", "company_all_access", "department_edit"])
    @api_ns.expect(department_input_model)
    @api_ns.marshal_with(department_model)
    def post(self):
        """Create a new department"""
        data = api_ns.payload
        created_by = g.current_user.id
        # Check if the user is a staff member and restrict access to their company
        if g.current_user.type == 'staff':
            company_id = g.current_user.company_id
            if data.get('company_id') != company_id:
                raise ForbiddenException("You do not have permission to create a department in this company.", 12001)
        
        # Create the new department using DepartmentService
        new_department = DepartmentService.create_department(data, created_by)
        
        return new_department, 201


@api_ns.doc(security="jsonWebToken")
@api_ns.route('/<int:department_id>')
class DepartmentDetail(Resource):

    @permission_required(["all_access", "company_all_access", "department_read"])
    @api_ns.marshal_with(department_model)
    def get(self, department_id):
        """Get department details"""
        user = g.current_user
        department = DepartmentService.get_department(department_id)
        if user.type == 'staff':
            # 员工只能访问自己的公司信息
            if user.company_id != department.company_id:
                raise ForbiddenException("You do not have permission to view this company.", 12001)
            
        return department

    @permission_required(["all_access", "company_all_access", "department_edit"])
    @api_ns.expect(department_input_model)
    @api_ns.marshal_with(department_model)
    def put(self, department_id):
        """Update department details"""
        data = api_ns.payload
        user = g.current_user
        if user.type == 'staff':
            # 员工只能更新自己的公司信息
            department = DepartmentService.get_department(department_id)
            if user.company_id != department.company_id:
                raise ForbiddenException("You do not have permission to update this department.", 12001)

        updated_department = DepartmentService.update_department(department_id, data)
        return updated_department

    @permission_required(["all_access", "company_all_access", "department_delete"])
    def delete(self, department_id):
        """Delete a department"""
        user = g.current_user
        if user.type == 'staff':
            # 员工只能删除自己的公司信息
            department = DepartmentService.get_department(department_id)
            if user.company_id != department.company_id:
                raise ForbiddenException("You do not have permission to delete this department.", 12001)
        DepartmentService.delete_department(department_id)
        return {"message": "Department deleted successfully"}, 200
