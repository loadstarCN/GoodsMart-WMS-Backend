"""FedEx Track API：请求组装、响应解析（纯函数，HTTP 在 client.py）。

状态码按 FedEx Track API 的 latestStatusDetail.derivedCode（没有就 code），归到 base.TRACK_*：
拿不准含义的码不放进在途 / 送达表 —— 落到「认不出」（unknown），不会触发自动发货 / 签收，比把退件当成在途安全。
"""
from datetime import datetime, timezone

from ..base import (
    TRACK_DELIVERED, TRACK_EXCEPTION, TRACK_IN_TRANSIT, TRACK_LABEL_CREATED, TRACK_OUT_FOR_DELIVERY, TRACK_UNKNOWN,
)

# 一次请求最多 30 个单号（FedEx Track API 上限）
MAX_NUMBERS_PER_REQUEST = 30
MAX_EVENTS = 30

DELIVERED_CODES = frozenset({'DL'})
# 派送中 / 到网点等收件人自取（HL = Hold at Location，HP = Ready for recipient pickup）
OUT_FOR_DELIVERY_CODES = frozenset({'OD', 'HL', 'HP'})
# 已揽收、在 FedEx 网络里移动（含通关中 CP・已通关 CC；OW = On the way，在途时 latestStatusDetail 的 derivedCode）
IN_TRANSIT_CODES = frozenset({
    'PU', 'PX', 'IT', 'IX', 'OW', 'AR', 'AF', 'AA', 'AC', 'AD', 'AO', 'AX', 'DP', 'LO',
    'ED', 'EO', 'FD', 'OF', 'PF', 'PL', 'PM', 'SF', 'SP', 'TR', 'CC', 'CP', 'CH',
})
# 不说明货在哪：面单已建・等揽收（IN = Initiated / OC = 运单信息已发给 FedEx / LP・DS・EP・AP・DR 揽收前），
# 退货面单、改派请求之类的通知（RP・RG・RD・RR・RM・RC）。已揽收过的按在途
NEUTRAL_CODES = frozenset({'IN', 'OC', 'LP', 'DS', 'EP', 'AP', 'DR', 'RP', 'RG', 'RD', 'RR', 'RM', 'RC'})
# 异常：DE 配达异常 / SE 运输异常 / CA 取消 / RS 退回寄件人 / CD 通关延误 / DY・DD 延误 / PD 揽收延误
EXCEPTION_CODES = frozenset({'DE', 'SE', 'CA', 'RS', 'CD', 'DY', 'DD', 'PD'})
# 说明货已经到了 FedEx 手上的扫描（第一条的时刻 = picked_up_at）
HANDLED_CODES = IN_TRANSIT_CODES | OUT_FOR_DELIVERY_CODES | DELIVERED_CODES


def build_track_request(numbers) -> dict:
    return {
        'includeDetailedScans': True,
        'trackingInfo': [{'trackingNumberInfo': {'trackingNumber': n}} for n in numbers],
    }


def _date_only(value):
    """'YYYY-MM-DD'（纯日期）原样返回，别的返回 None"""
    text = str(value).strip()
    if len(text) != 10:
        return None
    try:
        datetime.strptime(text, '%Y-%m-%d')
    except ValueError:
        return None
    return text


def _to_utc_iso(value):
    """FedEx 带时区偏移的时刻串 → UTC ISO 串；纯日期原样返回（补成 00:00 就成了并不存在的时刻）；解析不了原样返回"""
    if not value:
        return None
    if _date_only(value):
        return _date_only(value)
    try:
        moment = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    except ValueError:
        return str(value)
    if moment.tzinfo is None:
        return moment.isoformat()
    return moment.astimezone(timezone.utc).isoformat()


def _local_date(value):
    """预计送达是目的地当地的日期（串里的偏移就是当地时区）：取原串的日期部分，不换算成 UTC"""
    if not value:
        return None
    return _date_only(str(value).strip()[:10])


