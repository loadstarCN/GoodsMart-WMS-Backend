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
        # 用没有存货的库位（LOC003）：有货的库位不能停用（B-55，见 test_inventory_guards.py）
        location = get_location_by_id(3)
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


def test_location_zone_counts_exact_filter_and_scope(client, access_company_admin_token):
    with client.application.app_context():
        warehouse = get_warehouse()
        warehouse_id = warehouse.id
        other_company = Company.query.filter_by(name='Company B').first()
        foreign = Warehouse(name='Zone foreign', address='B Street', phone='000', zip_code='00000', company_id=other_company.id, created_by=get_admin_user().id)
        db.session.add(foreign)
        db.session.flush()
        for code, active in [('A-01-1', True), ('A-02', False), ('AB-01', True), ('A%-01', True), ('-01', True)]:
            db.session.add(Location(warehouse_id=warehouse_id, code=code, location_type='standard', is_active=active, created_by=get_admin_user().id))
        db.session.add(Location(warehouse_id=foreign.id, code='FOREIGN-01', location_type='standard', created_by=get_admin_user().id))
        db.session.commit()
        foreign_id = foreign.id
    headers = {'Authorization': f'Bearer {access_company_admin_token}', 'X-WAREHOUSE-ID': str(warehouse_id)}
    response = client.get('/location/zones', headers=headers)
    assert response.status_code == 200, response.json
    zones = {r['zone']: r for r in response.json['items']}
    assert zones['A'] == {'zone':'A', 'active':1, 'inactive':1}
    assert 'FOREIGN' not in zones
    assert '-01' in zones
    for zone, code, active in [('A','A-01-1','true'),('A','A-02','false'),('A%','A%-01','true')]:
        result = client.get('/location/', headers=headers, query_string={'zone':zone,'is_active':active})
        assert result.status_code == 200, result.json
        assert [r['code'] for r in result.json['items']] == [code]
    headers['X-WAREHOUSE-ID'] = str(foreign_id)
    assert client.get('/location/zones', headers=headers).status_code == 403
