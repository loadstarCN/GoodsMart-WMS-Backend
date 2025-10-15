from .helpers import *

def test_get_carriers(client, access_token):
    response = client.get('/carrier/?page=1&per_page=10', headers={
        'Authorization': f'Bearer {access_token}'
    })
    assert response.status_code == 200
    data = response.get_json()
    assert 'items' in data
    assert len(data['items']) > 0


def test_create_carrier(client, access_token):
    response = client.post('/carrier/', headers={
        'Authorization': f'Bearer {access_token}'
    }, json={
        'name': 'Carrier B',
        'address': '456 Carrier Lane',
        'zip_code': '54321',
        'phone': '987654321',
        'email': 'carrierB@example.com',
        'contact': 'Jane Doe',
        'is_active': True,
        'company_id': 1
    })
    assert response.status_code == 201
    data = response.get_json()
    assert data['name'] == 'Carrier B'
    assert data['email'] == 'carrierB@example.com'


def test_get_carrier_details(client, access_token):
    with client.application.app_context():
        carrier = get_carrier()
        response = client.get(f'/carrier/{carrier.id}', headers={
            'Authorization': f'Bearer {access_token}'
        })
        assert response.status_code == 200
        data = response.get_json()
        assert data['id'] == carrier.id
        assert data['name'] == carrier.name


def test_update_carrier(client, access_token):
    with client.application.app_context():
        carrier = get_carrier()
        response = client.put(f'/carrier/{carrier.id}', headers={
            'Authorization': f'Bearer {access_token}'
        }, json={
            'name': 'Carrier A Updated',
            'address': 'Updated Carrier Address',
            'phone': '111222333',
            'is_active': False
        })
        assert response.status_code == 200
        data = response.get_json()
        assert data['name'] == 'Carrier A Updated'
        assert data['is_active'] is False


def test_delete_carrier(client, access_token):
    with client.application.app_context():
        carrier = get_carrier()
        response = client.delete(f'/carrier/{carrier.id}', headers={
            'Authorization': f'Bearer {access_token}'
        })
        assert response.status_code == 200
        data = response.get_json()
        assert data['message'] == 'Carrier deleted successfully'

        # Ensure the carrier is deleted
        deleted_carrier = get_carrier_by_id(carrier.id)
        assert deleted_carrier is None
