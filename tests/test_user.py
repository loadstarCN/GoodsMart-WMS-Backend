from datetime import timedelta

from system.user.services import UserService
from .helpers import *

def test_login(client):
    response = client.post('/user/login', json={
        'account': 'admin',
        'password': 'password'
    })
    assert response.status_code == 200
    assert 'access_token' in response.get_json()

def test_staff_login_with_valid_company(client):
    """测试staff用户所属公司未过期时的正常登录"""
    # 创建未过期公司（expired_at=未来时间）
    with client.application.app_context():
        admin_user = get_company_admin_user()
        company = get_company()
        company.expired_at = datetime.datetime.now() + timedelta(days=1)
        db.session.add(company)
        db.session.commit()

        staff_user = Staff.query.filter_by(company_id=company.id).first()
        if not staff_user:
            staff_user = Staff(
            user_name='staff_user',
            email='company_admin222@test.com',
            is_active=True,
            company_id=company.id,
            created_by=admin_user.id
            )
            staff_user.set_password('password')
            db.session.add(staff_user)
            db.session.commit()
        else:
            staff_user.set_password('password')
            db.session.commit()
    
        response = client.post('/user/login', json={
            'account': staff_user.user_name,
            'password': 'password'
        })
        assert response.status_code == 200
        assert 'access_token' in response.json

def test_staff_login_with_expired_company(client):
    """测试staff用户所属公司过期时的登录拦截"""
    # 创建已过期公司（expired_at=过去时间）
    with client.application.app_context():
        

        admin_user = get_company_admin_user()
        company = get_company()
        company.expired_at = datetime.datetime.now() - timedelta(days=1)
        db.session.add(company)
        db.session.commit()

        staff_user = Staff.query.filter_by(company_id=company.id).first()
        if not staff_user:
            staff_user = Staff(
            user_name='staff_user',
            email='company_admin222@test.com',
            is_active=True,
            company_id=company.id,
            created_by=admin_user.id
            )
            staff_user.set_password('password')
            db.session.add(staff_user)
            db.session.commit()
        else:
            staff_user.set_password('password')
            db.session.commit()

        
        response = client.post('/user/login', json={
            'account': staff_user.user_name,
            'password': 'password'
        })
        assert response.status_code == 401
        assert response.json["message"] == "Company is expired"

def test_non_staff_login(client):
    """测试非staff用户不受公司过期逻辑影响"""
    # 创建普通用户
    with client.application.app_context():
        admin_user = get_company_admin_user()
        admin_user.set_password('password')
        db.session.commit()
        
        response = client.post('/user/login', json={
            'account': admin_user.user_name,
            'password': 'password'
        })
        assert response.status_code == 200  # 正常登录不受影响

def test_get_users(client, access_token):
    response = client.get('/user/users', headers={
        'Authorization': f'Bearer {access_token}'
    })

    assert response.status_code == 200
    assert len(response.get_json()['items']) >= 1

def test_create_user(client, access_token):
    response = client.post('/user/users', headers={
        'Authorization': f'Bearer {access_token}'
    }, json={
        'user_name': 'test_user',
        'email': 'test_user@example.com',
        'password': 'test_password',
        'roles': ['user'],
        'is_active': True
    })
    assert response.status_code == 201
    assert response.get_json()['user_name'] == 'test_user'

def test_get_roles(client, access_token):
    response = client.get('/user/roles', headers={
        'Authorization': f'Bearer {access_token}'
    })
    assert response.status_code == 200
    assert len(response.get_json()['items']) >= 2

def test_create_role(client, access_token):
    response = client.post('/user/roles', headers={
        'Authorization': f'Bearer {access_token}'
    }, json={
        'name': 'new_role',
        'description': 'A new role',
        'is_active': True
    })
    assert response.status_code == 201
    assert response.get_json()['name'] == 'new_role'

def test_update_user(client, access_token):
    with client.application.app_context():
        user = get_admin_user()
        response = client.put(f'/user/users/{user.id}', headers={
            'Authorization': f'Bearer {access_token}'
        }, json={
            'user_name': 'updated_admin',
            'email': 'updated_admin@example.com',
            'is_active': True
        })
        assert response.status_code == 200
        assert response.get_json()['user_name'] == 'updated_admin'

def test_delete_user(client, access_token):
    with client.application.app_context():
        user = get_company_admin_user()
        response = client.delete(
            f'/user/users/{user.id}',
            headers={'Authorization': f'Bearer {access_token}'}
        )
        # 断言返回 400 错误状态码
        assert response.status_code == 200
        data = response.get_json()
        assert data['message'] == 'User deleted successfully'
        

def test_update_role(client, access_token):
    with client.application.app_context():
        role = get_admin_role()
        response = client.put(f'/user/roles/{role.id}', headers={
            'Authorization': f'Bearer {access_token}'
        }, json={
            'name': 'updated_admin_role',
            'description': 'Updated admin role',
            'is_active': True
        })
        assert response.status_code == 200
        assert response.get_json()['name'] == 'updated_admin_role'

def test_delete_role(client, access_token):
    with client.application.app_context():
        role = get_admin_role()
        response = client.delete(f'/user/roles/{role.id}', headers={
            'Authorization': f'Bearer {access_token}'
        })
        assert response.status_code == 200
        assert response.get_json()['message'] == 'Role deleted successfully'
        
# 只有admin用户才能创建，修改，删除用户和角色
def test_create_user_unauthorized(client, access_operator_token):
    response = client.post('/user/users', headers={
        'Authorization': f'Bearer {access_operator_token}'
    }, json={
        'user_name': 'test_user',
        'email': 'test_user@test.com',
        'password': 'test_password',
        'roles': ['user'],
        'is_active': True
    })
    assert response.status_code == 403
    assert response.get_json()['message'] == "Permission denied"

def test_self_password_change(client):
    """测试用户自主修改密码"""
    # 初始化测试数据
    with client.application.app_context():
        user = get_admin_user()

        # 登录获取令牌
        login_res = client.post('/user/login', json={
            'account': user.user_name,
            'password': 'password'
        })
        access_token = login_res.json['access_token']

        # 正常修改用例
        response = client.put('/user/change-password',
            headers={'Authorization': f'Bearer {access_token}'},
            json={
                "old_password": "password",
                "new_password": "NewPass456@"
            }
        )
        assert response.status_code == 200
        assert response.json["message"] == "Password updated successfully"

        # 验证数据库更新
        assert User.query.first().check_password('NewPass456@')


def test_password_change_with_invalid_old_password(client):
    """测试旧密码错误场景[3,6](@ref)"""
    with client.application.app_context():
        # 初始化测试用户并设置初始密码
        user = get_admin_user()
        user.set_password('OldPass123!')  # 设置正确初始密码
        db.session.commit()

        # 登录获取有效令牌
        login_res = client.post('/user/login', json={
            'account': user.user_name,
            'password': 'OldPass123!'  # 使用正确密码登录
        })
        access_token = login_res.json['access_token']

        # 发送错误旧密码的修改请求
        response = client.put('/user/change-password',
            headers={'Authorization': f'Bearer {access_token}'},
            json={
                "old_password": "WrongOldPass",  # 故意使用错误旧密码
                "new_password": "NewPass456@"
            }
        )

        # 验证响应状态和错误消息
        assert response.status_code == 400
        assert "Old password is incorrect" in response.json["message"]  # 参考错误提示规范

        # 验证数据库未更新
        updated_user = User.query.first()
        assert updated_user.check_password('OldPass123!') is True  # 密码保持原样
        assert updated_user.check_password('NewPass456@') is False

        
