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
    assert data['email'] == 'recipientB@example.com'


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
