from .helpers import *


def test_get_suppliers(client, access_token):
    response = client.get('/supplier/?page=1&per_page=10', headers={
        'Authorization': f'Bearer {access_token}'
    })
    assert response.status_code == 200
    data = response.get_json()
    assert 'items' in data
    assert len(data['items']) > 0


def test_create_supplier(client, access_token):
    response = client.post('/supplier/', headers={
        'Authorization': f'Bearer {access_token}'
    }, json={
        'name': 'Supplier B',
        'address': '456 Supplier Lane',
        'phone': '987654321',
        'email': 'supplierB@example.com',
        'contact': 'Jane Doe',
        'is_active': True,
        'company_id': 1
    })
    assert response.status_code == 201
    data = response.get_json()
    assert data['name'] == 'Supplier B'
    assert data['email'] == 'supplierB@example.com'


def test_get_supplier_details(client, access_token):
    with client.application.app_context():
        supplier = get_supplier()
        response = client.get(f'/supplier/{supplier.id}', headers={
            'Authorization': f'Bearer {access_token}'
        })
        assert response.status_code == 200
        data = response.get_json()
        assert data['id'] == supplier.id
        assert data['name'] == supplier.name


def test_update_supplier(client, access_token):
    with client.application.app_context():
        supplier = get_supplier()
        response = client.put(f'/supplier/{supplier.id}', headers={
            'Authorization': f'Bearer {access_token}'
        }, json={
            'name': 'Supplier A Updated',
            'address': '789 Updated Street',
            'phone': '111222333',
            'is_active': False
        })
        assert response.status_code == 200
        data = response.get_json()
        assert data['name'] == 'Supplier A Updated'
        assert data['is_active'] is False


def test_delete_supplier(client, access_token):
    with client.application.app_context():
        supplier = get_supplier_by_id(2)
        response = client.delete(f'/supplier/{supplier.id}', headers={
            'Authorization': f'Bearer {access_token}'
        })
        assert response.status_code == 200
        data = response.get_json()
        assert data['message'] == 'Supplier deleted successfully'

        # Ensure the supplier is deleted
        deleted_supplier = get_supplier_by_id(supplier.id)
        assert deleted_supplier is None
