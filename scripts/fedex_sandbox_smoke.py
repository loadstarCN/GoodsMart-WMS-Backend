"""FedEx 测试环境（sandbox）冒烟：按面单打印方式（A4 / THERMAL）× 箱数各建一票测试运单 → 存面单 → 取消。

不进 pytest、不连数据库，直接用 WMS 的请求组装与面单存档代码（warehouse/dn/fedex_shipment.py）和客户端
（warehouse/dn/fedex_client.py）。面单文件按「服务_箱数_打印方式.扩展名」存（如
INTERNATIONAL_ECONOMY_2pkg_A4.pdf），可作为向 FedEx 申请面单认证（label certification）的样张。

只允许 FEDEX_API_BASE 含 "sandbox"，否则拒绝执行。凭证从环境变量读（FEDEX_API_KEY / FEDEX_SECRET_KEY /
FEDEX_ACCOUNT_NUMBER / FEDEX_API_BASE 以及 FEDEX_* 选项），也可用 --env-file 只读入某个 .env 里未注释的
FEDEX_* 项。不打印任何凭证。

发件人 / 收件人：默认用虚构数据；认证样张请用 --shipper-json 给出贵司出口资料（与 WMS 公司 / 仓库设置相同的
字段），用 --recipient-json 给一个真实格式的海外地址（与报关快照 consignee 相同的字段）：
    shipper.json   {"company_name", "person_name", "phone", "email", "address_en", "postal_code",
                    "country_code", "tax_id"}
    recipient.json {"company", "name", "phone", "address_line1", "address_line2", "city", "state",
                    "postal_code", "country", "tax_id", "tax_id_type"}

用法：
    python scripts/fedex_sandbox_smoke.py [--env-file PATH] [--label-format A4|THERMAL|both] [--packages 1,2]
        [--to US|DE|HK] [--shipper-json PATH] [--recipient-json PATH] [--etd] [--out DIR] [--keep]
"""
import argparse
import json
import os
import sys
from datetime import datetime
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

FEDEX_KEYS = (
    'FEDEX_API_BASE', 'FEDEX_API_KEY', 'FEDEX_SECRET_KEY', 'FEDEX_ACCOUNT_NUMBER', 'FEDEX_SERVICE_TYPE',
    'FEDEX_PICKUP_TYPE', 'FEDEX_DUTIES_PAYMENT_TYPE', 'FEDEX_DEFAULT_LABEL_FORMAT',
    'FEDEX_LABEL_A4_IMAGE_TYPE', 'FEDEX_LABEL_A4_STOCK_TYPE', 'FEDEX_LABEL_THERMAL_IMAGE_TYPE',
    'FEDEX_LABEL_THERMAL_STOCK_TYPE', 'FEDEX_DOCUMENT_API_BASE', 'FEDEX_CONNECT_TIMEOUT_SECONDS',
    'FEDEX_TIMEOUT_SECONDS',
)
SECRET_KEYS = ('FEDEX_API_KEY', 'FEDEX_SECRET_KEY', 'FEDEX_ACCOUNT_NUMBER')

# 虚构收件人（报关快照 consignee 的字段）
RECIPIENTS = {
    'US': {'company': 'Sandbox Recipient Inc.', 'name': 'Sandbox Recipient', 'phone': '9015551234',
           'address_line1': '123 Example Street', 'city': 'Memphis', 'state': 'TN', 'postal_code': '38117',
           'country': 'US', 'tax_id': '123456789', 'tax_id_type': 'EIN'},
    'DE': {'company': 'Sandbox Empfaenger GmbH', 'name': 'Max Mustermann', 'phone': '4930000000',
           'address_line1': 'Hauptstrasse 1', 'city': 'Berlin', 'postal_code': '10115', 'country': 'DE',
           'tax_id': 'DE000000000000000', 'tax_id_type': 'EORI'},
    'HK': {'company': 'Sandbox Recipient Ltd.', 'name': 'Chan Tai Man', 'phone': '85212345678',
           'address_line1': 'Flat A, 1/F, 1 Example Road', 'city': 'Kowloon', 'postal_code': None,
           'country': 'HK'},
}
# 虚构发件人（WMS 公司 / 仓库出口资料的字段）
SHIPPER = {
    'company_name': 'WMS Sandbox Shipper', 'person_name': 'Sandbox Shipper', 'phone': '0312345678',
    'email': None, 'address_en': '1-1-1 Example, Chiyoda-ku, Tokyo', 'postal_code': '100-0001',
    'country_code': 'JP', 'tax_id': None,
}
COMMODITIES = [
    {'description': 'Plastic figure (sandbox test)', 'origin_country': 'CN', 'hs_code': '950300',
     'quantity': 3, 'quantity_unit': 'PCS', 'unit_value': 1200, 'amount': 3600,
     'weight_kg': Decimal('0.9'), 'part_number': 'SANDBOX-001'},
    {'description': 'Acrylic stand (sandbox test)', 'origin_country': 'JP', 'hs_code': '392640',
     'quantity': 2, 'quantity_unit': 'PCS', 'unit_value': 2100, 'amount': 4200,
     'weight_kg': Decimal('0.4'), 'part_number': 'SANDBOX-002'},
]
DECLARED_VALUE = 5000      # ≤ 货值（FedEx 拒绝申告价额高于报关货值）


