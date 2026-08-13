from .helpers import *

def test_get_recipients(client, access_token):
    response = client.get('/recipient/?page=1&per_page=10', headers={
        'Authorization': f'Bearer {access_token}'
    })
    assert response.status_code == 200
    data = response.get_json()
    assert 'items' in data
    assert len(data['items']) > 0


def test_create_recipient(client, access_token):
    response = client.post('/recipient/', headers={
        'Authorization': f'Bearer {access_token}'
    }, json={
        'name': 'Recipient B',
        'external_reference': 'web-address-b',
        'address': '456 Recipient Lane',
        'zip_code': '54321',
        'phone': '987654321',
        'email': 'recipientB@example.com',
        'contact': 'Jane Doe',
        'country': 'jp',
        'is_active': True,
        'company_id': 1
    })
    assert response.status_code == 201
    data = response.get_json()
    assert data['name'] == 'Recipient B'
    assert data['external_reference'] == 'web-address-b'
    assert data['email'] == 'recipientB@example.com'


def test_filter_recipient_by_external_reference(client, access_token):
    response = client.post('/recipient/', headers={
        'Authorization': f'Bearer {access_token}'
    }, json={
        'name': '同名收件人',
        'external_reference': 'web-address-exact',
        'address': 'Tokyo 1-2-3',
        'country': 'jp',
        'company_id': 1,
    })
    assert response.status_code == 201

    response = client.get(
        '/recipient/?external_reference=web-address-exact&page=1&per_page=10',
        headers={'Authorization': f'Bearer {access_token}'},
    )
    assert response.status_code == 200
    data = response.get_json()
    assert len(data['items']) == 1
    assert data['items'][0]['external_reference'] == 'web-address-exact'


def test_same_name_can_have_multiple_delivery_addresses(client, access_token):
    for reference, address in (
        ('same-name-address-1', 'Tokyo 1-1-1'),
        ('same-name-address-2', 'Osaka 2-2-2'),
    ):
        response = client.post('/recipient/', headers={
            'Authorization': f'Bearer {access_token}'
        }, json={
            'name': '山田太郎',
            'external_reference': reference,
            'address': address,
            'email': 'shared-recipient@example.com',
            'country': 'jp',
            'company_id': 1,
        })
        assert response.status_code == 201


def test_external_reference_lookup_includes_inactive_recipient(client, access_token):
    response = client.post('/recipient/', headers={
        'Authorization': f'Bearer {access_token}'
    }, json={
        'name': 'Inactive Recipient',
        'external_reference': 'inactive-address',
        'country': 'jp',
        'is_active': False,
        'company_id': 1,
    })
    assert response.status_code == 201

    response = client.get(
        '/recipient/?external_reference=inactive-address&page=1&per_page=10',
        headers={'Authorization': f'Bearer {access_token}'},
    )
    assert response.status_code == 200
    assert response.get_json()['items'][0]['external_reference'] == 'inactive-address'


def test_get_recipient_details(client, access_token):
    with client.application.app_context():
        recipient = get_recipient()
        response = client.get(f'/recipient/{recipient.id}', headers={
            'Authorization': f'Bearer {access_token}'
        })
        assert response.status_code == 200
        data = response.get_json()
        assert data['id'] == recipient.id
        assert data['name'] == recipient.name


def test_update_recipient(client, access_token):
    with client.application.app_context():
        recipient = get_recipient()
        response = client.put(f'/recipient/{recipient.id}', headers={
            'Authorization': f'Bearer {access_token}'
        }, json={
            'name': 'Recipient A Updated',
            'address': 'Updated Recipient Street',
            'phone': '111222333',
            'country': 'cn',
            'is_active': False
        })
        assert response.status_code == 200
        data = response.get_json()
        assert data['name'] == 'Recipient A Updated'
        assert data['is_active'] is False


def test_delete_recipient(client, access_token):
    with client.application.app_context():
        recipient = get_recipient_by_id(2)
        response = client.delete(f'/recipient/{recipient.id}', headers={
            'Authorization': f'Bearer {access_token}'
        })
        assert response.status_code == 200
        data = response.get_json()
        assert data['message'] == 'Recipient deleted successfully'

        # Ensure the recipient is deleted
        deleted_recipient = get_recipient_by_id(recipient.id)
        assert deleted_recipient is None
