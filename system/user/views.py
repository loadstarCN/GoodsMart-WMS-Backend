from flask import g
from flask_jwt_extended import get_current_user, jwt_required
from flask_restx import Resource, abort
from extensions.error import UnauthorizedException
from system.common import paginate, permission_required
from .schemas import api_ns, user_model, role_model, permission_model, user_input_model, role_input_model, permission_input_model, pagination_parser, user_pagination_model, role_pagination_model, permission_pagination_model, login_model,password_change_model
from .services import UserService, RoleService, PermissionService

# ------------------------
# 用户路由
# ------------------------


@api_ns.doc(security="jsonWebToken")
@api_ns.route('/test')
class Test(Resource):
    
    @permission_required(["all_access","user_read"])
    def get(self):
        # 打印当前用户信息
        _user = g.get('current_user')
        print(f"Current User: {g.current_user}")
        if _user:
            return {"id": _user.id, "name": _user.user_name}, 200
        return {"message": "No user found"}, 404
    
@api_ns.route('/login')
class UserLogin(Resource):

    @api_ns.expect(login_model)
    def post(self):
        """用户登录"""
        data = api_ns.payload
        account = data.get("account")
        password = data.get("password")

        # 调用 UserService 的登录方法
        result = UserService.login(account, password)

        return result, 200

@api_ns.doc(security="jsonWebToken")
@api_ns.route('/refresh')
class TokenRefresh(Resource):

        @jwt_required(refresh=True)
        def post(self):
            """刷新令牌"""
            current_user = get_current_user()
            if not current_user:
                raise UnauthorizedException("Token is invalid", 11002)
            
            result = UserService.refresh_token(current_user)
            return result, 200


@api_ns.doc(security="jsonWebToken")
@api_ns.route('/change-password')
class UserPassword(Resource):

    @api_ns.expect(password_change_model)
    def put(self):
        """修改用户密码"""
        data = api_ns.payload
        old_password = data.get('old_password')
        new_password = data.get('new_password')

        current_user = get_current_user()
        if not current_user:
            raise UnauthorizedException("Token is invalid", 11002)

        # 调用密码修改服务
        UserService.change_password(
            user_id=current_user.id,
            old_password=old_password,
            new_password=new_password
        )
        return {"message": "Password updated successfully"}, 200
        

@api_ns.doc(security="jsonWebToken")
@api_ns.route('/users')
class UserList(Resource):

    @permission_required(["all_access", "user_read"])
    @api_ns.expect(pagination_parser)
    @api_ns.marshal_with(user_pagination_model)
    def get(self):
        """获取用户列表（分页）"""
        args = pagination_parser.parse_args()
        page = args.get('page')
        per_page = args.get('per_page')

        filters = {
            'username': args.get('username'),
            'email': args.get('email')
        }

        query = UserService.list_users(filters)
        return paginate(query, page, per_page), 200

    @permission_required(["all_access", "user_edit"])
    @api_ns.expect(user_input_model)
    @api_ns.marshal_with(user_model)
    def post(self):
        """创建新用户"""
        data = api_ns.payload
        new_user = UserService.create_user(data)
        return new_user, 201


@api_ns.doc(security="jsonWebToken")
@api_ns.route('/users/<int:user_id>')
class UserDetail(Resource):

    @permission_required(["all_access", "user_read"])
    @api_ns.marshal_with(user_model)
    def get(self, user_id):
        """获取用户详情"""
        user = UserService.get_user(user_id)
        return user

    @permission_required(["all_access", "user_edit"])
    @api_ns.expect(user_input_model)
    @api_ns.marshal_with(user_model)
    def put(self, user_id):
        """更新用户信息"""
        data = api_ns.payload
        updated_user = UserService.update_user(user_id, data)        
        return updated_user

    @permission_required(["all_access", "user_delete"])
    def delete(self, user_id):
        """删除用户"""
        UserService.delete_user(user_id)
        return {"message": "User deleted successfully"}, 200


# ------------------------
# 角色路由
# ------------------------

@api_ns.doc(security="jsonWebToken")
@api_ns.route('/roles')
class RoleList(Resource):

    @permission_required(["all_access", "role_read"])
    @api_ns.expect(pagination_parser)
    @api_ns.marshal_with(role_pagination_model)
    def get(self):
        """获取角色列表（分页）"""
        args = pagination_parser.parse_args()
        page = args.get('page')
        per_page = args.get('per_page')

        filters = {
            'name': args.get('name'),
            'is_active': args.get('is_active')
        }

        query = RoleService.list_roles(filters)
        return paginate(query, page, per_page)

    @permission_required(["all_access", "role_edit"])
    @api_ns.expect(role_input_model)
    @api_ns.marshal_with(role_model)
    def post(self):
        """创建新角色"""
        data = api_ns.payload
        new_role = RoleService.create_role(data)
        return new_role, 201


@api_ns.doc(security="jsonWebToken")
@api_ns.route('/roles/<int:role_id>')
class RoleDetail(Resource):

    @permission_required(["all_access", "role_read"])
    @api_ns.marshal_with(role_model)
    def get(self, role_id):
        """获取角色详情"""
        role = RoleService.get_role(role_id)
        return role

    @permission_required(["all_access", "role_edit"])
    @api_ns.expect(role_input_model)
    @api_ns.marshal_with(role_model)
    def put(self, role_id):
        """更新角色信息"""
        data = api_ns.payload
        updated_role = RoleService.update_role(role_id, data)
        return updated_role

    @permission_required(["all_access", "role_delete"])
    def delete(self, role_id):
        """删除角色"""
        RoleService.delete_role(role_id)
        return {"message": "Role deleted successfully"}, 200


# ------------------------
# 权限路由
# ------------------------

@api_ns.doc(security="jsonWebToken")
@api_ns.route('/permissions')
class PermissionList(Resource):

    @permission_required(["all_access", "permission_read"])
    @api_ns.expect(pagination_parser)
    @api_ns.marshal_with(permission_pagination_model)
    def get(self):
        """获取所有权限（分页）"""
        args = pagination_parser.parse_args()
        page = args.get('page')
        per_page = args.get('per_page')

        filters = {
            'name': args.get('name'),
            'is_active': args.get('is_active')
        }

        query = PermissionService.list_permissions(filters)
        return paginate(query, page, per_page)

    @permission_required(["all_access", "permission_edit"])
    @api_ns.expect(permission_input_model)
    @api_ns.marshal_with(permission_model)
    def post(self):
        """创建新权限"""
        data = api_ns.payload
        new_permission = PermissionService.create_permission(data)
        return new_permission, 201


@api_ns.doc(security="jsonWebToken")
@api_ns.route('/permissions/<int:permission_id>')
class PermissionDetail(Resource):

    @permission_required(["all_access", "permission_read"])
    @api_ns.marshal_with(permission_model)
    def get(self, permission_id):
        """获取权限详情"""
        permission = PermissionService.get_permission(permission_id)
        return permission

    @permission_required(["all_access", "permission_edit"])
    @api_ns.expect(permission_input_model)
    @api_ns.marshal_with(permission_model)
    def put(self, permission_id):
        """更新权限信息"""
        data = api_ns.payload
        updated_permission = PermissionService.update_permission(permission_id, data)
        return updated_permission

    @permission_required(["all_access", "permission_delete"])
    def delete(self, permission_id):
        """删除权限"""
        PermissionService.delete_permission(permission_id)
        return {"message": "Permission deleted successfully"}, 200
