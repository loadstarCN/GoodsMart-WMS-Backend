"""Company-owned roles. Global roles are read-only and permission grants are bounded."""
import uuid
from flask import request
from flask_restx import Resource
from extensions.db import db
from extensions.error import BadRequestException, ConflictException, ForbiddenException
from extensions.transaction import transactional
from system.common import permission_required
from system.common.permissions import get_actor_context, assert_grantable_permissions, PLATFORM_ONLY_PERMISSIONS
from system.user.models import Role, Permission
from warehouse.common.locks import lock_row
from .models import Company
from .schemas import api_ns

READ = ['all_access','company_all_access','company_roles_read','company_roles_edit','staff_read','staff_edit']
EDIT = ['all_access','company_all_access','company_roles_edit']
FORBIDDEN = PLATFORM_ONLY_PERMISSIONS | {'company_all_access'}


def company_id():
    value = get_actor_context()[2]
    if value is None:
        raise BadRequestException('A company staff session is required')
    return value


def grantable(role):
    try:
        assert_grantable_permissions({p.name for p in role.permissions})
        return True
    except ForbiddenException:
        return False


def serialize(role):
    from warehouse.staff.models import Staff
    from system.user.models import User
    users = role.users.filter(User.id.in_(db.session.query(Staff.id).filter(Staff.company_id==company_id()))).count()
    return dict(id=role.id,name=role.name,display_name=role.display_name or role.name,
        description=role.description,is_active=role.is_active,revision=role.revision,
        company_id=role.company_id,readonly=role.company_id is None,is_builtin=role.is_builtin,
        permissions=[p.name for p in role.permissions],assignable=grantable(role),users=users)


def get_owned(role_id):
    role = db.session.get(Role,role_id)
    if role is None or role.company_id != company_id():
        raise ForbiddenException('Company role is not available in this scope',12001)
    return role


@transactional
def save(body, role_id=None):
    cid = company_id()
    lock_row(db.session.get(Company,cid))
    if not isinstance(body,dict):
        raise BadRequestException('Role body is required')
    role = get_owned(role_id) if role_id else None
    if role:
        assert_grantable_permissions({p.name for p in role.permissions})
    if role and (type(body.get('revision')) is not int or body['revision'] != role.revision):
        raise ConflictException('Role changed; reload before saving',field='revision')
    name, description = body.get('display_name'), body.get('description','')
    if not isinstance(name,str) or not 1 <= len(name.strip()) <= 80 or any(ord(c)<32 for c in name):
        raise BadRequestException('Role name must contain 1 to 80 characters',field='display_name')
    if not isinstance(description,str) or len(description)>255:
        raise BadRequestException('Description must contain at most 255 characters',field='description')
    if type(body.get('is_active',True)) is not bool:
        raise BadRequestException('is_active must be a boolean',field='is_active')
    duplicate = Role.query.filter(Role.company_id==cid,db.func.lower(Role.display_name)==name.strip().lower())
    if role:
        duplicate=duplicate.filter(Role.id!=role.id)
    if duplicate.first():
        raise ConflictException('A role with this name already exists in the company',field='display_name')
    ids=body.get('permissions',[])
    if not isinstance(ids,list) or any(type(value) is not int or value<=0 for value in ids):
        raise BadRequestException('Permission IDs must be a list of positive integers',field='permissions')
    ids=set(ids)
    permissions=Permission.query.filter(Permission.id.in_(ids)).all() if ids else []
    if len(permissions)!=len(ids):
        raise BadRequestException('Unknown permission',field='permissions')
    names={p.name for p in permissions}
    if names & FORBIDDEN:
        raise ForbiddenException('Company roles cannot grant platform or administrator permissions',12006)
    assert_grantable_permissions(names)
    if role is None:
        role=Role(name=f'company:{cid}:{uuid.uuid4().hex}',company_id=cid,revision=0)
        db.session.add(role)
    role.display_name,role.description,role.is_active=name.strip(),description,body.get('is_active',True)
    role.permissions=permissions
    role.revision+=1
    db.session.flush()
    return serialize(role)


@api_ns.route('/roles')
class CompanyRoles(Resource):
    @permission_required(READ)
    def get(self):
        cid=company_id()
        roles=Role.query.filter(db.or_(Role.company_id==cid,Role.company_id.is_(None))).order_by(Role.id).all()
        # Do not expose platform roles to warehouse staff.
        roles=[row for row in roles if row.company_id is not None or not {p.name for p in row.permissions}&PLATFORM_ONLY_PERMISSIONS]
        return dict(items=[serialize(row) for row in roles])

    @permission_required(EDIT)
    def post(self):
        return save(request.get_json(silent=True)),201


@api_ns.route('/roles/permissions')
class CompanyRolePermissions(Resource):
    @permission_required(READ)
    def get(self):
        company_id()
        actor=get_actor_context()
        rows=Permission.query.filter(~Permission.name.in_(FORBIDDEN)).order_by(Permission.name).all()
        return dict(items=[dict(id=row.id,name=row.name,description=row.description,
            grantable=actor[0] or 'company_all_access' in actor[1] or row.name in actor[1]) for row in rows])


@api_ns.route('/roles/<int:role_id>')
class CompanyRoleDetail(Resource):
    @permission_required(EDIT)
    def put(self,role_id):
        return save(request.get_json(silent=True),role_id)

    @permission_required(EDIT)
    @transactional
    def delete(self,role_id):
        lock_row(db.session.get(Company,company_id()))
        role=get_owned(role_id)
        assert_grantable_permissions({p.name for p in role.permissions})
        body=request.get_json(silent=True) or {}
        if type(body.get('revision')) is not int or body['revision']!=role.revision:
            raise ConflictException('Role changed; reload before deleting',field='revision')
        if role.users.count():
            raise ConflictException('Assigned roles cannot be deleted; disable the role instead')
        db.session.delete(role)
        return dict(message='Role deleted')
