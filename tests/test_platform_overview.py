from tests.helpers import *
from datetime import datetime, timedelta, timezone
from warehouse.common import clock
from system.logs.models import ActivityLog
from tests.test_inventory_guards import _h, _ok


def test_platform_overview_is_platform_only_and_uses_aggregate_safe_data(client, access_token, access_company_admin_token):
    endpoint = '/company/platform-overview'
    assert client.get(endpoint, headers=_h(access_company_admin_token)).status_code == 403
    assert client.get(endpoint).status_code in (401, 403)
    with client.application.app_context():
        now = clock.now()
        company = get_company()
        company.expired_at = now + timedelta(days=9)
        db.session.add(ActivityLog(actor='Unknown', endpoint='/system/user/login', method='POST',
            status_code=401, ip_address='192.0.2.1', created_at=now-timedelta(minutes=5),
            request_data='must-not-leak', response_data='secret'))
        db.session.add(ActivityLog(actor='Unknown', endpoint='/old', method='POST', status_code=500,
                                  created_at=now-timedelta(hours=25)))
        db.session.commit()
        expected_tenants = Company.query.count()
        cid = company.id
    data = _ok(client.get(endpoint, headers=_h(access_token)))
    assert data['tenants']['total'] == expected_tenants
    assert data['tenants']['total'] == data['tenants']['active'] + data['tenants']['inactive']
    assert data['users']['total'] == data['users']['platform'] + data['users']['staff']
    assert data['documents']['total'] == data['documents']['dn'] + data['documents']['asn']
    assert any(row['id'] == cid and not row['expired'] for row in data['expiring'])
    assert any(row['endpoint'] == '/system/user/login' and row['status'] == 401 for row in data['errors']['items'])
    assert all(row['endpoint'] != '/old' for row in data['errors']['items'])
    assert 'must-not-leak' not in str(data) and 'secret' not in str(data)
    summary_url = f'/company/{cid}/platform-summary'
    assert client.get(summary_url, headers=_h(access_company_admin_token)).status_code == 403
    summary = _ok(client.get(summary_url, headers=_h(access_token)))
    assert summary['staff_count'] == 3
    assert summary['admins'][0]['name'] == 'company_admin'
    assert all(row['timezone'] for row in summary['warehouses'])
    assert summary['staff_active'] == 3
    assert summary['creator_name'] and summary['created_at']
    assert summary['api_keys']['total'] >= summary['api_keys']['active']


def test_tenant_with_only_company_role_cannot_be_deleted(client, access_token):
    with client.application.app_context():
        company = Company(name='Role owner', created_by=get_admin_user().id)
        db.session.add(company)
        db.session.flush()
        db.session.add(Role(name='owned-retained-role', company_id=company.id, display_name='Retained'))
        db.session.commit()
        cid = company.id
    summary = _ok(client.get(f'/company/{cid}/platform-summary', headers=_h(access_token)))
    assert summary['related'] == {'roles': 1}
    assert client.delete(f'/company/{cid}', headers=_h(access_token)).status_code == 409


def test_platform_overview_day_uses_platform_zone_and_shipping_timestamp(client, access_token, monkeypatch):
    frozen = datetime(2026, 10, 31, 1, tzinfo=timezone.utc)
    monkeypatch.setattr(clock, 'now', lambda: frozen)
    client.application.config['WMS_DEFAULT_TIMEZONE'] = 'Asia/Tokyo'
    with client.application.app_context():
        # Clear only timestamps in this isolated fixture so the boundary is unambiguous.
        for model in (DN, ASN):
            for row in model.query.all():
                row.created_at = frozen-timedelta(days=10)
                if model is DN:
                    row.delivered_at = None
        dn = DN.query.first()
        dn.created_at = datetime(2026, 10, 30, 15, tzinfo=timezone.utc)
        dn.delivered_at = datetime(2026, 10, 31, 0, tzinfo=timezone.utc)
        asn = ASN.query.first()
        asn.created_at = datetime(2026, 10, 30, 14, 59, tzinfo=timezone.utc)
        db.session.commit()
    data = _ok(client.get('/company/platform-overview', headers=_h(access_token)))
    assert data['timezone'] == 'Asia/Tokyo'
    assert data['day'] == '2026-10-31'
    assert data['documents'] == dict(dn=1, asn=0, shipped=1, total=1)


def test_tenant_list_filters_counts_and_month_boundary(client, access_token, access_company_admin_token, monkeypatch):
    frozen = datetime(2026, 10, 31, 1, tzinfo=timezone.utc)
    monkeypatch.setattr(clock, 'now', lambda: frozen)
    client.application.config['WMS_DEFAULT_TIMEZONE'] = 'Asia/Tokyo'
    endpoint = '/company/platform-tenants'
    assert client.get(endpoint, headers=_h(access_company_admin_token)).status_code == 403
    with client.application.app_context():
        company = get_company()
        company.expired_at = frozen + timedelta(days=30)
        company.email = 'search-month@example.test'
        cid = company.id
        for model in (DN, ASN):
            for row in model.query.all():
                row.created_at = frozen-timedelta(days=60)
        dn = DN.query.filter_by(warehouse_id=get_warehouse().id).first()
        dn.created_at = datetime(2026, 9, 30, 15, tzinfo=timezone.utc)
        asn = ASN.query.filter_by(warehouse_id=get_warehouse().id).first()
        asn.created_at = datetime(2026, 10, 31, 15, tzinfo=timezone.utc)
        db.session.add_all([
            Company(name='Status expired', expired_at=frozen, created_by=get_admin_user().id),
            Company(name='Status disabled', is_active=False, expired_at=frozen, created_by=get_admin_user().id),
        ])
        db.session.commit()
    data = _ok(client.get(endpoint+'?keyword=search-month&status=expiring', headers=_h(access_token)))
    assert data['total'] == 1 and data['items'][0]['id'] == cid
    assert data['items'][0]['document_count'] == 1
    assert data['items'][0]['warehouse_count'] >= 1 and data['items'][0]['user_count'] == 3
    assert data['month'] == '2026-10' and data['timezone'] == 'Asia/Tokyo'
    summary = _ok(client.get(f'/company/{cid}/platform-summary', headers=_h(access_token)))
    assert summary['month'] == '2026-10' and summary['timezone'] == 'Asia/Tokyo'
    assert summary['documents'] == dict(dn=1, asn=0)
    data = _ok(client.get(endpoint+'?keyword=Status', headers=_h(access_token)))
    assert data['counts'] == dict(all=2, active=1, inactive=1, expired=1, expiring=0)
    assert len(_ok(client.get(endpoint+'?per_page=1', headers=_h(access_token)))['items']) == 1
    assert _ok(client.get(endpoint+'?keyword=%25', headers=_h(access_token)))['total'] == 0
    for query in ('status=bad', 'page=0', 'per_page=101'):
        assert client.get(endpoint+'?'+query, headers=_h(access_token)).status_code == 400
