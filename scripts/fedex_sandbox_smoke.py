"""FedEx 测试环境（sandbox）冒烟：用虚构数据建一票测试运单 → 存面单 PDF → 取消。

不进 pytest、不连数据库，直接用 WMS 的请求组装代码（warehouse/dn/fedex_shipment.py）与客户端
（warehouse/dn/fedex_client.py）。面单 PDF 可作为向 FedEx 申请面单认证（label certification）的样张。

只允许 FEDEX_API_BASE 含 "sandbox"，否则拒绝执行。凭证从环境变量读（FEDEX_API_KEY / FEDEX_SECRET_KEY /
FEDEX_ACCOUNT_NUMBER / FEDEX_API_BASE 等），也可用 --env-file 只读入某个 .env 里未注释的 FEDEX_* 项。
不打印任何凭证。

用法：
    python scripts/fedex_sandbox_smoke.py [--env-file PATH] [--to US|DE|HK] [--packages 2] [--etd]
                                          [--out ./fedex-sandbox-labels] [--keep]
"""
import argparse
import os
import sys
from datetime import datetime
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

FEDEX_KEYS = (
    'FEDEX_API_BASE', 'FEDEX_API_KEY', 'FEDEX_SECRET_KEY', 'FEDEX_ACCOUNT_NUMBER', 'FEDEX_SERVICE_TYPE',
    'FEDEX_PICKUP_TYPE', 'FEDEX_LABEL_IMAGE_TYPE', 'FEDEX_LABEL_STOCK_TYPE', 'FEDEX_DUTIES_PAYMENT_TYPE',
    'FEDEX_DOCUMENT_API_BASE', 'FEDEX_CONNECT_TIMEOUT_SECONDS', 'FEDEX_TIMEOUT_SECONDS',
)
SECRET_KEYS = ('FEDEX_API_KEY', 'FEDEX_SECRET_KEY', 'FEDEX_ACCOUNT_NUMBER')

# 虚构收件人（按目的国）
RECIPIENTS = {
    'US': {'company_name': 'Sandbox Recipient Inc.', 'person_name': 'Sandbox Recipient', 'phone': '9015551234',
           'address': {'streetLines': ['123 Example Street'], 'city': 'Memphis', 'stateOrProvinceCode': 'TN',
                       'postalCode': '38117', 'countryCode': 'US'},
           'tins': [{'number': '123456789', 'tinType': 'BUSINESS_NATIONAL'}]},
    'DE': {'company_name': 'Sandbox Empfaenger GmbH', 'person_name': 'Max Mustermann', 'phone': '4930000000',
           'address': {'streetLines': ['Hauptstrasse 1'], 'city': 'Berlin', 'postalCode': '10115',
                       'countryCode': 'DE'},
           'tins': [{'number': 'DE000000000000000', 'tinType': 'BUSINESS_UNION'}]},
    'HK': {'company_name': 'Sandbox Recipient Ltd.', 'person_name': 'Chan Tai Man', 'phone': '85212345678',
           'address': {'streetLines': ['Flat A, 1/F, 1 Example Road'], 'city': 'Kowloon', 'postalCode': '',
                       'countryCode': 'HK'},
           'tins': []},
}
SHIPPER = {
    'company_name': 'WMS Sandbox Shipper', 'person_name': 'Sandbox Shipper', 'phone': '0312345678',
    'email': None,
    'address': {'streetLines': ['1-1-1 Example, Chiyoda-ku'], 'city': 'Tokyo', 'postalCode': '100-0001',
                'countryCode': 'JP'},
    'tins': [],
}
COMMODITIES = [
    {'description': 'Plastic figure (sandbox test)', 'origin_country': 'CN', 'hs_code': '950300',
     'quantity': 3, 'quantity_unit': 'PCS', 'unit_value': 1200, 'amount': 3600,
     'weight_kg': Decimal('0.9'), 'part_number': 'SANDBOX-001'},
    {'description': 'Acrylic stand (sandbox test)', 'origin_country': 'JP', 'hs_code': '392640',
     'quantity': 2, 'quantity_unit': 'PCS', 'unit_value': 2100, 'amount': 4200,
     'weight_kg': Decimal('0.4'), 'part_number': 'SANDBOX-002'},
]


