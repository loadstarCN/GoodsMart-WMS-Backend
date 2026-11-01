from tests.helpers import *
from datetime import datetime, timezone
from tests.test_inventory_guards import _h, _ok


def test_login_timestamp_only_changes_on_success(client, access_token):
    old = datetime(2025, 1, 1, tzinfo=timezone.utc)
    with client.application.app_context():
        user = get_admin_user()
        user.last_login_at = old
        db.session.commit()
        uid = user.id
    assert client.post('/user/login', json=dict(account='admin', password='wrong')).status_code == 401
    with client.application.app_context():
        assert db.session.get(User, uid).last_login_at == old
    assert client.post('/user/login', json=dict(account='admin', password='password')).status_code == 200
    data = _ok(client.get(f'/user/users/{uid}', headers=_h(access_token)))
    assert data['last_login_at'] and not data['last_login_at'].startswith('2025-01-01')


def test_admin_user_company_filter_status_and_owned_role_update(client, access_token):
    with client.application.app_context():
        company = get_company()
        cid = company.id
        company.is_active = False
        role = Role(name='company-role-review', display_name='Review staff', company_id=cid)
        db.session.add(role)
        db.session.commit()
    rows = _ok(client.get(f'/user/users?company_id={cid}&type=staff', headers=_h(access_token)))['items']
    assert len(rows) == 3
    assert all(row['company_id'] == cid and row['company_status'] == 'inactive' for row in rows)
    uid = rows[0]['id']
    updated = _ok(client.put(f'/user/users/{uid}', headers=_h(access_token), json={'roles':['company-role-review']}))
    assert updated['role_labels']['company-role-review'] == 'Review staff'
    assert client.put('/user/users/1', headers=_h(access_token), json={'roles':['company-role-review']}).status_code == 403
    assert _ok(client.get('/user/users?company_id=999999', headers=_h(access_token)))['total'] == 0
