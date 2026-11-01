from tests.helpers import *


def test_catalog_counts_real_role_links_and_restricts_tenants(client, access_token):
    from system.user.models import Permission, Role
    with client.application.app_context():
        custom = Permission(name='catalog_custom', description='Custom catalog permission')
        role = Role(name='catalog_role', permissions=[custom])
        db.session.add_all([custom, role])
        db.session.commit()
        company_token = create_access_token(identity=get_company_admin_user())
    headers = {'Authorization': f'Bearer {access_token}'}
    response = client.get('/user/permissions/catalog', headers=headers)
    assert response.status_code == 200, response.get_json()
    items = {p['name']: p for p in response.get_json()['items']}
    assert items['catalog_custom']['role_count'] == 1
    assert items['catalog_custom']['is_builtin'] is False
    assert items['catalog_custom']['is_platform_only'] is False
    assert items['all_access']['is_platform_only'] is True
    assert items['all_access']['is_builtin'] is True
    assert client.get('/user/permissions/catalog', headers={
        'Authorization': f'Bearer {company_token}'}).status_code == 403
