"""FedEx Pickup Request API 的请求组装与响应解析（纯函数，不碰数据库、不联网）。

字段依据 FedEx 开发者门户的 Pickup Request API JSON 示例集（Postman collection）与字段说明，以及正式环境
查可约时间的实际返回（2026-10-21，京都仓）：
- 查可约时间 POST /pickup/v1/pickups/availabilities：pickupAddress、pickupRequestType [SAME_DAY, FUTURE_DAY]、
  carriers [FDXE]、countryRelationship（不用带账号）。返回 output.options[]：pickupDate、scheduleDay、available、
  cutOffTime（准备好时间不能晚于它）、accessTime {hours, minutes}（准备好到最晚揽收之间至少这么久）、
  readyTimeOptions / latestTimeOptions（可选时间，当地时间 HH:MM:SS）、defaultReadyTime / defaultLatestTimeOptions。
  当天过了截止时间就只返回之后的营业日（例：20:30 查，返回次日、后天两个 FUTURE_DAY）。
- 预约 POST /pickup/v1/pickups：associatedAccountNumber {value}、originDetail {pickupLocation {contact, address},
  readyDateTimestamp, customerCloseTime, pickupDateType}、carrierCode FDXE、countryRelationships、packageCount、
  totalWeight {units, value}、remarks、pickupNotificationDetail。返回 output.pickupConfirmationCode 与
  output.location（FedEx Express 站点代码，取消时要用）。
  readyDateTimestamp 带揽收地点的时区偏移（示例集里日本的写法 2023-07-07T09:00:00+09:00），不用 Z，免得被当成 UTC；
  customerCloseTime 是当地时间 HH:MM:SS。
- 取消 PUT /pickup/v1/pickups/cancel：associatedAccountNumber {value}、pickupConfirmationCode、carrierCode、
  scheduledDate（揽收日）、location。
- 地址与建运单的发件人地址同一套（split_address_text）；日本地址不带 stateOrProvinceCode（与 Ship API 一致）。
- 约好后不能改，只能取消重约。
"""
from datetime import date, datetime, time
from decimal import Decimal, ROUND_CEILING

from .shipment import MAX_COMPANY_NAME, MAX_EMAIL, MAX_PERSON_NAME, _alerts, _clip, phone_digits

CARRIER_CODE = 'FDXE'                       # FedEx Express
COUNTRY_RELATIONSHIP = 'INTERNATIONAL'      # 揽收的都是海外件
SAME_DAY = 'SAME_DAY'
FUTURE_DAY = 'FUTURE_DAY'
MAX_REMARKS = 60                            # 给快递员的留言（文档没写上限，取 FedEx 网站集荷备注的长度）
PHONE_DIGITS_RANGE = (8, 13)                # 揽收联系人电话位数（文档：最少 8、最多 13）
NOTIFICATION_FORMAT = 'HTML'


class PickupRequestError(ValueError):
    """请求数据放不进 FedEx 格式（如电话位数不对），调用方转成前置条件 / 400"""


def parse_hhmm(value):
    """'HH:MM' / 'HH:MM:SS' → time；不合法返回 None"""
    text = str(value or '').strip()
    for fmt in ('%H:%M', '%H:%M:%S'):
        try:
            return datetime.strptime(text, fmt).time()
        except ValueError:
            continue
    return None


def fedex_time(value: time) -> str:
    return value.strftime('%H:%M:%S')


def ready_timestamp(pickup_date: date, ready: time, tz) -> str:
    """揽收地点当地的「准备好」时刻，带时区偏移：2026-10-22T12:00:00+09:00"""
    return datetime.combine(pickup_date, ready, tzinfo=tz).isoformat(timespec='seconds')


def total_weight_kg(value) -> float:
    """揽收总重量（kg）：向上取一位小数，最小 0.1"""
    number = Decimal(str(value or 0)).quantize(Decimal('0.1'), rounding=ROUND_CEILING)
    return float(max(number, Decimal('0.1')))


def pickup_contact(company_name, person_name, phone) -> dict:
    digits = phone_digits(phone)
    low, high = PHONE_DIGITS_RANGE
    if not low <= len(digits) <= high:
        raise PickupRequestError(f"phone number must have {low}-{high} digits (current: {len(digits)})")
    contact = {'phoneNumber': digits}
    person = _clip(person_name, MAX_PERSON_NAME)
    company = _clip(company_name, MAX_COMPANY_NAME)
    if person:
        contact['personName'] = person
    if company:
        contact['companyName'] = company
    return contact


def build_availability_request(address: dict) -> dict:
    """查当天与之后营业日的可约时间（不带日期 / 时间：FedEx 返回各天的截止时间与可选时间）"""
    return {
        'pickupAddress': address,
        'pickupRequestType': [SAME_DAY, FUTURE_DAY],
        'carriers': [CARRIER_CODE],
        'countryRelationship': COUNTRY_RELATIONSHIP,
    }


