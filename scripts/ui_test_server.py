"""Local UI acceptance server: in-memory test data only, no production database or Redis.

Run: .venv/Scripts/python.exe scripts/ui_test_server.py
Login: company_admin / password. All changes disappear when the process exits.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tests.conftest  # noqa: F401,E402 -- install isolated fake Redis
from tests.helpers import setup_app, init_test_data  # noqa: E402
from werkzeug.serving import run_simple  # noqa: E402


def main():
    app = setup_app()
    from system.webhook.schemas import api_ns as webhook_ns
    from system.webhook import views as webhook_views  # noqa: F401
    from system.settings.schemas import api_ns as settings_ns
    from system.settings import views as settings_views  # noqa: F401
    api = app.extensions['tests.restx_api']
    api.add_namespace(webhook_ns, path='/webhook')
    api.add_namespace(settings_ns, path='/settings')
    init_test_data(app)
    # Isolated preview fixtures also exercise the nightly analytics UI.
    with app.app_context():
        from extensions.db import db
        from system.user.models import Permission
        from system.user.builtin import BUILTIN_PERMISSIONS
        for name, description in BUILTIN_PERMISSIONS:
            if not Permission.query.filter_by(name=name).first():
                db.session.add(Permission(name=name, description=description))
        db.session.flush()
        from warehouse.warehouse.models import Warehouse
        from warehouse.stock.metrics import generate_metrics
        from warehouse.common import clock
        for warehouse in Warehouse.query.all():
            generate_metrics(warehouse.id, clock.local_today(warehouse))
        db.session.commit()
    # Test helpers register namespaces without the production blueprint prefixes.
    import os
    if os.environ.get('WMS_UI_BRAND_FIXTURE') == '1':
        from extensions.oss import oss
        from flask import Response
        from uuid import uuid4
        assets = {}
        def local_upload(file, path, random_name=True):
            key = uuid4().hex + '.png'
            assets[key] = file.read()
            return 'http://127.0.0.1:5099/ui-assets/' + key
        oss.upload_file = local_upload
        @app.route('/ui-assets/<key>')
        def local_asset(key):
            return Response(assets.get(key, b''), status=200 if key in assets else 404, mimetype='image/png')
    if os.environ.get('WMS_UI_LOT_FIXTURE') == '1':
        from tests.helpers import create_access_token, get_admin_user
        from tests.test_inventory_guards import _world, _h, _ok
        from tests.test_lot_operations import receive_lots, pick_lots
        with app.app_context():
            token = create_access_token(identity=get_admin_user())
        client = app.test_client()
        world = _world(client)
        first, second = receive_lots(client, token, world)
        dn_id, task_id = pick_lots(client, token, world, [(first, 3), (second, 3)])
        _ok(client.put(f'/packing/{task_id}/process/', headers=_h(token)))
        _ok(client.post(f'/packing/{task_id}/batches/', json={'details': [{'goods_id': world['g1'], 'packed_quantity': 3}]}, headers=_h(token)))
        print(f'Local mixed-lot fixture: DN {dn_id}, packing {task_id}', flush=True)
    if os.environ.get('WMS_UI_FREIGHT_FIXTURE') == '1':
        from tests.helpers import create_access_token, get_admin_user
        from tests.test_freight_invoices import world, upload, csv
        with app.app_context():
            token = create_access_token(identity=get_admin_user())
        client = app.test_client()
        freight_world = world(client)
        response = upload(client, token, freight_world, 'import', csv(freight_world))
        assert response.status_code == 201, response.status_code
        print('Local freight fixture ready', flush=True)
    if os.environ.get('WMS_UI_ROUTE_FIXTURE') == '1':
        assert os.environ.get('WMS_UI_LOT_FIXTURE') == '1'
        from tests.test_inventory_guards import _h, _ok
        from tests.helpers import warehouse_today
        headers = _h(token)
        _ok(client.post('/transfer/', headers=headers, json=dict(goods_id=3, lot_id=1,
            from_location_id=4, to_location_id=5, quantity=1)))
        def shape(ident, kind, x, y, width, height, **extra):
            return dict(id=ident,type=kind,name=ident,x=x,y=y,width=width,height=height,rotation=0,**extra)
        layout=dict(floors=[dict(id='1F',name='1F',width=24,height=20,objects=[
            shape('Packing station','station',1,1,2,2),shape('Wall','wall',8,3,1,14),
            shape('Rack A','rack',13,10,2,3,location_ids=[4],pick_face='front'),
            shape('Lift 1','connector',20,3,2,2,target_id='Lift 2',travel_seconds=45)]),
            dict(id='2F',name='2F',width=24,height=20,objects=[
            shape('Lift 2','connector',20,3,2,2,target_id='Lift 1',travel_seconds=45),
            shape('Rack B','rack',5,10,2,3,location_ids=[5],pick_face='front')])])
        saved=_ok(client.put('/warehouse/1/layout',headers=headers,json=dict(revision=0,layout=layout)))
        _ok(client.post('/warehouse/1/layout/publish',headers=headers,json=dict(revision=saved['revision'])))
        with app.app_context():
            today=warehouse_today().isoformat()
        dn=_ok(client.post('/dn/',headers=headers,json=dict(warehouse_id=1,recipient_id=1,
            shipping_address='Test',expected_shipping_date=today,details=[dict(goods_id=3,quantity=4)])))
        _ok(client.put(f'/dn/{dn["id"]}/progress/',headers=headers))
        print('Local two-floor route fixture ready',flush=True)
    if os.environ.get('WMS_UI_PENDINGAGE_FIXTURE') == '1':
        from tests.helpers import add_stock
        with app.app_context():
            add_stock(3, 1, pending=3)
        print('Local unknown-age pending fixture ready', flush=True)
    if os.environ.get('WMS_UI_WEBHOOK_FIXTURE') == '1':
        from system.third_party.models import APIKey
        from warehouse.goods.models import Goods
        from warehouse.goods.services import GoodsService
        from system.webhook.models import WebhookEvent
        from hashlib import sha256
        with app.app_context():
            goods = Goods.query.filter_by(company_id=1).first()
            key = APIKey(key=sha256(b'local-webhook-fixture-only').hexdigest(),
                system_name='Local acceptance receiver', permissions=['goods_read'])
            key.company_id = goods.company_id
            key.webhook_url = 'http://127.0.0.1:9/local-only'
            key.webhook_subscriptions = ['goods.spec_updated']
            db.session.add(key)
            db.session.commit()
            original = goods.weight
            GoodsService.update_goods(goods.id, {'weight': 1234}, spec_source='manual')
            GoodsService.update_goods(goods.id, {'weight': original}, spec_source='manual')
            # Never deliver fixture data. A real retry exercises the missing-URL failure path.
            key.webhook_url = None
            db.session.commit()
            assert WebhookEvent.query.filter_by(api_key_id=key.id).count() > 0
        print('Local business webhook fixture ready (delivery disabled)', flush=True)
    if os.environ.get('WMS_UI_CUSTOMS_FIXTURE') == '1':
        from tests.helpers import create_access_token, get_admin_user
        from tests.test_customs import _export_dn_ready
        with app.app_context():
            token = create_access_token(identity=get_admin_user())
        customs_dn, customs_delivery = _export_dn_ready(app.test_client(), token)
        print(f'Local packed customs fixture: DN {customs_dn}, delivery {customs_delivery}', flush=True)
    def mounted(environ, start_response):
        path = environ.get('PATH_INFO', '')
        for prefix in ('/warehouse', '/system'):
            if path.startswith(prefix + '/'):
                environ['PATH_INFO'] = path[len(prefix):]
                break
        if environ['PATH_INFO'].startswith('/limiter/'):
            environ['PATH_INFO'] = '/ip-lists/' + environ['PATH_INFO'][len('/limiter/'):]
        if environ['PATH_INFO'].startswith('/third-party/'):
            environ['PATH_INFO'] = '/api-keys/' + environ['PATH_INFO'][len('/third-party/'):]
        return app(environ, start_response)
    run_simple('127.0.0.1', 5099, mounted, threaded=False, use_reloader=False)


if __name__ == '__main__':
    main()