def _load_env_file(path):
    from dotenv import dotenv_values
    values = dotenv_values(path)     # 注释行不读
    loaded = []
    for key in FEDEX_KEYS:
        if values.get(key):
            os.environ[key] = values[key]
            loaded.append(key)
    return loaded


def _dummy_invoice_pdf(invoice_number):
    import io
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas
    out = io.BytesIO()
    pdf = canvas.Canvas(out, pagesize=A4)
    pdf.setFont('Helvetica-Bold', 16)
    pdf.drawString(60, 780, 'COMMERCIAL INVOICE (SANDBOX TEST - NOT A REAL SHIPMENT)')
    pdf.setFont('Helvetica', 11)
    pdf.drawString(60, 750, f'Invoice No.: {invoice_number}')
    y = 720
    for line in COMMODITIES:
        pdf.drawString(60, y, f"{line['description']}  HS {line['hs_code']}  {line['quantity']} PCS  "
                              f"JPY {line['amount']:,}")
        y -= 18
    pdf.showPage()
    pdf.save()
    return out.getvalue()


def main():
    parser = argparse.ArgumentParser(description='FedEx sandbox smoke test (create + cancel a test shipment)')
    parser.add_argument('--env-file', help='read the uncommented FEDEX_* entries of this .env file')
    parser.add_argument('--to', default='US', choices=sorted(RECIPIENTS), help='destination country')
    parser.add_argument('--packages', type=int, default=2, help='number of packages (1-30)')
    parser.add_argument('--etd', action='store_true', help='upload a dummy commercial invoice (ETD) first')
    parser.add_argument('--out', default='fedex-sandbox-labels', help='directory for the label PDFs')
    parser.add_argument('--keep', action='store_true', help='do not cancel the test shipment')
    args = parser.parse_args()

    if args.env_file:
        loaded = _load_env_file(args.env_file)
        print(f"Loaded from env file: {', '.join(loaded) or '(nothing)'}")

    base = os.environ.get('FEDEX_API_BASE', 'https://apis-sandbox.fedex.com')
    if 'sandbox' not in base.lower():
        print(f"REFUSED: FEDEX_API_BASE={base} is not the FedEx sandbox. This script only runs against sandbox.")
        return 2
    missing = [k for k in SECRET_KEYS if not os.environ.get(k)]
    if missing:
        print(f"Missing credentials: {', '.join(missing)}")
        return 2
    if not 1 <= args.packages <= 30:
        print("--packages must be 1-30")
        return 2

    from flask import Flask
    from warehouse.dn import fedex_client
    from warehouse.dn.fedex_client import FedexError
    from warehouse.dn.fedex_shipment import build_ship_request, merge_labels, parse_ship_response, split_evenly

    app = Flask('fedex_sandbox_smoke')
    app.config.update({k: os.environ[k] for k in FEDEX_KEYS if os.environ.get(k)})
    app.config.setdefault('FEDEX_API_BASE', base)
    settings = {
        'service_type': app.config.get('FEDEX_SERVICE_TYPE', 'INTERNATIONAL_ECONOMY'),
        'pickup_type': app.config.get('FEDEX_PICKUP_TYPE', 'USE_SCHEDULED_PICKUP'),
        'label_image_type': app.config.get('FEDEX_LABEL_IMAGE_TYPE', 'PDF').upper(),
        'label_stock_type': app.config.get('FEDEX_LABEL_STOCK_TYPE', 'PAPER_4X6'),
        'duties_payment_type': app.config.get('FEDEX_DUTIES_PAYMENT_TYPE', 'RECIPIENT').upper(),
    }
    print(f"API base: {base}  (document API: ", end='')

    with app.app_context():
        print(f"{fedex_client.document_api_base()})")
        print(f"Service {settings['service_type']} / pickup {settings['pickup_type']} / label "
              f"{settings['label_image_type']} {settings['label_stock_type']} / duties {settings['duties_payment_type']}")
        invoice_number = f"SANDBOX-{datetime.now():%Y%m%d%H%M%S}"
        declared_total = 5000
        packages = [{'gross_weight_kg': Decimal('2.5'), 'length_mm': 300, 'width_mm': 200, 'height_mm': 150,
                     'declared_value': value} for value in split_evenly(declared_total, args.packages)]

        etd_document_id = None
        if args.etd:
            try:
                uploaded = fedex_client.upload_etd_document(
                    _dummy_invoice_pdf(invoice_number), f'CI_{invoice_number}.pdf', 'JP', args.to)
            except FedexError as exc:
                _report_error('Trade Documents Upload', exc)
                return 3
            meta = (uploaded.get('output') or {}).get('meta') or {}
            etd_document_id = meta.get('docId')
            print(f"ETD upload OK: docId={etd_document_id} documentType={meta.get('documentType')}")

        request = build_ship_request(
            account_number=app.config['FEDEX_ACCOUNT_NUMBER'], settings=settings,
            ship_date=datetime.now().date(), shipper=SHIPPER, recipient=RECIPIENTS[args.to],
            packages=packages, commodities=COMMODITIES,
            invoice={'invoice_number': invoice_number, 'currency': 'JPY', 'incoterm': 'DAP',
                     'export_reason': 'SALE', 'freight': 3000, 'insurance': 0,
                     'goods_value': sum(c['amount'] for c in COMMODITIES), 'declared_value': declared_total},
            reference=invoice_number, etd_document_id=etd_document_id,
        )
        try:
            body = fedex_client.create_shipment(request)
        except FedexError as exc:
            _report_error('Ship API create', exc)
            return 3

        result = parse_ship_response(body)
        print(f"Created: master tracking {result['tracking_number']}  service {result['service_type']}  "
              f"ship date {result['ship_date']}")
        print(f"  package tracking numbers: {', '.join(result['package_tracking_numbers']) or '-'}")
        print(f"  net charge: {result['net_charge']} {result['currency'] or ''}  transactionId: {result['transaction_id']}")
        for alert in result['alerts']:
            print(f"  alert {alert['alert_type']} {alert['code']}: {alert['message']}")

        out_dir = Path(args.out)
        out_dir.mkdir(parents=True, exist_ok=True)
        extension = 'pdf' if settings['label_image_type'] == 'PDF' else settings['label_image_type'].lower()
        for label in result['labels']:
            path = out_dir / f"label_{result['tracking_number']}_{label['sequence']}.{extension}"
            path.write_bytes(label['content'])
            print(f"  saved {path} ({len(label['content'])} bytes)")
        if settings['label_image_type'] in ('PDF', 'PNG') and result['labels']:
            merged = out_dir / f"label_{result['tracking_number']}_all.pdf"
            merged.write_bytes(merge_labels(result['labels'], settings['label_image_type']))
            print(f"  saved {merged} (merged)")

        if args.keep:
            print("--keep: the test shipment was NOT cancelled")
            return 0
        try:
            cancelled = fedex_client.cancel_shipment(result['tracking_number'], 'JP')
        except FedexError as exc:
            _report_error('Ship API cancel', exc)
            return 4
        output = cancelled.get('output') or {}
        print(f"Cancelled: cancelledShipment={output.get('cancelledShipment')} "
              f"message={output.get('successMessage') or output.get('message')} "
              f"transactionId={cancelled.get('transactionId')}")
        for alert in output.get('alerts') or []:
            print(f"  alert {alert.get('alertType')} {alert.get('code')}: {alert.get('message')}")
        return 0 if output.get('cancelledShipment') is not False else 4


def _report_error(what, exc):
    print(f"FAILED: {what}: HTTP {exc.status_code}  transactionId={exc.transaction_id}")
    for error in exc.errors:
        print(f"  {error.get('code')}: {error.get('message')}")
    if not exc.errors:
        print(f"  {exc.message}")
    if exc.permission_denied:
        print("  -> 权限类错误：FedEx 测试项目可能还没开通这个 API（Ship API / Trade Documents Upload），"
              "或凭证无权限。请在 developer.fedex.com 的项目里添加对应 API 后重试。")


if __name__ == '__main__':
    sys.exit(main())
