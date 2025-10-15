from .helpers import *

def test_get_departments(client, access_token):
    response = client.get('/department/?page=1&per_page=10', headers={
        'Authorization': f'Bearer {access_token}'
    })
    assert response.status_code == 200
    data = response.get_json()
    assert 'items' in data
    assert len(data['items']) > 0

def test_create_department(client, access_token):
    with client.application.app_context():
        company = get_company()
        response = client.post('/department/', headers={
            'Authorization': f'Bearer {access_token}'
        }, json={
            'name': 'Finance',
            'description': 'Finance Department',
            'company_id': company.id,
            'is_active': True,
            'created_by': 1
        })
        assert response.status_code == 201
        data = response.get_json()
        assert data['name'] == 'Finance'
        assert data['description'] == 'Finance Department'

def test_get_department_details(client, access_token):
    with client.application.app_context():
        department = get_department()
        response = client.get(f'/department/{department.id}', headers={
            'Authorization': f'Bearer {access_token}'
        })
        assert response.status_code == 200
        data = response.get_json()
        assert data['id'] == department.id
        assert data['name'] == department.name

def test_update_department(client, access_token):
    with client.application.app_context():
        department = get_department()
        response = client.put(f'/department/{department.id}', headers={
            'Authorization': f'Bearer {access_token}'
        }, json={
            'name': 'Updated Department',
            'description': 'Updated Description',
            'is_active': False,
            'company_id': department.company_id,
            'created_by': 1
        })
        assert response.status_code == 200
        data = response.get_json()
        assert data['name'] == 'Updated Department'
        assert data['is_active'] is False

def test_delete_department(client, access_token):
    with client.application.app_context():
        department = get_department()
        response = client.delete(f'/department/{department.id}', headers={
            'Authorization': f'Bearer {access_token}'
        })
        assert response.status_code == 200
        data = response.get_json()
        assert data['message'] == 'Department deleted successfully'

        # Ensure the department is deleted
        deleted_department = get_department_by_id(department.id)
        assert deleted_department is None
