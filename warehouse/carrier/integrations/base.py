"""承运商对接插件的接口与公用工具。

一家承运商的对接 = 一个 CarrierAdapter 子类（warehouse/carrier/integrations/<code>/），在本包 __init__ 里注册，
第三方包也可以通过 entry point 组 goodsmart_wms.carrier_integrations 注册。分工：
- WMS（warehouse/dn/carrier_services.py）：前置条件、建单记录的状态机（pending / active / unknown / cancelling …）、
  结果不明与补偿取消、运单号写回发货任务、CI / PL 带运单号重签、面单存档。与具体承运商无关。
- 适配器：声明设置项（fields：账号、密钥、服务类型……，承运商编辑页按它渲染表单）并检查设置；检查一票货能不能
  交给这家承运商（地址放不放得下、贸易条件与关税付款方是否矛盾……）；上传电子单证、建单、取消、生成面单文件、测试连接。
- 上门揽收（可选，supports_pickup = True 的插件）：WMS（warehouse/dn/pickup_services.py）管预约记录的状态机、
  占用运单、件数 / 重量合计；适配器把仓库地址转成承运商的格式、查可约时间、预约、取消。
- 跟踪（可选，supports_tracking = True 的插件）：WMS（warehouse/dn/tracking_services.py）定时查有效运单、按结果自动完成发货 /
  签收、推 dn.tracking_updated；适配器只负责查询，并把承运商的状态码归到 TRACK_* 几类。

适配器的调用失败一律抛 CarrierError。maybe_processed 表示请求可能已被承运商处理（读超时、连接中途断开、5xx、
响应看不懂）：建单时 WMS 按「结果不明」处理（不能再建、要核对），取消时按「取消结果不明」处理。
其余失败（4xx、连接没建立、时间预算不够没发出去）是确定没处理。

承运商对接的设置按承运商记录保存（carrier_integrations），不放在环境变量里：每家公司用自己的账号。
"""
import time

from flask import current_app

DEFAULT_CONNECT_TIMEOUT = 5
DEFAULT_READ_TIMEOUT = 30
# 时间预算剩余不足这么多秒就不再发下一个请求（发了多半读超时，结果反而不明）
MIN_REQUEST_SECONDS = 5

# CarrierError.step：出错的是哪一步
STEP_AUTH = 'auth'              # 取访问令牌
STEP_DOCUMENTS = 'documents'    # 上传电子单证（建单前）
STEP_CREATE = 'create'          # 建单
STEP_CANCEL = 'cancel'          # 取消
STEP_TEST = 'test'              # 测试连接
STEP_PICKUP = 'pickup'                              # 预约上门揽收
STEP_PICKUP_CANCEL = 'pickup_cancel'                # 取消揽收预约
STEP_PICKUP_AVAILABILITY = 'pickup_availability'    # 查可约的揽收日 / 时间
STEP_TRACK = 'track'                                # 查跟踪

# 跟踪状态：插件把承运商的状态码归到这几类，WMS 只按它决定自动发货 / 签收
TRACK_LABEL_CREATED = 'label_created'           # 只建了单，承运商还没收到货
TRACK_IN_TRANSIT = 'in_transit'                 # 已揽收、在途（含通关）
TRACK_OUT_FOR_DELIVERY = 'out_for_delivery'     # 派送中 / 到网点等收件人自取
TRACK_DELIVERED = 'delivered'                   # 已送达
TRACK_EXCEPTION = 'exception'                   # 异常（退回、延误、运单被取消……），要人看
TRACK_UNKNOWN = 'unknown'                       # 认不出的状态码
TRACK_STATUSES = (TRACK_LABEL_CREATED, TRACK_IN_TRANSIT, TRACK_OUT_FOR_DELIVERY, TRACK_DELIVERED, TRACK_EXCEPTION,
                  TRACK_UNKNOWN)

