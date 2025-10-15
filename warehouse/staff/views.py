from flask import g
from flask_restx import Resource, abort
from extensions import oss
from extensions.error import UnauthorizedException
from warehouse.common.decorators import warehouse_required
from .schemas import api_ns, staff_model, staff_input_model, pagination_parser, pagination_model,upload_parser
from system.common import paginate, permission_required
from .services import StaffService


@api_ns.doc(security="jsonWebToken")
@api_ns.route('/')
class StaffList(Resource):

    @permission_required(["all_access", "company_all_access", "staff_read"])
    @api_ns.expect(pagination_parser)
    @api_ns.marshal_with(pagination_model)
    def get(self):
        """获取所有员工"""
        args = pagination_parser.parse_args()
        page = args.get('page')
        per_page = args.get('per_page')

        filters = {
            'company_id': args.get('company_id'),
            'department_id': args.get('department_id'),
            'is_active': args.get('is_active'),
            'user_name': args.get('user_name'),
            'email': args.get('email'),
            'phone': args.get('phone'),
            'position': args.get('position'),
            'employee_number': args.get('employee_number'),
            'hire_date': args.get('hire_date'),
            'keyword': args.get('keyword')
        }

        if g.current_user.type == 'staff':
            filters['company_id'] = g.current_user.company_id

        query = StaffService.list_staff(filters)
        return paginate(query, page, per_page)


    @permission_required(["all_access", "company_all_access", "staff_edit"])
    @api_ns.expect(staff_input_model)
    @api_ns.marshal_with(staff_model)
    def post(self):
        """创建新员工"""
        data = api_ns.payload
        created_by = g.current_user.id
        
        # 创建新员工
        new_staff = StaffService.create_staff(data, created_by)
        return new_staff, 201


@api_ns.doc(security="jsonWebToken")
@api_ns.route('/<int:staff_id>')
class StaffDetail(Resource):

    @permission_required(["all_access", "company_all_access", "staff_read"])
    @api_ns.marshal_with(staff_model)
    def get(self, staff_id):
        """获取员工详情"""
        staff = StaffService.get_staff(staff_id)
        return staff

    @permission_required(["all_access", "company_all_access", "staff_edit"])
    @api_ns.expect(staff_input_model)
    @api_ns.marshal_with(staff_model)
    def put(self, staff_id):
        """更新员工信息"""
        data = api_ns.payload

        updated_staff = StaffService.update_staff(staff_id, data)

        return updated_staff

    @permission_required(["all_access", "company_all_access", "staff_delete"])
    def delete(self, staff_id):
        """删除员工"""
        StaffService.delete_staff(staff_id)

        return {"message": "Staff deleted successfully"}, 200


@api_ns.doc(security="jsonWebToken")
@api_ns.route('/current')
class StaffCurrent(Resource):

    @permission_required(["all_access", "company_all_access", "staff_read"])
    @warehouse_required()
    @api_ns.marshal_with(staff_model)
    def get(self):
        """获取当前用户信息"""
        if hasattr(g, "current_user") and g.current_user:
            if g.current_user.type == "staff":
                # 调用 services 中的 get_staff 方法
                staff = StaffService.get_staff(g.current_user.id)
                if not staff:
                    raise UnauthorizedException("Current user is not a staff member", 11005)
                staff.warehouses = g.accessible_warehouses
            else:
                raise UnauthorizedException("Current user is not a staff member", 11005)
        else:
            raise UnauthorizedException("No current user found", 11004)
        
        return staff
    

@api_ns.route('/image/upload')
class StaffAvatarUpload(Resource):
    @permission_required(["all_access", "company_all_access", "staff_edit"])
    @api_ns.expect(upload_parser)
    def post(self):
        """Upload an image"""
        args = upload_parser.parse_args()
        uploaded_file = args['file']
        # 获取OSS实例
        file_url = oss.upload_file(uploaded_file, "user/images/")
        return {"file_url": file_url}, 200