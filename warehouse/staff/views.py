from flask import g
from flask_restx import Resource, marshal
from extensions import oss
from extensions.error import BadRequestException, ForbiddenException, UnauthorizedException
from system.common import paginate, permission_required
from system.common.permissions import get_actor_company_id
from .schemas import api_ns, staff_model, staff_input_model, pagination_parser, pagination_model, upload_parser, warehouse_model
from .services import StaffService


def _enforce_staff_scope(staff):
    """员工 / 公司级 API Key 只能操作本公司员工；平台管理员不受限"""
    company_id = get_actor_company_id()
    if company_id is not None and staff.company_id != company_id:
        raise ForbiddenException("Permission denied: staff belongs to another company", 12001)


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

        company_id = get_actor_company_id()
        if company_id is not None:
            filters['company_id'] = company_id

        query = StaffService.list_staff(filters)
        return paginate(query, page, per_page)


    @permission_required(["all_access", "company_all_access", "staff_edit"])
    @api_ns.expect(staff_input_model)
    @api_ns.marshal_with(staff_model)
    def post(self):
        """创建新员工（非平台管理员只能在本公司创建）"""
        data = api_ns.payload

        company_id = get_actor_company_id()
        if company_id is not None:
            data['company_id'] = company_id
        elif not data.get('company_id'):
            raise BadRequestException("company_id is required", 14015)

        actor = g.get('current_user')
        new_staff = StaffService.create_staff(data, actor.id if actor else None)
        return new_staff, 201


@api_ns.doc(security="jsonWebToken")
@api_ns.route('/<int:staff_id>')
class StaffDetail(Resource):

    @permission_required(["all_access", "company_all_access", "staff_read"])
    @api_ns.marshal_with(staff_model)
    def get(self, staff_id):
        """获取员工详情"""
        staff = StaffService.get_staff(staff_id)
        _enforce_staff_scope(staff)
        return staff

    @permission_required(["all_access", "company_all_access", "staff_edit"])
    @api_ns.expect(staff_input_model)
    @api_ns.marshal_with(staff_model)
    def put(self, staff_id):
        """更新员工信息（company_id 不可修改；不能停用自己）"""
        staff = StaffService.get_staff(staff_id)
        _enforce_staff_scope(staff)

        data = api_ns.payload
        data.pop('company_id', None)
        actor = g.get('current_user')
        updated_staff = StaffService.update_staff(staff_id, data, actor_id=actor.id if actor else None)

        return updated_staff

    @permission_required(["all_access", "company_all_access", "staff_delete"])
    def delete(self, staff_id):
        """删除员工"""
        staff = StaffService.get_staff(staff_id)
        _enforce_staff_scope(staff)

        actor = g.get('current_user')
        if actor and actor.id == staff.id:
            raise BadRequestException("You cannot delete your own account", 10011)

        StaffService.delete_staff(staff_id)

        return {"message": "Staff deleted successfully"}, 200


@api_ns.doc(security="jsonWebToken")
@api_ns.route('/current')
class StaffCurrent(Resource):

    @permission_required(["all_access", "company_all_access", "staff_read"])
    @api_ns.response(200, 'Success', staff_model)
    def get(self):
        """获取当前员工信息及其可访问仓库（登录后选仓前调用，不要求 X-WAREHOUSE-ID）"""
        user = g.get('current_user')
        if not user:
            raise UnauthorizedException("No current user found", 11004)
        if user.type != "staff":
            raise UnauthorizedException("Current user is not a staff member", 11005)

        staff = StaffService.get_staff(user.id)
        data = marshal(staff, staff_model)
        # 可访问仓库按角色计算，不修改 ORM 关系（避免把可访问列表当成归属关系写回）
        data['warehouses'] = marshal(StaffService.accessible_warehouses(staff), warehouse_model)
        return data, 200


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
