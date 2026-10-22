"""FedEx 对接（Ship API 建单 / 取消、Trade Documents Upload API 电子单证、Pickup Request API 上门揽收、OAuth 测试连接）。

设置项见 FedexAdapter.fields：账号与 API Key / Secret Key 按承运商记录保存（密钥加密），每家公司用自己的 FedEx 账号。
请求组装 / 响应解析在 shipment.py、pickup.py（纯函数），HTTP 在 client.py。
"""
import logging
import re

from ..base import (
    FIELD_BOOLEAN, FIELD_SECRET, FIELD_SELECT, FIELD_TEXT, STEP_DOCUMENTS, STEP_PICKUP, STEP_PICKUP_CANCEL,
    CarrierAdapter, CarrierError, CarrierResponseError, option, setting,
)
from .client import FedexClient
from .pickup import (
    MAX_REMARKS, PickupRequestError, build_availability_request, build_cancel_request, build_create_request,
    parse_availability_response, parse_cancel_response, parse_create_response, pickup_contact,
)
from .shipment import (
    AddressError, MAX_PACKAGES_PER_REQUEST, SHIPPER_TIN_TYPE, build_label_archive, build_ship_request,
    consignee_address, parse_page_size, parse_ship_response, split_address_text, tin_type_for,
)

logger = logging.getLogger(__name__)

API_BASES = {
    'production': 'https://apis.fedex.com',
    'sandbox': 'https://apis-sandbox.fedex.com',
}
# Trade Documents Upload API 与 Ship API 不在同一个域名
DOCUMENT_API_BASES = {
    'production': 'https://documentapi.prod.fedex.com',
    'sandbox': 'https://documentapitest.prod.fedex.com/sandbox',
}

# 面单打印方式 → (imageType, labelStockType)
LABEL_STOCKS = {
    'A4': ('PDF', 'PAPER_85X11_TOP_HALF_LABEL'),   # A4 / Letter 普通纸：上半页面单，下半页折叠说明
    'THERMAL': ('PDF', 'STOCK_4X6'),               # 4x6 英寸（约 100×150 mm）热敏面单纸
}
# 注意 PAPER_4X6 在 FedEx 回的是 Letter 页、面单在左上角（sandbox 实测），热敏机要用 STOCK_4X6

# FedEx 取消接口回「已取消 / 查无此运单」时视为取消成功（取消后本地写库失败再点取消、补偿取消撞上已取消的运单）。
# 依据：FedEx Ship API（REST）公开文档没有单列这两种情况的错误码，所以只按错误码（不看 message，会随 X-locale 变）判断：
#   - 码里同时有 ALREADY 与 CANCEL / DELETE（已取消）；
#   - 码里同时有 TRACKING / SHIPMENT 与 NOT FOUND / NOT EXIST（查无此运单）；
#   - 精确码 ALREADY_CANCELLED_CODES：旧版 FedEx Web Services 的 8159
#     「Shipment Delete was requested for a tracking number already in a deleted state」。
# 命中时日志记原始错误码（WARNING）；正式环境遇到没覆盖的码，补到 ALREADY_CANCELLED_CODES。
ALREADY_CANCELLED_CODES = frozenset({'8159'})


def already_cancelled(items) -> bool:
    """FedEx 错误 / 提示里有「已取消 / 查无此运单」类的码"""
    for item in items or []:
        if not isinstance(item, dict):
            continue
        code = str(item.get('code') or '').strip().upper()
        if not code:
            continue
        if code in ALREADY_CANCELLED_CODES:
            return True
        compact = re.sub(r'[^A-Z0-9]', '', code)
        if 'ALREADY' in compact and ('CANCEL' in compact or 'DELETE' in compact):
            return True
        if ('TRACKING' in compact or 'SHIPMENT' in compact) and ('NOTFOUND' in compact or 'NOTEXIST' in compact):
            return True
    return False


def _codes(items) -> str:
    return ', '.join(str(i.get('code')) for i in items or [] if isinstance(i, dict)) or '-'


