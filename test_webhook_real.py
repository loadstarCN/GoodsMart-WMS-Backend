"""Webhook 真实业务流测试

通过实际调用 ASN 接收/完成 和 DN 流程来触发 webhook，验证端到端推送。
"""
import hashlib
import hmac
import json
import secrets
import threading
import time
from http.server import HTTPServer, BaseHTTPRequestHandler

from app import create_app
from extensions import db
from system.third_party.models import APIKey
from system.webhook.models import WebhookEvent
from system.webhook.services import push_pending_events
from warehouse.asn.models import ASN
from warehouse.asn.services import ASNService
from warehouse.dn.models import DN
from warehouse.dn.services import DNService

# ============================================================
# 1. 本地 Webhook 接收服务器
# ============================================================
received_events = []

class WebhookReceiver(BaseHTTPRequestHandler):
    def do_POST(self):
        content_length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(content_length)
        event = {
            'event_type': self.headers.get('X-Webhook-Event'),
            'signature': self.headers.get('X-Webhook-Signature'),
            'body': json.loads(body.decode('utf-8')),
            'raw_body': body,
        }
        received_events.append(event)
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(b'{"ok": true}')

    def log_message(self, format, *args):
        pass


def start_receiver(port=19877):
    server = HTTPServer(('127.0.0.1', port), WebhookReceiver)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def verify_signature(raw_body, secret, signature_header):
    if not signature_header:
        return False
    expected = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
    actual = signature_header.replace('sha256=', '')
    return hmac.compare_digest(expected, actual)


