"""FedEx 测试环境（sandbox）冒烟：按面单打印方式（A4 / THERMAL）× 箱数各建一票测试运单 → 存面单 → 取消。

不进 pytest、不连数据库，直接调 WMS 的 FedEx 对接插件（warehouse/carrier/integrations/fedex：FedexAdapter 的
upload_documents / create_shipment / label_archive / cancel_shipment，与 WMS 建单走同一套代码）。
面单文件按「服务_箱数_打印方式.扩展名」存（如 INTERNATIONAL_ECONOMY_2pkg_A4.pdf），可作为向 FedEx 申请面单认证
（label certification）的样张。

只连 FedEx sandbox。凭证从环境变量读（FEDEX_API_KEY / FEDEX_SECRET_KEY / FEDEX_ACCOUNT_NUMBER，可选
FEDEX_SERVICE_TYPE / FEDEX_PICKUP_TYPE / FEDEX_DUTIES_PAYMENT_TYPE / FEDEX_LABEL_THERMAL_IMAGE_TYPE /
FEDEX_LABEL_THERMAL_PAGE_SIZE），也可用 --env-file 只读入某个 .env 里未注释的这些项。不打印任何凭证。
（WMS 本身不再读这些环境变量，对接设置在承运商编辑页里按承运商保存。）

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
    'FEDEX_API_KEY', 'FEDEX_SECRET_KEY', 'FEDEX_ACCOUNT_NUMBER', 'FEDEX_SERVICE_TYPE', 'FEDEX_PICKUP_TYPE',
    'FEDEX_DUTIES_PAYMENT_TYPE', 'FEDEX_LABEL_THERMAL_IMAGE_TYPE', 'FEDEX_LABEL_THERMAL_PAGE_SIZE',
)
SECRET_KEYS = ('FEDEX_API_KEY', 'FEDEX_SECRET_KEY', 'FEDEX_ACCOUNT_NUMBER')
# 环境变量 → FedexAdapter 的设置项
SETTING_KEYS = {
    'FEDEX_SERVICE_TYPE': 'service_type',
    'FEDEX_PICKUP_TYPE': 'pickup_type',
    'FEDEX_DUTIES_PAYMENT_TYPE': 'duties_payment_type',
    'FEDEX_LABEL_THERMAL_IMAGE_TYPE': 'thermal_image_type',
    'FEDEX_LABEL_THERMAL_PAGE_SIZE': 'thermal_page_size',
}

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
    """JSON / 预设数据 → 交给插件的发件人 / 收件人（与 WMS 建单时的 shipment.shipper / recipient 同形）"""
    from warehouse.common.countries import COUNTRY_NAMES_EN

    country = (shipper_raw.get('country_code') or 'JP').upper()
    shipper = {
        'company_name': shipper_raw.get('company_name'),
        'person_name': shipper_raw.get('person_name'),
        'phone': shipper_raw.get('phone'),
        'email': shipper_raw.get('email'),
        'address_text': shipper_raw.get('address_en'),
        'postal_code': shipper_raw.get('postal_code'),
        'country_code': country,
        'country_name': COUNTRY_NAMES_EN.get(country),
        'tax_id': shipper_raw.get('tax_id'),
    }
    recipient = {
        'company_name': recipient_raw.get('company'),
        'person_name': recipient_raw.get('name') or recipient_raw.get('company'),
        'phone': recipient_raw.get('phone'),
        'email': None,
        'address_line1': recipient_raw.get('address_line1'),
        'address_line2': recipient_raw.get('address_line2'),
        'city': recipient_raw.get('city'),
        'state': recipient_raw.get('state'),
        'postal_code': recipient_raw.get('postal_code'),
        'country_code': (recipient_raw.get('country') or '').upper(),
        'tax_id': recipient_raw.get('tax_id'),
        'tax_id_type': recipient_raw.get('tax_id_type'),
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


def _run_one(adapter, label, packages_count, shipper, recipient, args):
    """建一票 → 存面单 → 取消。返回进程退出码（0 = 成功）"""
    from warehouse.carrier.integrations.base import CarrierError, clock
    from warehouse.dn.carrier_services import split_evenly

    service = adapter.service_type()
    print(f"\n== {service} / {packages_count} package(s) / {label['label_format']} "
          f"({label['image_type']} {label['stock_type']})")
    invoice_number = f"SANDBOX-{datetime.now():%Y%m%d%H%M%S}-{packages_count}{label['label_format'][0]}"
    shipment = {
        'reference': invoice_number,
        'ship_date': datetime.now().date(),
        'shipper': shipper,
        'recipient': recipient,
        'packages': [{'gross_weight_kg': Decimal('2.5'), 'length_mm': 300, 'width_mm': 200, 'height_mm': 150,
                      'declared_value': value} for value in split_evenly(DECLARED_VALUE, packages_count)],
        'commodities': COMMODITIES,
        'invoice': {'invoice_number': invoice_number, 'currency': 'JPY', 'incoterm': 'DAP',
                    'export_reason': 'SALE', 'freight': 3000, 'insurance': 0,
                    'goods_value': sum(c['amount'] for c in COMMODITIES), 'declared_value': DECLARED_VALUE},
        'documents': ({'commercial_invoice': {'content': _dummy_invoice_pdf(invoice_number),
                                              'file_name': f'CI_{invoice_number}.pdf'}} if args.etd else None),
    }

    document_ref = None
    if args.etd:
        try:
            document_ref = adapter.upload_documents(shipment, deadline=clock() + 60)
        except CarrierError as exc:
            _report_error('Trade Documents Upload', exc)
            return 3
        print(f"  ETD upload OK: docId={document_ref}")

    try:
        result = adapter.create_shipment(shipment, label, deadline=clock() + 90, document_ref=document_ref)
    except CarrierError as exc:
        _report_error('Ship API create', exc)
        return 3
    print(f"  created: master tracking {result['tracking_number']}  service {result['service_type']}  "
          f"ship date {result['ship_date']}  packages {', '.join(result['package_tracking_numbers']) or '-'}")
    print(f"  net charge: {result['net_charge']} {result['currency'] or ''}  "
          f"transactionId: {result['transaction_id']}")
    for alert in result['alerts']:
        print(f"  alert {alert['alert_type']} {alert['code']}: {alert['message']}")

    archive = adapter.label_archive(result['documents'], label)
    for part in archive['parts']:
        print(f"  document: {part['source']} #{part['package_sequence'] or '-'} {part['content_type']} "
              f"{part['doc_type']} pages={part['pages']} archived={part['archived']}")
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{service}_{packages_count}pkg_{label['label_format']}.{archive['extension']}"
    path.write_bytes(archive['content'])
    print(f"  saved {path} ({len(archive['content'])} bytes)")

    if args.keep:
        print("  --keep: the test shipment was NOT cancelled")
        return 0
    try:
        outcome = adapter.cancel_shipment(result['tracking_number'], sender_country=shipper['country_code'],
                                          deadline=clock() + 60)
    except CarrierError as exc:
        _report_error('Ship API cancel', exc)
        return 4
    print(f"  cancel: {outcome['result']} {outcome.get('reason') or ''} {outcome.get('problem') or ''} "
          f"transactionId={outcome.get('transaction_id')}")
    return 0 if outcome['result'] == 'confirmed' else 4


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
    from warehouse.carrier.integrations.fedex import FedexAdapter

    # 只连 sandbox：environment 固定，不从环境变量读
    settings = {'environment': 'sandbox', 'account_number': os.environ['FEDEX_ACCOUNT_NUMBER'],
                'etd_enabled': bool(args.etd)}
    for env_name, key in SETTING_KEYS.items():
        if os.environ.get(env_name):
            try:
                settings.update(FedexAdapter.clean_settings({key: os.environ[env_name].strip()}))
            except ValueError as exc:
                print(f"{env_name}: {exc.args[-1]}")
                return 2
    adapter = FedexAdapter(settings, {'api_key': os.environ['FEDEX_API_KEY'],
                                      'secret_key': os.environ['FEDEX_SECRET_KEY']})
    shipper, recipient = _parties(
        _load_json(args.shipper_json) if args.shipper_json else SHIPPER,
        _load_json(args.recipient_json) if args.recipient_json else RECIPIENTS[args.to])
    problems = adapter.check_shipment({'shipper': shipper, 'recipient': recipient, 'invoice': {'incoterm': 'DAP'}})
    if problems:
        for problem in problems:
            print(f"{problem['code']}: {problem['message']}")
        return 2

    app = Flask('fedex_sandbox_smoke')
    formats = adapter.label_formats if args.label_format == 'both' else (args.label_format,)
    exit_code = 0
    with app.app_context():
        client = adapter.client()
        print(f"API base: {client.api_base}  (document API: {client.document_api_base})")
        print(f"Service {adapter.service_type()} / pickup {adapter.settings['pickup_type']} / "
              f"duties {adapter.duties_payment_type} / to {recipient['country_code']}")
        for fmt in formats:
            label = adapter.label_options(fmt)
            for count in counts:
                exit_code = _run_one(adapter, label, count, shipper, recipient, args) or exit_code
    return exit_code


if __name__ == '__main__':
    sys.exit(main())
