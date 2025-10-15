import pytest
from sqlalchemy.exc import IntegrityError
from .helpers import *


def test_get_warehouses(client, access_token):
    response = client.get('/warehouse/?page=1&per_page=10', headers={
        'Authorization': f'Bearer {access_token}'
    })
    assert response.status_code == 200
    data = response.get_json()
    assert 'items' in data
    assert len(data['items']) > 0


def test_create_warehouse(client, access_token):
    with client.application.app_context():
        company = get_company()
        response = client.post('/warehouse/', headers={
            'Authorization': f'Bearer {access_token}'
        }, json={
            'name': 'Warehouse B',
            'address': 'Warehouse Address B',
            'phone': '987654321',
            'zip_code': '54321',
            'company_id': company.id,
            'manager_id': None,
            'is_active': True
        })
        assert response.status_code == 201
        data = response.get_json()
        assert data['name'] == 'Warehouse B'
        assert data['address'] == 'Warehouse Address B'


def test_get_warehouse_details(client, access_token):
    with client.application.app_context():
        warehouse = get_warehouse()
        response = client.get(f'/warehouse/{warehouse.id}', headers={
            'Authorization': f'Bearer {access_token}'
        })
        assert response.status_code == 200
        data = response.get_json()
        assert data['id'] == warehouse.id
        assert data['name'] == warehouse.name


def test_update_warehouse(client, access_token):
    with client.application.app_context():
        warehouse = get_warehouse()
        response = client.put(f'/warehouse/{warehouse.id}', headers={
            'Authorization': f'Bearer {access_token}'
        }, json={
            'name': 'Updated Warehouse A',
            'address': 'Updated Address A',
            'phone': '111111111',
            'zip_code': '67890',
            'is_active': False
        })
        assert response.status_code == 200
        data = response.get_json()
        assert data['name'] == 'Updated Warehouse A'
        assert data['is_active'] is False


def test_delete_warehouse(client, access_token):
    with client.application.app_context():
        warehouse = get_warehouse_by_id(2)
        response = client.delete(f'/warehouse/{warehouse.id}', headers={
            'Authorization': f'Bearer {access_token}'
        })
        assert response.status_code == 200
        data = response.get_json()
        assert data['message'] == 'Warehouse deleted successfully'

        # Ensure the warehouse is deleted
        deleted_warehouse = get_warehouse_by_id(2)
        assert deleted_warehouse is None