def _access_minutes(value):
    """accessTime：正式环境回 {hours, minutes}；也认 ISO 时长 PT2H30M"""
    if isinstance(value, dict):
        try:
            return int(value.get('hours') or 0) * 60 + int(value.get('minutes') or 0)
        except (TypeError, ValueError):
            return None
    text = str(value or '').strip().upper()
    if text.startswith('PT'):
        hours = minutes = 0
        number = ''
        for ch in text[2:]:
            if ch.isdigit():
                number += ch
            elif ch == 'H' and number:
                hours, number = int(number), ''
            elif ch == 'M' and number:
                minutes, number = int(number), ''
        return hours * 60 + minutes
    return None


def _hhmm(value):
    parsed = parse_hhmm(value)
    return parsed.strftime('%H:%M') if parsed else None


def _times(values) -> list:
    return [t for t in (_hhmm(v) for v in values or []) if t]


def parse_availability_response(body: dict) -> dict:
    """→ {request_timestamp, options: [{pickup_date, schedule_day, available, cutoff_time, access_minutes,
    ready_times, latest_times, default_ready_time, default_latest_time, residential_available}]}（时间 HH:MM）"""
    output = (body or {}).get('output') or {}
    options = []
    for item in output.get('options') or []:
        if not isinstance(item, dict) or not item.get('pickupDate'):
            continue
        if item.get('carrier') and item.get('carrier') != CARRIER_CODE:
            continue
        options.append({
            'pickup_date': str(item.get('pickupDate'))[:10],
            'schedule_day': item.get('scheduleDay'),
            'available': bool(item.get('available')),
            'cutoff_time': _hhmm(item.get('cutOffTime')),
            'access_minutes': _access_minutes(item.get('accessTime')),
            'ready_times': _times(item.get('readyTimeOptions')),
            'latest_times': _times(item.get('latestTimeOptions')),
            'default_ready_time': _hhmm(item.get('defaultReadyTime')),
            'default_latest_time': _hhmm(item.get('defaultLatestTimeOptions') or item.get('defaultLatestTime')),
            'residential_available': item.get('residentialAvailable'),
        })
    options.sort(key=lambda o: o['pickup_date'])
    return {
        'request_timestamp': output.get('requestTimestamp'),
        'options': options,
        'alerts': _alerts(output, body),
    }


def build_create_request(*, account_number, contact: dict, address: dict, pickup_date: date, ready: time,
                         close: time, same_day: bool, tz, package_count: int, total_weight, remarks=None,
                         notification_email=None, notification_locale='en_US') -> dict:
    """组装 POST /pickup/v1/pickups 请求体"""
    payload = {
        'associatedAccountNumber': {'value': account_number},
        'originDetail': {
            'pickupLocation': {'contact': contact, 'address': address},
            'readyDateTimestamp': ready_timestamp(pickup_date, ready, tz),
            'customerCloseTime': fedex_time(close),
            'pickupDateType': SAME_DAY if same_day else FUTURE_DAY,
        },
        'carrierCode': CARRIER_CODE,
        'countryRelationships': COUNTRY_RELATIONSHIP,
        'packageCount': int(package_count),
        'totalWeight': {'units': 'KG', 'value': total_weight_kg(total_weight)},
    }
    remarks = _clip(remarks, MAX_REMARKS)
    if remarks:
        payload['remarks'] = remarks
    email = _clip(notification_email, MAX_EMAIL)
    if email:
        payload['pickupNotificationDetail'] = {
            'emailDetails': [{'address': email, 'locale': notification_locale}],
            'format': NOTIFICATION_FORMAT,
        }
    return payload


def parse_create_response(body: dict) -> dict:
    """→ {confirmation_code, location, message, alerts}；没有确认号 → ValueError（结果不明）"""
    output = (body or {}).get('output') or {}
    code = output.get('pickupConfirmationCode')
    if code is None or str(code).strip() == '':
        raise ValueError("FedEx pickup response has no pickupConfirmationCode")
    return {
        'confirmation_code': str(code).strip(),
        'location': (str(output.get('location')).strip() or None) if output.get('location') else None,
        'message': output.get('message'),
        'alerts': _alerts(output, body),
    }


def build_cancel_request(*, account_number, confirmation_code, pickup_date: date, location=None) -> dict:
    payload = {
        'associatedAccountNumber': {'value': account_number},
        'pickupConfirmationCode': confirmation_code,
        'carrierCode': CARRIER_CODE,
        'scheduledDate': pickup_date.isoformat(),
    }
    if location:
        payload['location'] = location
    return payload


def parse_cancel_response(body: dict) -> dict:
    """→ {confirmation_code, message, alerts}；200 但没有 output → ValueError（结果不明）"""
    output = (body or {}).get('output')
    if not isinstance(output, dict):
        raise ValueError("FedEx cancel pickup response has no output")
    return {
        'confirmation_code': output.get('pickupConfirmationCode'),
        'message': output.get('cancelConfirmationMessage') or output.get('message'),
        'alerts': _alerts(output, body),
    }