# 面单打印方式（各承运商共用的取值）：A4 = 普通打印机打在 A4 / Letter 纸上；THERMAL = 热敏标签机
LABEL_FORMATS = ('A4', 'THERMAL')

# 设置项类型
FIELD_TEXT = 'text'
FIELD_SECRET = 'secret'         # 加密保存，接口只返回「已设置 / 未设置」
FIELD_SELECT = 'select'
FIELD_BOOLEAN = 'boolean'
FIELD_KINDS = (FIELD_TEXT, FIELD_SECRET, FIELD_SELECT, FIELD_BOOLEAN)


def _clock() -> float:
    """单调时钟（测试里替换它来模拟时间流逝）"""
    return time.monotonic()


def clock() -> float:
    """当前时刻（deadline 用这个时钟算）"""
    return _clock()


class CarrierError(Exception):
    """承运商接口调用失败。

    Attributes:
        step: 出错的是哪一步（STEP_*）
        status_code: HTTP 状态码（超时 / 连接失败为 None）
        errors: 承运商回的错误 [{code, message}]
        transaction_id: 承运商的请求 ID（在承运商那边查找用）
        timeout: 请求超时（连接或读取），或时间预算不够没发出去
        maybe_processed: 结果不明 —— 请求可能已被承运商处理（例如运单已经建了）
        budget_exhausted: 时间预算不够，请求没有发出去
    """

    def __init__(self, message, *, step=None, status_code=None, errors=None, transaction_id=None,
                 timeout=False, maybe_processed=False, budget_exhausted=False):
        super().__init__(message)
        self.message = message
        self.step = step
        self.status_code = status_code
        self.errors = errors or []
        self.transaction_id = transaction_id
        self.timeout = timeout
        self.maybe_processed = maybe_processed
        self.budget_exhausted = budget_exhausted

    @property
    def permission_denied(self) -> bool:
        """401 / 403：凭证不对，或承运商那边没开通这个接口"""
        return self.status_code in (401, 403)


class CarrierResponseError(CarrierError):
    """建单响应看不懂（HTTP 200 但取不到运单号等）。tracking_number 是从响应里尽量找出的主运单号：
    有它时 WMS 会取消这张运单（补偿），没有就按结果不明处理。"""

    def __init__(self, message, *, tracking_number=None, transaction_id=None):
        super().__init__(message, step=STEP_CREATE, status_code=200, transaction_id=transaction_id,
                         maybe_processed=True)
        self.tracking_number = tracking_number


def _config_float(key, default) -> float:
    value = current_app.config.get(key)
    try:
        return float(value) if value not in (None, '') else float(default)
    except (TypeError, ValueError):
        return float(default)


def request_timeout(deadline=None, *, carrier='Carrier', what='request', step=None):
    """(连接超时, 读取超时)：CARRIER_CONNECT_TIMEOUT_SECONDS / CARRIER_TIMEOUT_SECONDS。
    带 deadline（clock() 的时刻）时按剩余时间收缩（两者之和不超过剩余时间）；
    剩余不足 MIN_REQUEST_SECONDS → CarrierError(budget_exhausted)，请求不发。"""
    connect = _config_float('CARRIER_CONNECT_TIMEOUT_SECONDS', DEFAULT_CONNECT_TIMEOUT) or DEFAULT_CONNECT_TIMEOUT
    read = _config_float('CARRIER_TIMEOUT_SECONDS', DEFAULT_READ_TIMEOUT) or DEFAULT_READ_TIMEOUT
    if deadline is None:
        return (connect, read)
    remaining = deadline - _clock()
    if remaining < MIN_REQUEST_SECONDS:
        raise CarrierError(f"{carrier} {what} not sent: time budget exhausted ({max(remaining, 0):.0f}s left)",
                           step=step, timeout=True, budget_exhausted=True)
    connect = min(connect, remaining / 2)
    return (connect, min(read, remaining - connect))