def _load_env_file(path):
    from dotenv import dotenv_values
    values = dotenv_values(path)     # 注释行不读
    loaded = []
    for key in FEDEX_KEYS:
        if values.get(key):
            os.environ[key] = values[key]
            loaded.append(key)
    return loaded


def _load_json(path):
    with open(path, encoding='utf-8') as f:
        return json.load(f)


def _parties(shipper_raw, recipient_raw):
    """与 WMS 建单相同的规则把发件人 / 收件人整理成 FedEx 格式（地址放不下会抛 AddressError）"""
    from warehouse.common.countries import COUNTRY_NAMES_EN
    from warehouse.dn.fedex_shipment import SHIPPER_TIN_TYPE, consignee_address, split_address_text, tin_type_for

    country = (shipper_raw.get('country_code') or 'JP').upper()
    shipper = {
        'company_name': shipper_raw.get('company_name'),
        'person_name': shipper_raw.get('person_name'),
        'phone': shipper_raw.get('phone'),
        'email': shipper_raw.get('email'),
        'address': split_address_text(shipper_raw.get('address_en'), country, shipper_raw.get('postal_code'),
                                      COUNTRY_NAMES_EN.get(country)),
        'tins': ([{'number': shipper_raw['tax_id'], 'tinType': SHIPPER_TIN_TYPE}]
                 if shipper_raw.get('tax_id') else []),
    }
    recipient_country = (recipient_raw.get('country') or '').upper()
    recipient = {
        'company_name': recipient_raw.get('company'),
        'person_name': recipient_raw.get('name') or recipient_raw.get('company'),
        'phone': recipient_raw.get('phone'),
        'email': None,
        'address': consignee_address(recipient_raw, recipient_country),
        'tins': ([{'number': recipient_raw['tax_id'], 'tinType': tin_type_for(recipient_raw.get('tax_id_type'))}]
                 if recipient_raw.get('tax_id') else []),
    }
    return shipper, recipient


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


def _report_error(what, exc):
    print(f"  FAILED: {what}: HTTP {exc.status_code}  transactionId={exc.transaction_id}")
    for error in exc.errors:
        print(f"    {error.get('code')}: {error.get('message')}")
    if not exc.errors:
        print(f"    {exc.message}")
    if exc.permission_denied:
        print("    -> 权限类错误：FedEx 测试项目可能还没开通这个 API（Ship API / Trade Documents Upload），"
              "或凭证无权限。请在 developer.fedex.com 的项目里添加对应 API 后重试。")


def _run_one(app, settings, label, packages_count, shipper, recipient, args):
    """建一票 → 存面单 → 取消。返回进程退出码（0 = 成功）"""
    from warehouse.dn import fedex_client
    from warehouse.dn.fedex_client import FedexError
    from warehouse.dn.fedex_shipment import build_label_archive, build_ship_request, parse_ship_response, split_evenly

    print(f"\n== {settings['service_type']} / {packages_count} package(s) / {label['label_format']} "
          f"({label['image_type']} {label['stock_type']})")
    invoice_number = f"SANDBOX-{datetime.now():%Y%m%d%H%M%S}-{packages_count}{label['label_format'][0]}"
    packages = [{'gross_weight_kg': Decimal('2.5'), 'length_mm': 300, 'width_mm': 200, 'height_mm': 150,
                 'declared_value': value} for value in split_evenly(DECLARED_VALUE, packages_count)]

    etd_document_id = None
    if args.etd:
        try:
            uploaded = fedex_client.upload_etd_document(
                _dummy_invoice_pdf(invoice_number), f'CI_{invoice_number}.pdf',
                shipper['address']['countryCode'], recipient['address']['countryCode'])
        except FedexError as exc:
            _report_error('Trade Documents Upload', exc)
            return 3
        meta = (uploaded.get('output') or {}).get('meta') or {}
        etd_document_id = meta.get('docId')
        print(f"  ETD upload OK: docId={etd_document_id} documentType={meta.get('documentType')}")

    request = build_ship_request(
        account_number=app.config['FEDEX_ACCOUNT_NUMBER'], settings=settings, label=label,
        ship_date=datetime.now().date(), shipper=shipper, recipient=recipient,
        packages=packages, commodities=COMMODITIES,
        invoice={'invoice_number': invoice_number, 'currency': 'JPY', 'incoterm': 'DAP',
                 'export_reason': 'SALE', 'freight': 3000, 'insurance': 0,
                 'goods_value': sum(c['amount'] for c in COMMODITIES), 'declared_value': DECLARED_VALUE},
        reference=invoice_number, etd_document_id=etd_document_id,
    )
    try:
        body = fedex_client.create_shipment(request)
    except FedexError as exc:
        _report_error('Ship API create', exc)
        return 3

    result = parse_ship_response(body)
    print(f"  created: master tracking {result['tracking_number']}  service {result['service_type']}  "
          f"ship date {result['ship_date']}  packages {', '.join(result['package_tracking_numbers']) or '-'}")
    print(f"  net charge: {result['net_charge']} {result['currency'] or ''}  "
          f"transactionId: {result['transaction_id']}")
    for alert in result['alerts']:
        print(f"  alert {alert['alert_type']} {alert['code']}: {alert['message']}")

    archive = build_label_archive(result['documents'], label['image_type'])
    for part in archive['parts']:
        print(f"  document: {part['source']} #{part['package_sequence'] or '-'} {part['content_type']} "
              f"{part['doc_type']} pages={part['pages']} archived={part['archived']}")
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / (f"{settings['service_type']}_{packages_count}pkg_{label['label_format']}"
                      f".{archive['extension']}")
    path.write_bytes(archive['content'])
    print(f"  saved {path} ({len(archive['content'])} bytes)")

    if args.keep:
        print("  --keep: the test shipment was NOT cancelled")
        return 0
    try:
        cancelled = fedex_client.cancel_shipment(result['tracking_number'], shipper['address']['countryCode'])
    except FedexError as exc:
        _report_error('Ship API cancel', exc)
        return 4
    output = cancelled.get('output') or {}
    print(f"  cancelled: cancelledShipment={output.get('cancelledShipment')} "
          f"message={output.get('successMessage') or output.get('message')} "
          f"transactionId={cancelled.get('transactionId')}")
    return 0 if output.get('cancelledShipment') is not False else 4


