from sqlalchemy.exc import IntegrityError
from .helpers import *

def test_get_locations(client, access_token):
    response = client.get('/location/?page=1&per_page=10', headers={
        'Authorization': f'Bearer {access_token}'
    })
    assert response.status_code == 200
    data = response.get_json()
    assert 'items' in data
    assert len(data['items']) > 0


def test_create_location(client, access_token):
    with client.application.app_context():
        warehouse = get_warehouse()
        response = client.post('/location/', headers={
            'Authorization': f'Bearer {access_token}'
        }, json={
            'warehouse_id': warehouse.id,
            'code': 'LOC010',
            'description': 'New Location',
            'location_type': 'damaged',
            'width': 5.0,
            'depth': 10.0,
            'height': 2.5,
            'capacity': 1000.0,
            'is_active': True
        })
        assert response.status_code == 201
        data = response.get_json()
        assert data['code'] == 'LOC010'
        assert data['location_type'] == 'damaged'


def test_get_location_details(client, access_token):
    with client.application.app_context():
        location = get_location()
        response = client.get(f'/location/{location.id}', headers={
            'Authorization': f'Bearer {access_token}'
        })
        assert response.status_code == 200
        data = response.get_json()
        assert data['id'] == location.id
        assert data['code'] == location.code


def test_update_location(client, access_token):
    with client.application.app_context():
        location = get_location()
        response = client.put(f'/location/{location.id}', headers={
            'Authorization': f'Bearer {access_token}'
        }, json={
            'code': 'LOC001-UPDATED',
            'description': 'Updated Location Description',
            'location_type': 'standard',
            'is_active': False
        })
        assert response.status_code == 200
        data = response.get_json()
        assert data['code'] == 'LOC001-UPDATED'
        assert data['is_active'] is False


def test_delete_location(client, access_token):
    with client.application.app_context():
        location = get_location_by_id(3)
        response = client.delete(f'/location/{location.id}', headers={
            'Authorization': f'Bearer {access_token}'
        })
        assert response.status_code == 200
        data = response.get_json()
        assert data['message'] == 'Location deleted successfully'

        # Ensure the supplier is deleted
        deleted_location = get_location_by_id(location.id)
        assert deleted_location is None
