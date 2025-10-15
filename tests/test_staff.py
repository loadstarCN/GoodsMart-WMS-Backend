from .helpers import *

def test_get_staff(client, access_token):
    response = client.get('/staff/?page=1&per_page=10', headers={
        'Authorization': f'Bearer {access_token}'
    })
    assert response.status_code == 200
    data = response.get_json()
    assert 'items' in data
    assert len(data['items']) > 0
    # 验证返回数据中包含 warehouses 字段
    for item in data['items']:
        assert 'warehouses' in item
        assert isinstance(item['warehouses'], list)



def test_create_staff(client, access_token):
    with client.application.app_context():
        company = get_company()
        department = get_department()
        warehouse = get_warehouse()
        response = client.post('/staff/', headers={
            'Authorization': f'Bearer {access_token}'
        }, json={
            'user_name': 'New Staff',
            'email': 'newstaff@company.com',
            'password': 'password123',
            'phone': '555555555',
            'company_id': company.id,
            'department_id': department.id,
            'position': 'Developer',
            'is_active': True,
            'warehouse_ids': [warehouse.id]
        })
        assert response.status_code == 201
        data = response.get_json()
        assert data['user_name'] == 'New Staff'
        assert data['email'] == 'newstaff@company.com'
        # 验证 warehouses 数据
        assert 'warehouses' in data
        assert len(data['warehouses']) == 1
        assert data['warehouses'][0]['id'] == warehouse.id


def test_get_staff_details(client, access_token):
    with client.application.app_context():
        staff = get_operator_user()
        response = client.get(f'/staff/{staff.id}', headers={
            'Authorization': f'Bearer {access_token}'
        })
        assert response.status_code == 200
        data = response.get_json()
        assert data['id'] == staff.id
        assert data['user_name'] == staff.user_name

        # 验证详细信息中包含 warehouses 字段
        assert 'warehouses' in data
        assert isinstance(data['warehouses'], list)



def test_update_staff(client, access_token):
    with client.application.app_context():
        staff = get_operator_user()
        response = client.put(f'/staff/{staff.id}', headers={
            'Authorization': f'Bearer {access_token}'
        }, json={
            'user_name': 'Updated Staff',
            'email': 'updatedstaff@company.com',
            'position': 'Updated Position',
            'is_active': False,
            'warehouse_ids': []  # 更新仓库关联为空
        })
        assert response.status_code == 200
        data = response.get_json()
        assert data['user_name'] == 'Updated Staff'
        assert data['is_active'] is False
        # 验证更新后 warehouses 为空
        assert 'warehouses' in data
        assert len(data['warehouses']) == 0


def test_delete_staff(client, access_token):
    with client.application.app_context():
        staff = get_warehouse_admin_user()
        response = client.delete(f'/staff/{staff.id}', headers={
            'Authorization': f'Bearer {access_token}'
        })
        assert response.status_code == 200
        data = response.get_json()
        assert data['message'] == 'Staff deleted successfully'

        # Ensure the staff is deleted
        deleted_staff = db.session.get(Staff, staff.id)
        assert deleted_staff is None