def cancel_verdict(body):
    """取消响应（HTTP 200）→ (verdict, detail)：
    - ('confirmed', None)：cancelledShipment 为 True
    - ('confirmed', 'already_cancelled')：提示里有「已取消 / 查无此运单」的码（cancelledShipment 为 False 或缺）
    - ('refused', 原因)：cancelledShipment 为 False —— FedEx 明确没取消
    - ('unclear', 原因)：缺 cancelledShipment / 不是布尔 / 响应结构不对 —— 不能当成功"""
    output = body.get('output') if isinstance(body, dict) else None
    output = output if isinstance(output, dict) else {}
    flag = output.get('cancelledShipment')
    alerts = output.get('alerts')
    alerts = [a for a in alerts if isinstance(a, dict)] if isinstance(alerts, list) else []
    if flag is True:
        return 'confirmed', None
    if already_cancelled(alerts):
        return 'confirmed', 'already_cancelled'
    text = output.get('message') or '; '.join(f"{a.get('code')}: {a.get('message')}" for a in alerts)
    if flag is False:
        return 'refused', text or 'no reason given'
    return 'unclear', text or 'the response does not say whether the shipment was cancelled (cancelledShipment)'


def loose_tracking_number(body):
    """解析失败的建单响应里尽量找出主运单号（用来补偿取消）"""
    try:
        shipment = ((body.get('output') or {}).get('transactionShipments') or [None])[0] or {}
        value = shipment.get('masterTrackingNumber') or (
            ((shipment.get('completedShipmentDetail') or {}).get('masterTrackingId') or {}).get('trackingNumber'))
        if not value:
            pieces = [p for p in shipment.get('pieceResponses') or [] if isinstance(p, dict)]
            if pieces:
                value = pieces[0].get('masterTrackingNumber') or pieces[0].get('trackingNumber')
    except (AttributeError, TypeError, IndexError):
        return None
    value = str(value or '').strip()[:100]
    return value or None


def _label(en, zh, ja):
    return {'en-US': en, 'zh-CN': zh, 'ja-JP': ja}


SERVICE_TYPES = (
    option('INTERNATIONAL_ECONOMY', 'International Economy'),
    option('INTERNATIONAL_PRIORITY', 'International Priority'),
    option('FEDEX_INTERNATIONAL_PRIORITY', 'FedEx International Priority'),
    option('FEDEX_INTERNATIONAL_PRIORITY_EXPRESS', 'FedEx International Priority Express'),
    option('FEDEX_INTERNATIONAL_CONNECT_PLUS', 'FedEx International Connect Plus'),
)


