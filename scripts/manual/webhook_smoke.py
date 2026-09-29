"""Webhook 端到端测试脚本

启动一个本地 HTTP 接收服务器，创建测试 APIKey，插入模拟 webhook 事件，推送并验证。
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
        print(f"  [Receiver] 收到事件: {event['event_type']}")
        print(f"  [Receiver] Payload: {json.dumps(event['body'], ensure_ascii=False, indent=2)}")
        print(f"  [Receiver] Signature: {event['signature']}")
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(b'{"ok": true}')

    def log_message(self, format, *args):
        pass  # 静默日志


def start_receiver(port=19876):
    server = HTTPServer(('127.0.0.1', port), WebhookReceiver)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


# ============================================================
# 2. 验证签名
# ============================================================
def verify_signature(raw_body, secret, signature_header):
    """验证 HMAC-SHA256 签名"""
    if not signature_header:
        return False
    expected = hmac.new(
        secret.encode('utf-8'),
        raw_body,
        hashlib.sha256,
    ).hexdigest()
    actual = signature_header.replace('sha256=', '')
    match = hmac.compare_digest(expected, actual)
    return match


# ============================================================
# 3. 主测试流程
# ============================================================
def main():
    RECEIVER_PORT = 19876
    WEBHOOK_URL = f'http://127.0.0.1:{RECEIVER_PORT}/webhook'
    WEBHOOK_SECRET = secrets.token_hex(16)

    print("=" * 60)
    print("Webhook 端到端测试")
    print("=" * 60)

    # 启动接收服务器
    print("\n[1] 启动本地 Webhook 接收服务器...")
    server = start_receiver(RECEIVER_PORT)
    print(f"    监听地址: {WEBHOOK_URL}")

    app = create_app()

    with app.app_context():
        # 创建测试 APIKey
        print("\n[2] 创建测试 APIKey...")
        test_key = f"test_webhook_{secrets.token_hex(8)}"
        api_key = APIKey(
            key=test_key,
            system_name='webhook_test',
            permissions=['all_access'],
        )
        api_key.webhook_url = WEBHOOK_URL
        api_key.webhook_secret = WEBHOOK_SECRET
        db.session.add(api_key)
        db.session.commit()
        api_key_id = api_key.id
        print(f"    APIKey ID: {api_key_id}")
        print(f"    Webhook Secret: {WEBHOOK_SECRET}")

        # 插入模拟事件
        print("\n[3] 插入模拟 Webhook 事件...")
        test_events = [
            ('asn.received', {
                'asn_id': 1001,
                'status': 'received',
                'order_number': 'ASN-TEST-001',
                'details': [
                    {'goods_code': 'SKU-A001', 'quantity': 100},
                    {'goods_code': 'SKU-B002', 'quantity': 50},
                ],
            }),
            ('asn.completed', {
                'asn_id': 1001,
                'status': 'completed',
                'order_number': 'ASN-TEST-001',
                'details': [
                    {'goods_code': 'SKU-A001', 'actual_quantity': 98, 'quantity': 100, 'sorted_quantity': 98, 'damage_quantity': 2},
                    {'goods_code': 'SKU-B002', 'actual_quantity': 50, 'quantity': 50, 'sorted_quantity': 50, 'damage_quantity': 0},
                ],
            }),
            ('dn.in_progress', {
                'dn_id': 2001,
                'status': 'in_progress',
                'order_number': 'DN-TEST-001',
            }),
            ('dn.delivered', {
                'dn_id': 2001,
                'status': 'delivered',
                'order_number': 'DN-TEST-001',
                'tracking_number': 'SF1234567890',
            }),
            ('dn.completed', {
                'dn_id': 2001,
                'status': 'completed',
                'order_number': 'DN-TEST-001',
            }),
        ]

        for event_type, payload in test_events:
            event = WebhookEvent(
                api_key_id=api_key_id,
                event_type=event_type,
                payload=payload,
                status='pending',
            )
            db.session.add(event)
            print(f"    + {event_type}")

        db.session.commit()

        # 推送
        print("\n[4] 执行 Webhook 推送...")
        sent, failed = push_pending_events()
        print(f"    结果: {sent} 成功, {failed} 失败")

        # 等待接收完成
        time.sleep(1)

        # 验证
        print("\n[5] 验证结果...")
        print(f"    接收到 {len(received_events)} 个事件")

        all_pass = True
        for i, evt in enumerate(received_events):
            # 验证签名
            sig_ok = verify_signature(evt['raw_body'], WEBHOOK_SECRET, evt['signature'])
            status = "PASS" if sig_ok else "FAIL"
            if not sig_ok:
                all_pass = False
            print(f"    [{i+1}] {evt['event_type']} - 签名验证: {status}")

        # 检查数据库状态
        print("\n[6] 检查数据库事件状态...")
        db_events = WebhookEvent.query.filter_by(api_key_id=api_key_id).all()
        for evt in db_events:
            status_ok = evt.status == 'sent'
            if not status_ok:
                all_pass = False
            print(f"    {evt.event_type}: status={evt.status}, attempts={evt.attempts}, sent_at={evt.sent_at}")

        # 清理
        print("\n[7] 清理测试数据...")
        WebhookEvent.query.filter_by(api_key_id=api_key_id).delete()
        APIKey.query.filter_by(id=api_key_id).delete()
        db.session.commit()
        print("    已清理")

        # 总结
        print("\n" + "=" * 60)
        if all_pass and len(received_events) == 5:
            print("测试结果: ALL PASSED ✓")
        else:
            print(f"测试结果: FAILED (收到 {len(received_events)}/5 事件, 验证{'通过' if all_pass else '失败'})")
        print("=" * 60)

    server.shutdown()


if __name__ == '__main__':
    main()
