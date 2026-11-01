from tests.helpers import *
from tests.test_inventory_guards import _h, _ok
from datetime import datetime, timezone
from warehouse.common import clock


def test_brand_image_upload_validates_pixels_and_platform_scope(client, access_token, access_company_admin_token, monkeypatch):
    from io import BytesIO
    from PIL import Image
    from extensions.oss import oss
    from system.settings.schemas import api_ns
    from system.settings import views
    client.application.extensions['tests.restx_api'].add_namespace(api_ns, path='/settings')
    endpoint = '/settings/presentation/image'
    saved = []
    def store(file, path):
        saved.append((file.read(), file.filename, path))
        return 'https://assets.example/brand.png'
    monkeypatch.setattr(oss, 'upload_file', store)
    def upload(raw, token=access_token):
        return client.post(endpoint, headers=_h(token), data={'file':(BytesIO(raw),'logo.png')})
    image = BytesIO()
    Image.new('RGB',(32,16),'red').save(image,format='PNG')
    assert upload(image.getvalue(),access_company_admin_token).status_code == 403
    assert upload(b'<svg onload="alert(1)"></svg>').status_code == 400
    assert upload(b'x'*(5*1024*1024+1)).status_code == 400
    assert not saved
    assert _ok(upload(image.getvalue()))['file_url'] == 'https://assets.example/brand.png'
    assert saved[0][1:] == ('brand.png','platform/brand/')
    with Image.open(BytesIO(saved[0][0])) as result:
        assert result.size == (32,16)
        assert result.format == 'PNG'
    # Uploading alone does not publish a changed brand configuration.
    assert _ok(client.get('/settings/presentation/public'))['brand'] == {}


def test_presentation_public_scope_schedule_and_conflict(client, access_token, access_company_admin_token, monkeypatch):
    from system.settings.schemas import api_ns
    from system.settings import views
    client.application.extensions['tests.restx_api'].add_namespace(api_ns, path='/settings')
    monkeypatch.setattr(clock, 'now', lambda: datetime(2026, 10, 31, 1, tzinfo=timezone.utc))
    endpoint = '/settings/presentation'
    assert client.get(endpoint, headers=_h(access_company_admin_token)).status_code == 403
    initial = _ok(client.get(endpoint, headers=_h(access_token)))
    initial['brand'] = dict(productName='Review WMS', logoOnDark='/brand/logo.svg')
    initial['pda'] = dict(url='https://example.test/app.apk', login=True, header=False)
    notice = dict(text='Current', kind='notice', enabled=True, login=True, header=False,
                  start='2026-10-31T01:00:00Z', end='2026-10-31T02:00:00Z')
    initial['notices'] = [notice, dict(notice, text='Future', start='2026-11-01T00:00:00Z', end=None),
                          dict(notice, text='Ended', start=None, end='2026-10-31T01:00:00Z'),
                          dict(notice, text='Disabled', enabled=False)]
    result = _ok(client.put(endpoint, headers=_h(access_token), json=initial))
    assert result['revision'] == 1
    assert client.put(endpoint, headers=_h(access_token), json=initial).status_code == 409
    public = _ok(client.get(endpoint+'/public'))
    assert [row['text'] for row in public['notices']] == ['Current']
    assert public['brand']['productName'] == 'Review WMS'
    assert set(public) == {'revision','brand','pda','notices'}
    for url in ('javascript:alert(1)', '//evil.example/logo', 'data:text/html,test', '/\\evil.example/logo'):
        result['brand']['logoOnDark'] = url
        assert client.put(endpoint, headers=_h(access_token), json=result).status_code == 400
    result['brand']['logoOnDark'] = ''
    result['notices'][0]['end'] = result['notices'][0]['start']
    assert client.put(endpoint, headers=_h(access_token), json=result).status_code == 400


def test_pda_release_metadata_validation_and_public_roundtrip(client, access_token):
    from system.settings.schemas import api_ns
    from system.settings import views
    client.application.extensions['tests.restx_api'].add_namespace(api_ns, path='/settings')
    endpoint = '/settings/presentation'
    data = _ok(client.get(endpoint, headers=_h(access_token)))
    data['pda'] = dict(url='/app.apk', version='1.4.2', releaseDate='2026-10-31',
                       versionCode=14, releasedAt='2026-10-31T13:21:00+09:00',
                       sizeBytes=12345678, sha256='AB' * 32, header=True, login=False)
    saved = _ok(client.put(endpoint, headers=_h(access_token), json=data))
    assert saved['pda']['sha256'] == 'ab' * 32
    assert saved['pda']['versionCode'] == 14 and saved['pda']['releasedAt'] == '2026-10-31T13:21:00+09:00'
    assert _ok(client.get(endpoint+'/public'))['pda'] == saved['pda']
    for key, value in [('releaseDate','2026-02-30'), ('releaseDate','20261031'),
                       ('sizeBytes',True), ('sizeBytes',-1), ('sizeBytes',1.5),
                       ('sizeBytes',11 * 1024**3), ('sha256','abc'), ('sha256','z'*64),
                       ('versionCode',True), ('versionCode',-1), ('versionCode',1.5),
                       ('releasedAt','2026-10-31T13:21:00'), ('releasedAt','invalid')]:
        bad = {**saved, 'pda':{**saved['pda'],key:value}}
        assert client.put(endpoint, headers=_h(access_token), json=bad).status_code == 400
    assert _ok(client.get(endpoint+'/public'))['revision'] == saved['revision']
    saved['pda'] = dict(url='', header=False, login=False)
    cleared = _ok(client.put(endpoint, headers=_h(access_token), json=saved))
    assert cleared['pda']['sizeBytes'] is None and cleared['pda']['sha256'] == ''


def test_ip_list_normalizes_ipv6_and_rejects_bad_or_duplicate_addresses(client, access_token):
    headers = _h(access_token)
    assert client.get('/ip-lists/status', headers=headers).status_code == 200
    for kind in ('blacklist', 'whitelist'):
        endpoint = '/ip-lists/'+kind
        for address in ('garbage', '192.0.2.0/24', '999.0.0.1'):
            assert client.post(endpoint, headers=headers, json={'ip_address':address}).status_code == 400
        result = _ok(client.post(endpoint, headers=headers, json={'ip_address':'2001:0db8:0000::1'}))
        assert result['ip_address'] == '2001:db8::1'
        assert client.post(endpoint, headers=headers, json={'ip_address':'2001:db8::1'}).status_code == 409
        assert client.delete(endpoint+'/'+str(result['id']), headers=headers).status_code == 200