class FedexAdapter(CarrierAdapter):
    code = 'fedex'
    name = 'FedEx'
    portal_name = 'FedEx Ship Manager'
    portal_url = 'https://www.fedex.com/shipping/shipEntryAction.do'
    max_packages = MAX_PACKAGES_PER_REQUEST
    supports_pickup = True
    pickup_max_remarks = MAX_REMARKS

    fields = (
        setting('environment', FIELD_SELECT, required=True, default='sandbox',
                label=_label('Environment', '环境', '環境'),
                options=[option('production', 'Production', '正式', '本番'),
                         option('sandbox', 'Sandbox (test)', '测试（Sandbox）', 'テスト（Sandbox）')],
                help=_label('Use Sandbox with test keys from the FedEx Developer Portal; nothing is shipped or billed.',
                            '用 FedEx 开发者门户的测试密钥时选「测试」，不会真的发货或计费。',
                            'FedEx Developer Portal のテスト用キーを使うときは「テスト」を選びます。発送・課金はされません。')),
        setting('account_number', FIELD_TEXT, required=True,
                label=_label('Account number', '账号', 'アカウント番号'),
                help=_label('Shipping charges are billed to this account.', '运费记在这个账号上。',
                            '送料はこのアカウントに請求されます。')),
        setting('api_key', FIELD_SECRET, required=True, label=_label('API key', 'API Key', 'API キー')),
        setting('secret_key', FIELD_SECRET, required=True, label=_label('Secret key', 'Secret Key', 'シークレットキー')),
        setting('service_type', FIELD_SELECT, group='shipping', default='INTERNATIONAL_ECONOMY',
                label=_label('Service', '服务', 'サービス'), options=list(SERVICE_TYPES)),
        setting('pickup_type', FIELD_SELECT, group='shipping', default='CONTACT_FEDEX_TO_SCHEDULE',
                label=_label('Pickup', '取件方式', '集荷'),
                options=[option('CONTACT_FEDEX_TO_SCHEDULE', 'Book each pickup (Pickups page)',
                                '每次预约上门取件（在「上门揽收」页约）', '都度集荷を依頼（「集荷依頼」画面）'),
                         option('USE_SCHEDULED_PICKUP', 'Regular scheduled pickup (contract with FedEx)',
                                '定期上门取件（已和 FedEx 签约）', '定期集荷（FedEx と契約済み）'),
                         option('DROPOFF_AT_FEDEX_LOCATION', 'Drop off at a FedEx location', '自送到 FedEx 网点',
                                'FedEx 拠点へ持ち込み')]),
        setting('duties_payment_type', FIELD_SELECT, group='shipping', default='RECIPIENT',
                label=_label('Duties and taxes paid by', '关税由谁付', '関税・税金の支払い'),
                options=[option('RECIPIENT', 'Recipient', '收件人', '受取人'),
                         option('SENDER', 'Sender (needed for DDP)', '发件人（DDP 时选这个）', '送り主（DDP の場合）')]),
        setting('etd_enabled', FIELD_BOOLEAN, group='shipping', default=False,
                label=_label('Send the commercial invoice electronically (ETD)', '电子提交商业发票（ETD）',
                             'インボイスを電子送信（ETD）'),
                help=_label('The invoice is uploaded to FedEx before the shipment is created; you do not need to '
                            'attach paper copies. The FedEx account must have ETD enabled.',
                            '建运单前把 CI 上传给 FedEx，不用再随货附纸质发票。需要 FedEx 账号已开通 ETD。',
                            '出荷前にインボイスを FedEx へアップロードし、紙の添付が不要になります。'
                            'FedEx アカウントで ETD の利用が必要です。')),
        setting('default_label_format', FIELD_SELECT, group='label', default='A4',
                label=_label('Default label printer', '默认面单打印方式', '既定のラベル印刷'),
                options=[option('A4', 'Plain paper (A4)', '普通打印机（A4 纸）', '普通紙（A4）'),
                         option('THERMAL', 'Thermal label printer', '热敏标签机', 'サーマルラベルプリンター')]),
        setting('thermal_page_size', FIELD_SELECT, group='label', default='100x150',
                label=_label('Thermal label size', '热敏面单纸尺寸', 'サーマルラベルのサイズ'),
                options=[option('100x150', '100 × 150 mm', '100 × 150 mm', '100 × 150 mm'),
                         option('101.6x152.4', '4 × 6 in', '4 × 6 英寸', '4 × 6 インチ')],
                help=_label('On 100 × 150 mm paper the 4 × 6 in label is scaled to 98.4% so nothing is cut off.',
                            '用 100×150 mm 纸时，4×6 英寸的面单会等比缩到 98.4%，打印时不会切边。',
                            '100×150 mm の用紙では 4×6 インチのラベルを 98.4% に縮小し、端が切れないようにします。')),
        setting('thermal_image_type', FIELD_SELECT, group='label', default='PDF',
                label=_label('Thermal label file', '热敏面单文件格式', 'サーマルラベルの形式'),
                options=[option('PDF', 'PDF (print from the browser)', 'PDF（浏览器直接打印）', 'PDF（ブラウザーから印刷）'),
                         option('ZPLII', 'ZPL II (Zebra command file)', 'ZPL II（斑马指令文件）', 'ZPL II（Zebra コマンド）'),
                         option('EPL2', 'EPL2 (Eltron command file)', 'EPL2（指令文件）', 'EPL2（コマンドファイル）')]),
    )

    # ------------------------------------------------------------------
    # 设置
    # ------------------------------------------------------------------

    @property
    def environment(self) -> str:
        return self.settings.get('environment') if self.settings.get('environment') in API_BASES else 'sandbox'

    def client(self) -> FedexClient:
        return FedexClient(
            api_base=API_BASES[self.environment],
            document_api_base=DOCUMENT_API_BASES[self.environment],
            api_key=self.secrets.get('api_key'),
            secret_key=self.secrets.get('secret_key'),
            account_number=self.settings.get('account_number'),
        )

    def label_options(self, label_format=None) -> dict:
        fmt = (label_format or self.default_label_format()).strip().upper()
        image_type, stock_type = LABEL_STOCKS.get(fmt, LABEL_STOCKS['A4'])
        page_size = None
        if fmt == 'THERMAL':
            image_type = str(self.settings.get('thermal_image_type') or image_type).upper()
            page_size = self.settings.get('thermal_page_size') or None
        return {'label_format': fmt, 'image_type': image_type, 'stock_type': stock_type, 'page_size': page_size}

    @classmethod
    def service_name(cls, service_type) -> str:
        for item in SERVICE_TYPES:
            if item['value'] == service_type:
                return item['label']['en-US']
        return super().service_name(service_type)

    def documents_enabled(self) -> bool:
        return self.settings.get('etd_enabled') is True

    @property
    def duties_payment_type(self) -> str:
        return str(self.settings.get('duties_payment_type') or 'RECIPIENT').upper()

    # ------------------------------------------------------------------
    # 一票货能不能交给 FedEx
    # ------------------------------------------------------------------

    @staticmethod
    def _shipper(shipper: dict) -> dict:
        """发件人：英文地址字符串按 FedEx 格式拆行（放不下抛 AddressError）"""
        address = split_address_text(shipper['address_text'], shipper['country_code'], shipper.get('postal_code'),
                                     shipper.get('country_name'))
        tins = [{'number': shipper['tax_id'], 'tinType': SHIPPER_TIN_TYPE}] if shipper.get('tax_id') else []
        return {
            'company_name': shipper.get('company_name'),
            'person_name': shipper.get('person_name'),
            'phone': shipper.get('phone'),
            'email': shipper.get('email'),
            'address': address,
            'tins': tins,
        }

    @staticmethod
    def _recipient(recipient: dict) -> dict:
        address = consignee_address(recipient, recipient['country_code'])
        tins = []
        if recipient.get('tax_id'):
            tins.append({'number': recipient['tax_id'], 'tinType': tin_type_for(recipient.get('tax_id_type'))})
        return {
            'company_name': recipient.get('company_name'),
            'person_name': recipient.get('person_name'),
            'phone': recipient.get('phone'),
            'email': None,
            'address': address,
            'tins': tins,
        }

    def check_shipment(self, shipment: dict) -> list:
        blockers = []
        # DDP = 发件人付关税；关税付款方设成收件人付，会被 FedEx 向收件人收关税，与贸易条件矛盾
        incoterm = str((shipment.get('invoice') or {}).get('incoterm') or '').strip().upper()
        if incoterm == 'DDP' and self.duties_payment_type != 'SENDER':
            blockers.append({
                'code': 'INCOTERM_DUTIES_MISMATCH', 'field': 'incoterm',
                'message': (f"Incoterm DDP means the shipper pays duties and taxes, but the FedEx duties payment type "
                            f"is {self.duties_payment_type}; use another incoterm (e.g. DAP) or set the duties "
                            "payment type to SENDER in the carrier settings"),
            })
        if shipment.get('shipper'):
            try:
                self._shipper(shipment['shipper'])
            except AddressError as exc:
                blockers.append({'code': 'SHIPPER_ADDRESS_INVALID', 'field': 'address_en',
                                 'message': f"Ship-from address cannot be used for FedEx: {exc}"})
        if shipment.get('recipient'):
            try:
                self._recipient(shipment['recipient'])
            except AddressError as exc:
                blockers.append({'code': 'RECIPIENT_ADDRESS_INVALID', 'field': 'consignee',
                                 'message': f"Consignee address cannot be used for FedEx: {exc}"})
        return blockers

    # ------------------------------------------------------------------
    # 建单 / 取消 / 面单 / 测试连接
    # ------------------------------------------------------------------

    def upload_documents(self, shipment: dict, *, deadline) -> str:
        invoice = shipment['documents']['commercial_invoice']
        uploaded = self.client().upload_etd_document(
            invoice['content'], invoice['file_name'], shipment['shipper']['country_code'],
            shipment['recipient']['country_code'], deadline=deadline)
        document_id = ((uploaded.get('output') or {}).get('meta') or {}).get('docId')
        if not document_id:
            raise CarrierError("FedEx trade document upload returned no docId", step=STEP_DOCUMENTS)
        return document_id

    def create_shipment(self, shipment: dict, label: dict, *, deadline, document_ref=None) -> dict:
        request = build_ship_request(
            account_number=self.settings.get('account_number'),
            settings={
                'service_type': self.service_type(),
                'pickup_type': self.settings.get('pickup_type'),
                'duties_payment_type': self.duties_payment_type,
            },
            label=label,
            ship_date=shipment['ship_date'],
            shipper=self._shipper(shipment['shipper']),
            recipient=self._recipient(shipment['recipient']),
            packages=shipment['packages'],
            commodities=shipment['commodities'],
            invoice=shipment['invoice'],
            reference=shipment.get('reference'),
            etd_document_id=document_ref,
        )
        body = self.client().create_shipment(request, deadline=deadline)
        try:
            return parse_ship_response(body)
        except (ValueError, TypeError, AttributeError, KeyError, IndexError) as exc:
            raise CarrierResponseError(f"Unexpected FedEx response: {exc}", tracking_number=loose_tracking_number(body),
                                       transaction_id=body.get('transactionId')) from exc

    def label_archive(self, documents, label: dict) -> dict:
        return build_label_archive(documents, label['image_type'], parse_page_size(label.get('page_size')))

    def cancel_shipment(self, tracking_number: str, *, sender_country: str, deadline) -> dict:
        try:
            body = self.client().cancel_shipment(tracking_number, sender_country, deadline=deadline)
        except CarrierError as exc:
            if already_cancelled(exc.errors):
                # 例：上次取消在 FedEx 成功了但本地写库失败，再点取消 FedEx 回「已取消」
                logger.warning("FedEx reports shipment %s as already cancelled / not found (%s)",
                               tracking_number, _codes(exc.errors))
                return {'result': 'confirmed', 'reason': 'already_cancelled', 'transaction_id': exc.transaction_id}
            raise
        transaction_id = body.get('transactionId') if isinstance(body, dict) else None
        output = body.get('output') if isinstance(body, dict) and isinstance(body.get('output'), dict) else {}
        verdict, detail = cancel_verdict(body)
        if verdict == 'confirmed':
            if detail == 'already_cancelled':
                logger.warning("FedEx reports shipment %s as already cancelled (%s)", tracking_number,
                               _codes(output.get('alerts') if isinstance(output.get('alerts'), list) else None))
            return {'result': 'confirmed', 'reason': detail, 'transaction_id': transaction_id}
        if verdict == 'unclear':
            logger.error("FedEx cancel response for shipment %s is unclear (transactionId %s): %s",
                         tracking_number, transaction_id, detail)
        return {'result': verdict, 'reason': 'bad_response' if verdict == 'unclear' else None, 'problem': detail,
                'transaction_id': transaction_id, 'alerts': output.get('alerts') or []}

    def test_connection(self, *, deadline=None) -> dict:
        self.client().access_token(force_refresh=True, deadline=deadline)
        return {'ok': True, 'environment': self.environment}

    # ------------------------------------------------------------------
    # 上门揽收（Pickup Request API）
    # ------------------------------------------------------------------

    def books_pickups(self) -> bool:
        return self.settings.get('pickup_type') == 'CONTACT_FEDEX_TO_SCHEDULE'

    def pickup_location(self, place: dict):
        location, blockers = {'contact': None, 'address': None}, []
        if place.get('address_text'):       # 没有地址由 WMS 报 PICKUP_ADDRESS_MISSING
            try:
                location['address'] = split_address_text(place['address_text'], place.get('country_code'),
                                                         place.get('postal_code'), place.get('country_name'))
            except AddressError as exc:
                blockers.append({'code': 'PICKUP_ADDRESS_INVALID', 'field': 'address_en',
                                 'message': f"Pickup address cannot be used for FedEx: {exc}"})
        try:
            location['contact'] = pickup_contact(place.get('company_name'), place.get('person_name'),
                                                 place.get('phone'))
        except PickupRequestError as exc:
            blockers.append({'code': 'PICKUP_PHONE_INVALID', 'field': 'phone',
                             'message': f"Pickup contact phone: {exc}"})
        return (None if blockers else location), blockers

    def pickup_availability(self, location: dict, *, deadline) -> dict:
        body = self.client().pickup_availability(build_availability_request(location['address']), deadline=deadline)
        result = parse_availability_response(body)
        result['transaction_id'] = body.get('transactionId')
        return result

    def schedule_pickup(self, location: dict, pickup: dict, *, deadline) -> dict:
        request = build_create_request(
            account_number=self.settings.get('account_number'),
            contact=location['contact'], address=location['address'],
            pickup_date=pickup['pickup_date'], ready=pickup['ready'], close=pickup['close'],
            same_day=pickup['same_day'], tz=pickup['tz'],
            package_count=pickup['package_count'], total_weight=pickup['total_weight_kg'],
            remarks=pickup.get('remarks'), notification_email=pickup.get('notification_email'),
        )
        body = self.client().create_pickup(request, deadline=deadline)
        transaction_id = body.get('transactionId')
        try:
            result = parse_create_response(body)
        except (ValueError, TypeError, AttributeError) as exc:
            # 没有确认号：FedEx 可能已经约上了 → 结果不明
            raise CarrierError(f"Unexpected FedEx response: {exc}", step=STEP_PICKUP, status_code=200,
                               transaction_id=transaction_id, maybe_processed=True) from exc
        return {'confirmation_code': result['confirmation_code'], 'location_code': result['location'],
                'message': result['message'], 'alerts': result['alerts'], 'transaction_id': transaction_id}

    def cancel_pickup(self, pickup: dict, *, deadline) -> dict:
        request = build_cancel_request(account_number=self.settings.get('account_number'),
                                       confirmation_code=pickup['confirmation_code'],
                                       pickup_date=pickup['pickup_date'], location=pickup.get('location_code'))
        try:
            body = self.client().cancel_pickup(request, deadline=deadline)
        except CarrierError as exc:
            if not exc.maybe_processed and already_cancelled(exc.errors):
                logger.warning("FedEx reports pickup %s as already cancelled (%s)", pickup['confirmation_code'],
                               _codes(exc.errors))
                return {'reason': 'already_cancelled', 'message': None, 'alerts': [],
                        'transaction_id': exc.transaction_id}
            raise
        transaction_id = body.get('transactionId')
        try:
            result = parse_cancel_response(body)
        except (ValueError, TypeError, AttributeError) as exc:
            raise CarrierError(f"Unexpected FedEx response: {exc}", step=STEP_PICKUP_CANCEL, status_code=200,
                               transaction_id=transaction_id, maybe_processed=True) from exc
        return {'reason': None, 'message': result['message'], 'alerts': result['alerts'],
                'transaction_id': transaction_id}