def main():
    parser = argparse.ArgumentParser(description='FedEx sandbox smoke test (create + cancel test shipments)')
    parser.add_argument('--env-file', help='read the uncommented FEDEX_* entries of this .env file')
    parser.add_argument('--label-format', default='both', choices=('A4', 'THERMAL', 'both'),
                        help='label format(s) to generate')
    parser.add_argument('--packages', default='1,2', help='comma-separated package counts (1-30), e.g. 1,2')
    parser.add_argument('--to', default='US', choices=sorted(RECIPIENTS), help='preset destination')
    parser.add_argument('--shipper-json', help='shipper (exporter profile) JSON file')
    parser.add_argument('--recipient-json', help='recipient (consignee) JSON file; overrides --to')
    parser.add_argument('--etd', action='store_true', help='upload a dummy commercial invoice (ETD) first')
    parser.add_argument('--out', default='fedex-sandbox-labels', help='directory for the label files')
    parser.add_argument('--keep', action='store_true', help='do not cancel the test shipments')
    args = parser.parse_args()

    if args.env_file:
        loaded = _load_env_file(args.env_file)
        print(f"Loaded from env file: {', '.join(loaded) or '(nothing)'}")

    base = os.environ.get('FEDEX_API_BASE') or 'https://apis-sandbox.fedex.com'
    if 'sandbox' not in base.lower():
        print(f"REFUSED: FEDEX_API_BASE={base} is not the FedEx sandbox. This script only runs against sandbox.")
        return 2
    missing = [k for k in SECRET_KEYS if not os.environ.get(k)]
    if missing:
        print(f"Missing credentials: {', '.join(missing)}")
        return 2
    try:
        counts = [int(x) for x in args.packages.split(',') if x.strip()]
    except ValueError:
        counts = []
    if not counts or any(not 1 <= n <= 30 for n in counts):
        print("--packages must be comma-separated numbers between 1 and 30")
        return 2

    from flask import Flask
    from warehouse.dn import fedex_client
    from warehouse.dn.fedex_shipment import AddressError, LABEL_FORMATS, label_settings

    app = Flask('fedex_sandbox_smoke')
    app.config.update({k: os.environ[k] for k in FEDEX_KEYS if os.environ.get(k)})
    app.config['FEDEX_API_BASE'] = base
    settings = {
        'service_type': app.config.get('FEDEX_SERVICE_TYPE', 'INTERNATIONAL_ECONOMY'),
        'pickup_type': app.config.get('FEDEX_PICKUP_TYPE', 'USE_SCHEDULED_PICKUP'),
        'duties_payment_type': app.config.get('FEDEX_DUTIES_PAYMENT_TYPE', 'RECIPIENT').upper(),
    }
    formats = LABEL_FORMATS if args.label_format == 'both' else (args.label_format,)
    try:
        shipper, recipient = _parties(
            _load_json(args.shipper_json) if args.shipper_json else SHIPPER,
            _load_json(args.recipient_json) if args.recipient_json else RECIPIENTS[args.to])
    except AddressError as exc:
        print(f"Address does not fit the FedEx format: {exc}")
        return 2

    exit_code = 0
    with app.app_context():
        print(f"API base: {base}  (document API: {fedex_client.document_api_base()})")
        print(f"Service {settings['service_type']} / pickup {settings['pickup_type']} / "
              f"duties {settings['duties_payment_type']} / to {recipient['address']['countryCode']}")
        for fmt in formats:
            label = label_settings(app.config, fmt)
            for count in counts:
                exit_code = _run_one(app, settings, label, count, shipper, recipient, args) or exit_code
    return exit_code


if __name__ == '__main__':
    sys.exit(main())