def _location_text(location):
    location = location if isinstance(location, dict) else {}
    parts = [location.get('city'), location.get('stateOrProvinceCode'), location.get('countryCode')]
    return ', '.join(p for p in parts if p) or None


def status_of(code, picked_up: bool) -> str:
    """FedEx 状态码 → TRACK_*"""
    if code in DELIVERED_CODES:
        return TRACK_DELIVERED
    if code in OUT_FOR_DELIVERY_CODES:
        return TRACK_OUT_FOR_DELIVERY
    if code in IN_TRANSIT_CODES:
        return TRACK_IN_TRANSIT
    if code in NEUTRAL_CODES:
        return TRACK_IN_TRANSIT if picked_up else TRACK_LABEL_CREATED
    if code in EXCEPTION_CODES:
        return TRACK_EXCEPTION
    return TRACK_UNKNOWN


def normalize_track_result(result: dict) -> dict:
    """一个 trackResult → CarrierAdapter.track 的单号结果"""
    error = result.get('error')
    if error:
        error = error if isinstance(error, dict) else {}
        return {'status': None, 'code': None, 'status_text': None, 'picked_up_at': None, 'delivered_at': None,
                'estimated_delivery': None, 'events': [],
                'error': f"{error.get('code')}: {error.get('message')}"}
    latest = result.get('latestStatusDetail') or {}
    code = latest.get('derivedCode') or latest.get('code')
    times = {d.get('type'): d.get('dateTime') for d in result.get('dateAndTimes') or [] if isinstance(d, dict)}
    estimated = times.get('ESTIMATED_DELIVERY')
    if not estimated:
        window = (result.get('estimatedDeliveryTimeWindow') or {}).get('window') or {}
        estimated = window.get('ends') or window.get('begins')
    events = [{
        'time': _to_utc_iso(e.get('date')),
        'code': e.get('derivedStatusCode') or e.get('eventType'),
        'description': e.get('eventDescription'),
        'location': _location_text(e.get('scanLocation')),
    } for e in result.get('scanEvents') or [] if isinstance(e, dict)]
    events.sort(key=lambda e: e['time'] or '', reverse=True)       # 新的在前
    handled = sorted(e['time'] for e in events if e['code'] in HANDLED_CODES and e['time'])
    picked_up_at = handled[0] if handled else None
    if picked_up_at is None and code in HANDLED_CODES:
        # 有的结果不带扫描记录：最新状态已是在途 / 送达，时刻取不到
        picked_up_at = _to_utc_iso(times.get('ACTUAL_PICKUP'))
    return {
        'status': status_of(code, picked_up_at is not None or code in HANDLED_CODES),
        'code': code,
        'status_text': latest.get('statusByLocale') or latest.get('description'),
        'picked_up_at': picked_up_at,
        'delivered_at': _to_utc_iso(times.get('ACTUAL_DELIVERY')),
        'estimated_delivery': _local_date(estimated),
        'events': events[:MAX_EVENTS],
        'error': None,
    }


def _newest_scan(result: dict) -> str:
    events = result.get('events') or []
    return max((e['time'] or '' for e in events), default='')


def parse_track_response(body: dict) -> dict:
    """Track API 响应 → {运单号: normalize_track_result(...)}。
    同一单号有多条结果（FedEx 回收再用的单号，新旧包裹都在）时取最新扫描最晚的那条；WMS 另按建单时刻排除旧包裹"""
    results = {}
    for complete in (body.get('output') or {}).get('completeTrackResults') or []:
        number = str(complete.get('trackingNumber') or '').strip()
        track_results = [r for r in complete.get('trackResults') or [] if isinstance(r, dict)]
        if not number or not track_results:
            continue
        candidates = [normalize_track_result(r) for r in track_results]
        if number in results:
            candidates.append(results[number])
        ok = [c for c in candidates if not c.get('error')]
        results[number] = max(ok, key=_newest_scan) if ok else candidates[0]
    return results