# ============================================================
# 2. 主测试
# ============================================================
def main():
    RECEIVER_PORT = 19877
    WEBHOOK_URL = f'http://127.0.0.1:{RECEIVER_PORT}/webhook'
    WEBHOOK_SECRET = secrets.token_hex(16)

    print("=" * 60)
    print("Webhook Real Business Flow Test")
    print("=" * 60)

    server = start_receiver(RECEIVER_PORT)
    print(f"\n[1] Receiver started at {WEBHOOK_URL}")

    app = create_app()

    with app.app_context():
        # 配置 APIKey webhook
        print("\n[2] Configuring APIKey webhook...")
        api_key = APIKey.query.filter_by(system_name='goodsmart').first()
        if not api_key:
            print("    ERROR: No 'goodsmart' APIKey found")
            return

        old_url = api_key.webhook_url
        old_secret = api_key.webhook_secret
        api_key.webhook_url = WEBHOOK_URL
        api_key.webhook_secret = WEBHOOK_SECRET
        db.session.commit()
        print(f"    APIKey ID={api_key.id}, webhook configured")

        results = []

        # ============ ASN Test ============
        print("\n[3] ASN receive test...")
        asn = ASN.query.filter_by(status='pending').first()
        if asn:
            asn_id = asn.id
            print(f"    Using ASN ID={asn_id}")
            try:
                ASNService.receive_asn(asn_id)
                print(f"    ASN {asn_id} -> received")

                # push webhook
                sent, failed = push_pending_events()
                print(f"    Webhook push: {sent} sent, {failed} failed")
                time.sleep(0.5)

                # check
                evt = next((e for e in received_events if e['event_type'] == 'asn.received'), None)
                if evt:
                    sig_ok = verify_signature(evt['raw_body'], WEBHOOK_SECRET, evt['signature'])
                    print(f"    asn.received payload: {json.dumps(evt['body'], ensure_ascii=False)}")
                    print(f"    Signature verify: {'PASS' if sig_ok else 'FAIL'}")
                    results.append(('asn.received', True, sig_ok))
                else:
                    print("    ERROR: asn.received event not received")
                    results.append(('asn.received', False, False))

                # complete ASN
                print("\n[4] ASN complete test...")
                # need to update actual_quantity for details first
                for detail in asn.details:
                    detail.actual_quantity = detail.quantity
                    detail.sorted_quantity = detail.quantity
                    detail.damage_quantity = 0
                db.session.commit()

                ASNService.complete_asn(asn_id)
                print(f"    ASN {asn_id} -> completed")

                sent, failed = push_pending_events()
                print(f"    Webhook push: {sent} sent, {failed} failed")
                time.sleep(0.5)

                evt = next((e for e in received_events if e['event_type'] == 'asn.completed'), None)
                if evt:
                    sig_ok = verify_signature(evt['raw_body'], WEBHOOK_SECRET, evt['signature'])
                    print(f"    asn.completed payload: {json.dumps(evt['body'], ensure_ascii=False)}")
                    print(f"    Signature verify: {'PASS' if sig_ok else 'FAIL'}")
                    results.append(('asn.completed', True, sig_ok))
                else:
                    print("    ERROR: asn.completed event not received")
                    results.append(('asn.completed', False, False))

            except Exception as e:
                print(f"    ASN Error: {e}")
                db.session.rollback()
                results.append(('asn', False, False))
        else:
            print("    No pending ASN found, skipping")

        # ============ DN Test ============
        print("\n[5] DN in_progress test...")
        dn = DN.query.filter_by(status='pending').first()
        if dn:
            dn_id = dn.id
            print(f"    Using DN ID={dn_id}")
            try:
                DNService.in_progress_dn(dn_id)
                print(f"    DN {dn_id} -> in_progress")

                sent, failed = push_pending_events()
                print(f"    Webhook push: {sent} sent, {failed} failed")
                time.sleep(0.5)

                evt = next((e for e in received_events if e['event_type'] == 'dn.in_progress'), None)
                if evt:
                    sig_ok = verify_signature(evt['raw_body'], WEBHOOK_SECRET, evt['signature'])
                    print(f"    dn.in_progress payload: {json.dumps(evt['body'], ensure_ascii=False)}")
                    print(f"    Signature verify: {'PASS' if sig_ok else 'FAIL'}")
                    results.append(('dn.in_progress', True, sig_ok))
                else:
                    print("    ERROR: dn.in_progress event not received")
                    results.append(('dn.in_progress', False, False))

            except Exception as e:
                print(f"    DN Error: {e}")
                db.session.rollback()
                results.append(('dn.in_progress', False, False))
        else:
            print("    No pending DN found, skipping")

        # DN delivered test
        print("\n[6] DN delivered test...")
        dn_packed = DN.query.filter_by(status='packed').first()
        if dn_packed:
            dn_id = dn_packed.id
            print(f"    Using DN ID={dn_id} (packed)")
            try:
                # set delivered_quantity
                for detail in dn_packed.details:
                    detail.delivered_quantity = detail.packed_quantity or detail.picked_quantity or detail.quantity
                db.session.commit()

                DNService.delivery_dn(dn_id)
                print(f"    DN {dn_id} -> delivered")

                sent, failed = push_pending_events()
                print(f"    Webhook push: {sent} sent, {failed} failed")
                time.sleep(0.5)

                evt = next((e for e in received_events if e['event_type'] == 'dn.delivered'), None)
                if evt:
                    sig_ok = verify_signature(evt['raw_body'], WEBHOOK_SECRET, evt['signature'])
                    print(f"    dn.delivered payload: {json.dumps(evt['body'], ensure_ascii=False)}")
                    print(f"    Signature verify: {'PASS' if sig_ok else 'FAIL'}")
                    results.append(('dn.delivered', True, sig_ok))
                else:
                    print("    ERROR: dn.delivered event not received")
                    results.append(('dn.delivered', False, False))

                # DN completed test
                print("\n[7] DN completed test...")
                DNService.complete_dn(dn_id)
                print(f"    DN {dn_id} -> completed")

                sent, failed = push_pending_events()
                print(f"    Webhook push: {sent} sent, {failed} failed")
                time.sleep(0.5)

                evt = next((e for e in received_events if e['event_type'] == 'dn.completed'), None)
                if evt:
                    sig_ok = verify_signature(evt['raw_body'], WEBHOOK_SECRET, evt['signature'])
                    print(f"    dn.completed payload: {json.dumps(evt['body'], ensure_ascii=False)}")
                    print(f"    Signature verify: {'PASS' if sig_ok else 'FAIL'}")
                    results.append(('dn.completed', True, sig_ok))
                else:
                    print("    ERROR: dn.completed event not received")
                    results.append(('dn.completed', False, False))

            except Exception as e:
                print(f"    DN Error: {e}")
                db.session.rollback()
                results.append(('dn.delivered', False, False))
        else:
            print("    No packed DN found, skipping")

        # Restore APIKey
        print("\n[8] Restoring APIKey webhook config...")
        api_key.webhook_url = old_url
        api_key.webhook_secret = old_secret
        db.session.commit()
        print("    Restored")

        # Summary
        print("\n" + "=" * 60)
        print("Results:")
        for event_type, received, sig_ok in results:
            status = "PASS" if (received and sig_ok) else "FAIL"
            print(f"  {event_type}: {status}")
        print("=" * 60)

    server.shutdown()


if __name__ == '__main__':
    main()
