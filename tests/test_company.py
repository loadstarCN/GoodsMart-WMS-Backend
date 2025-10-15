from .helpers import *

def test_get_companies(client, access_token):
    response = client.get('/company/?page=1&per_page=10', headers={
        'Authorization': f'Bearer {access_token}'
    })
    assert response.status_code == 200
    data = response.get_json()
    assert 'items' in data
    assert len(data['items']) > 0

def test_create_company(client, access_token):
    response = client.post('/company/', headers={
        'Authorization': f'Bearer {access_token}'
    }, json={
        'name': 'Company C',
        'email': 'c@company.com',
        'phone': '123123123',
        'address': '789 Road',
        'is_active': True,
        'expired_at': '2028-12-31',
        'created_by': 1
    })
    assert response.status_code == 201
    data = response.get_json()
    assert data['name'] == 'Company C'
    assert data['email'] == 'c@company.com'

def test_get_company_details(client, access_token):
    with client.application.app_context():
        company = get_company()
        response = client.get(f'/company/{company.id}', headers={
            'Authorization': f'Bearer {access_token}'
        })
        assert response.status_code == 200
        data = response.get_json()
        assert data['id'] == company.id
        assert data['name'] == company.name

def test_update_company(client, access_token):
    with client.application.app_context():
        company = get_company()
        response = client.put(f'/company/{company.id}', headers={
            'Authorization': f'Bearer {access_token}'
        }, json={
            'name': 'Updated Company A',
            'email': 'updated@company.com',
            'phone': '555555555',
            'address': 'Updated Address',
            'is_active': False,
            'created_by': 1
        })
        assert response.status_code == 200
        data = response.get_json()
        assert data['name'] == 'Updated Company A'
        assert data['is_active'] is False

def test_delete_company(client, access_token):
    with client.application.app_context():
        company = get_company_by_id(2)
        response = client.delete(f'/company/{company.id}', headers={
            'Authorization': f'Bearer {access_token}'
        })
        assert response.status_code == 200
        data = response.get_json()
        assert data['message'] == 'Company deleted successfully'

        # Ensure the company is deleted
        deleted_company = get_company_by_id(2)
        assert deleted_company is None