def setting(key, kind=FIELD_TEXT, *, label, required=False, default=None, options=None, help=None,
            group='account', placeholder=None) -> dict:
    """一个设置项。label / help 按语言给（{'en-US': …, 'zh-CN': …, 'ja-JP': …}），界面按当前语言取、没有用 en-US；
    options（select）：[{'value': …, 'label': {语言: 文本}}]。group：account（账号）/ shipping（发货）/ label（面单）。"""
    assert kind in FIELD_KINDS, kind
    return {
        'key': key, 'kind': kind, 'label': label, 'required': required, 'default': default,
        'options': options, 'help': help, 'group': group, 'placeholder': placeholder,
    }


def option(value, en, zh=None, ja=None) -> dict:
    return {'value': value, 'label': {'en-US': en, 'zh-CN': zh or en, 'ja-JP': ja or en}}


class CarrierAdapter:
    """承运商对接插件的基类。子类声明类属性并实现建单 / 取消 / 面单 / 测试连接。

    shipment（WMS 交给适配器的一票货，纯数据）：
        reference: 订单号；ship_date: date
        shipper: {company_name, person_name, phone, email, address_text, postal_code, country_code, country_name,
                  tax_id} | None（出口资料不全时为 None，那时报关问题清单已经拦着）
        recipient: {company_name, person_name, phone, email, address_line1, address_line2, city, state,
                    postal_code, country_code, tax_id, tax_id_type} | None
        packages: [{gross_weight_kg, length_mm, width_mm, height_mm, declared_value | None}]
        commodities: [{description, origin_country, hs_code, quantity, quantity_unit, unit_value, amount,
                       weight_kg, part_number}]
        invoice: {invoice_number, currency, incoterm, export_reason, freight, insurance, goods_value,
                  declared_value | None}
        documents: {'commercial_invoice': {content, file_name}} | None（开了电子单证时才有）
    """

    code = None                 # 插件代码（存进建单记录的 carrier 列），小写
    name = None                 # 显示名
    portal_name = None          # 承运商自己的发货后台（结果不明时去那里核对 / 取消），如 FedEx Ship Manager
    portal_url = None
    fields = ()                 # setting(...) 元组
    label_formats = LABEL_FORMATS
    max_packages = None         # 一票最多几箱（None = 不限）
    supports_pickup = False     # 能在 WMS 里预约上门揽收（实现了下面的 pickup_* / schedule_pickup / cancel_pickup）
    supports_tracking = False   # 能查跟踪（实现了下面的 track）：WMS 据此自动完成发货 / 签收
    pickup_max_remarks = 60     # 给快递员的留言最多几个字符

    def __init__(self, settings=None, secrets=None):
        settings = settings or {}
        secrets = secrets or {}
        self.settings = {}
        self.secrets = {}
        for field in self.fields:
            key = field['key']
            if field['kind'] == FIELD_SECRET:
                value = secrets.get(key)
                if value:
                    self.secrets[key] = value
                continue
            value = settings.get(key)
            self.settings[key] = field['default'] if value in (None, '') else value

    # ------------------------------------------------------------------
    # 描述与设置
    # ------------------------------------------------------------------

    @classmethod
    def describe(cls) -> dict:
        return {
            'code': cls.code,
            'name': cls.name,
            'portal_name': cls.portal_name,
            'portal_url': cls.portal_url,
            'label_formats': list(cls.label_formats),
            'supports_pickup': bool(cls.supports_pickup),
            'supports_tracking': bool(cls.supports_tracking),
            'fields': [dict(field) for field in cls.fields],
        }

    @classmethod
    def clean_settings(cls, settings) -> dict:
        """接口传来的设置（不含密钥）→ 要保存的值；不认识的键丢掉。取值不合法抛 ValueError(key, message)。"""
        settings = settings or {}
        cleaned = {}
        for field in cls.fields:
            key, kind = field['key'], field['kind']
            if kind == FIELD_SECRET or key not in settings:
                continue
            value = settings[key]
            if value is None or (isinstance(value, str) and not value.strip()):
                continue
            if kind == FIELD_BOOLEAN:
                if not isinstance(value, bool):
                    raise ValueError(key, f"{key} must be true or false")
            elif not isinstance(value, (str, int, float)) or isinstance(value, bool):
                raise ValueError(key, f"{key} must be a string")
            else:
                value = str(value).strip()
                if kind == FIELD_SELECT:
                    allowed = [str(o['value']) for o in (field.get('options') or [])]
                    if value not in allowed:
                        raise ValueError(key, f"{key} must be one of {', '.join(allowed)}")
            cleaned[key] = value
        return cleaned

    @classmethod
    def secret_keys(cls) -> list:
        return [field['key'] for field in cls.fields if field['kind'] == FIELD_SECRET]

    def missing_settings(self) -> list:
        """必填但没有值的设置项（key 列表）"""
        missing = []
        for field in self.fields:
            if not field['required']:
                continue
            store = self.secrets if field['kind'] == FIELD_SECRET else self.settings
            if store.get(field['key']) in (None, ''):
                missing.append(field['key'])
        return missing

    def setting_problems(self) -> list:
        """设置齐全但取值有问题时的说明（英文句子），如插件升级后旧的选项值不再支持；子类可追加"""
        problems = []
        for field in self.fields:
            value = self.settings.get(field['key'])
            if field['kind'] != FIELD_SELECT or value in (None, ''):
                continue
            allowed = [str(o['value']) for o in (field.get('options') or [])]
            if str(value) not in allowed:
                problems.append(f"{field['key']} must be one of {', '.join(allowed)} (current: {value})")
        return problems

    # ------------------------------------------------------------------
    # 能力
    # ------------------------------------------------------------------

    def default_label_format(self) -> str:
        value = str(self.settings.get('default_label_format') or '').upper()
        return value if value in self.label_formats else self.label_formats[0]

    def label_options(self, label_format=None) -> dict:
        """面单打印方式 → {label_format, image_type, stock_type, page_size}（image_type：PDF / PNG / ZPLII / EPL2）"""
        fmt = (label_format or self.default_label_format()).strip().upper()
        return {'label_format': fmt, 'image_type': 'PDF', 'stock_type': None, 'page_size': None}

    def service_type(self):
        return self.settings.get('service_type')

    @classmethod
    def service_name(cls, service_type) -> str:
        """服务代码 → 显示名（INTERNATIONAL_ECONOMY → International Economy）"""
        text = str(service_type or '').strip()
        return ' '.join(word.capitalize() for word in text.replace('_', ' ').split()) if text else ''

    def documents_enabled(self) -> bool:
        """建单前把 CI 电子提交给承运商（无纸化 / 电子贸易单证）"""
        return False

    # ------------------------------------------------------------------
    # 建单 / 取消 / 面单 / 测试连接（子类实现）
    # ------------------------------------------------------------------

    def check_shipment(self, shipment: dict) -> list:
        """这票货交给这家承运商有什么问题 → blockers [{code, message, field?}]（如地址放不下）"""
        return []

    def upload_documents(self, shipment: dict, *, deadline) -> str:
        """建单前上传电子单证，返回建单时引用的文档 ID（documents_enabled() 为真时才会调用）"""
        raise NotImplementedError

    def create_shipment(self, shipment: dict, label: dict, *, deadline, document_ref=None) -> dict:
        """建单 → {tracking_number, package_tracking_numbers, service_type, ship_date, net_charge(Decimal | None),
        currency, documents, transaction_id, alerts[{code, alert_type, message}]}。
        documents 交给 label_archive() 生成面单文件。失败抛 CarrierError，响应看不懂抛 CarrierResponseError。"""
        raise NotImplementedError

    def label_archive(self, documents, label: dict) -> dict:
        """建单响应里的文档 → 面单存档 {content, extension, parts, …}（WMS 存 content、按 extension 定文件名和下载类型）"""
        raise NotImplementedError

    def cancel_shipment(self, tracking_number: str, *, sender_country: str, deadline) -> dict:
        """取消整票运单 → {result, reason, problem, transaction_id, alerts}：
        - result confirmed：已取消；承运商回「已取消 / 查无此运单」也算（reason already_cancelled）
        - result refused：承运商明确没取消（problem 是原因）
        - result unclear：回了但看不出取消了没有（reason bad_response）
        请求本身失败（超时、4xx、5xx……）抛 CarrierError。"""
        raise NotImplementedError

    def test_connection(self, *, deadline=None) -> dict:
        """用当前设置连一次承运商（只取令牌 / 查账号，不建单）→ dict（附加信息，原样放进测试结果）；失败抛 CarrierError"""
        raise NotImplementedError

    # ------------------------------------------------------------------
    # 上门揽收（supports_pickup = True 时实现）
    # ------------------------------------------------------------------

    def books_pickups(self) -> bool:
        """建好的运单要在 WMS 里另约上门揽收（没有定期集荷）。运单状态里据此提示「还没约揽收」"""
        return bool(self.supports_pickup)

    def pickup_location(self, place: dict):
        """揽收地点 → (承运商格式的地点 | None, blockers [{code, message, field?}])。
        place：{company_name, person_name, phone, address_text, postal_code, country_code, country_name}（仓库的，
        没有用公司的）。返回的地点原样存进预约记录（pickup_location），并交给 pickup_availability / schedule_pickup。
        界面按 {contact: {companyName, personName, phoneNumber}, address: {streetLines[], city, postalCode,
        countryCode}} 显示，插件尽量用这个结构。"""
        raise NotImplementedError

    def pickup_availability(self, location: dict, *, deadline) -> dict:
        """可约的揽收日与时间（只查询）→ {request_timestamp, transaction_id, alerts, options: [{pickup_date,
        schedule_day, available, cutoff_time, access_minutes, ready_times[HH:MM], latest_times[HH:MM],
        default_ready_time, default_latest_time}]}。失败抛 CarrierError。"""
        raise NotImplementedError

    def schedule_pickup(self, location: dict, pickup: dict, *, deadline) -> dict:
        """预约上门揽收。pickup：{pickup_date: date, ready: time, close: time, same_day, tz, package_count,
        total_weight_kg: Decimal, remarks, notification_email}。
        → {confirmation_code, location_code, message, alerts, transaction_id}。
        失败抛 CarrierError；响应里没有确认号等看不懂的情形抛 maybe_processed 的 CarrierError（结果不明）。"""
        raise NotImplementedError

    def cancel_pickup(self, pickup: dict, *, deadline) -> dict:
        """取消揽收预约。pickup：{confirmation_code, location_code, pickup_date: date}。
        → {reason: None | 'already_cancelled', message, alerts, transaction_id}（承运商回「已取消」也算取消成功）。
        失败抛 CarrierError；响应看不懂抛 maybe_processed 的 CarrierError。"""
        raise NotImplementedError

    # ------------------------------------------------------------------
    # 跟踪（supports_tracking = True 时实现）
    # ------------------------------------------------------------------

    def track(self, tracking_numbers, *, deadline) -> dict:
        """查一批主运单号的跟踪 → {运单号: {status, code, status_text, picked_up_at, delivered_at, estimated_delivery,
        events: [{time, code, description, location}]（新的在前）, error}}。
        - status：TRACK_* 之一；code / status_text：承运商自己的最新状态码与说明
        - picked_up_at：承运商第一次实际经手（揽收等）的时刻，还没经手为 None。WMS 据此自动完成发货
        - delivered_at：送达时刻（WMS 据此自动签收）；时刻一律是 UTC ISO 串
        - estimated_delivery：预计送达日（目的地当地日期 'YYYY-MM-DD'）
        - error：单号级的错误（查无此号等）不抛，写在这里（其余字段为空）；没返回的单号可以不出现在结果里
        请求本身失败抛 CarrierError（step = STEP_TRACK）。"""
        raise NotImplementedError
