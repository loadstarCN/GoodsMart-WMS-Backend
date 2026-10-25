"""时钟与时区（doc/timezone-utc.md）。

- now()：当前时刻，带时区的 UTC。要存或要比较的时刻一律用它，不用不带时区的 datetime.now() / datetime.utcnow()。
  （耗时 / 超时预算用 time.monotonic()，不受影响。）
- 仓库时区：warehouse_timezone(warehouse) = 仓库自己设的 timezone → WMS_DEFAULT_TIMEZONE → WMS_TIMEZONE → UTC。
- 业务日期（实际发货日、单证日期、揽收日、「预计日期已过」、自动视为签收……）按单据所属仓库的时区：
  local_today / local_date / to_local / local_midnight。
- 用户输入的时刻：parse_input_datetime（带偏移的按偏移；不带的按仓库时区；只给日期的按仓库当地 00:00）。
- 接口输出：时刻字段一律 UTC（+00:00，utc_iso）；webhook 的 event_time 用 local_iso（同一时刻、仓库时区的偏移）。

参数 where 可以是：仓库对象、仓库 id、带 warehouse / warehouse_id 的单据（DN、ASN、任务……；发货任务等经 dn / asn
找仓库）、tzinfo、IANA 时区名，或 None（默认时区）。
"""
from datetime import date, datetime, time, timedelta, timezone, tzinfo
from zoneinfo import ZoneInfo

from flask import current_app, has_app_context

from config import is_valid_timezone
from extensions.db import as_utc, utc_now
from extensions.error import BadRequestException

UTC = timezone.utc
TIMEZONE_INVALID = 14022        # 400 仓库时区不是合法的 IANA 名
TIMEZONE_MAX_LENGTH = 64


def now() -> datetime:
    """当前时刻（带时区的 UTC）"""
    return utc_now()


def zone(name):
    """IANA 名 → ZoneInfo；不合法返回 None"""
    if isinstance(name, str) and is_valid_timezone(name.strip()):
        return ZoneInfo(name.strip())
    return None


def _config_value(key) -> str:
    if has_app_context():
        value = current_app.config.get(key)
    else:
        import config
        value = getattr(config, key, None)
    return value.strip() if isinstance(value, str) else ''


def default_timezone_name() -> str:
    """没设时区的仓库（以及没有仓库时）用的时区：WMS_DEFAULT_TIMEZONE → WMS_TIMEZONE → UTC"""
    for key in ('WMS_DEFAULT_TIMEZONE', 'WMS_TIMEZONE'):
        name = _config_value(key)
        if name and is_valid_timezone(name):
            return name
    return 'UTC'


def legacy_timezone_name() -> str:
    """旧数据的墙上时间口径：WMS_LEGACY_TIMEZONE → WMS_TIMEZONE → UTC（见 config.py）"""
    for key in ('WMS_LEGACY_TIMEZONE', 'WMS_TIMEZONE'):
        name = _config_value(key)
        if name and is_valid_timezone(name):
            return name
    return 'UTC'


def _warehouse_of(where):
    """仓库对象 / 仓库 id / 带 warehouse（或 warehouse_id）的单据 → 仓库对象；找不到返回 None"""
    if where is None or isinstance(where, bool):
        return None
    if isinstance(where, int):
        from extensions.db import db
        from warehouse.warehouse.models import Warehouse
        return db.session.get(Warehouse, where)
    if hasattr(where, 'timezone') and hasattr(where, 'company_id'):
        return where                                    # 仓库本身
    warehouse = getattr(where, 'warehouse', None)
    if warehouse is not None and not isinstance(warehouse, (int, str)):
        return warehouse
    warehouse_id = getattr(where, 'warehouse_id', None)
    if isinstance(warehouse_id, int) and not isinstance(warehouse_id, bool):
        return _warehouse_of(warehouse_id)
    for parent in ('dn', 'asn'):                         # 发货任务等：经所属的 DN / ASN 找仓库
        document = getattr(where, parent, None)
        if document is not None and not isinstance(document, (int, str)):
            return _warehouse_of(document)
    return None


def configured_timezone(warehouse):
    """仓库自己设的时区（合法时）；没设返回 None"""
    name = getattr(warehouse, 'timezone', None) if warehouse is not None else None
    if isinstance(name, str) and is_valid_timezone(name.strip()):
        return name.strip()
    return None


def warehouse_timezone_name(warehouse=None) -> str:
    """仓库（或单据所属仓库）的生效时区名：仓库设了的 → 默认时区"""
    return configured_timezone(_warehouse_of(warehouse)) or default_timezone_name()


def warehouse_timezone(warehouse=None) -> ZoneInfo:
    """仓库（或单据所属仓库）的生效时区"""
    return ZoneInfo(warehouse_timezone_name(warehouse))


def resolve_timezone(where=None) -> tzinfo:
    """where（见模块说明）→ tzinfo；不合法的时区名按默认时区"""
    if isinstance(where, tzinfo):
        return where
    if isinstance(where, str):
        return zone(where) or ZoneInfo(default_timezone_name())
    return warehouse_timezone(where)


def to_local(dt, where=None):
    """时刻 → where 时区的带时区 datetime（不带时区的按旧口径时区解释，同 UTCDateTime）；None → None"""
    if dt is None:
        return None
    return as_utc(dt).astimezone(resolve_timezone(where))


def local_now(where=None) -> datetime:
    """where 时区的当前时刻"""
    return now().astimezone(resolve_timezone(where))


def local_today(where=None) -> date:
    """where（仓库）当地的今天"""
    return local_now(where).date()


def local_date(dt, where=None):
    """时刻在 where（仓库）当地是哪一天；None → None"""
    return to_local(dt, where).date() if dt is not None else None


def local_today_expression(warehouse_id_column):
    """SQL 里「各仓库当地的今天」（列表的阶段判定等跨仓库的查询用）：按 warehouse_id 列取。
    只为设了时区、且今天与默认时区不同的仓库加 CASE 分支；都相同时就是一个日期常量。"""
    from sqlalchemy import case, literal
    from extensions.db import db
    from warehouse.warehouse.models import Warehouse
    default_today = local_today(None)
    current = now()
    branches = {}
    rows = db.session.query(Warehouse.id, Warehouse.timezone).filter(Warehouse.timezone.isnot(None)).all()
    for warehouse_id, name in rows:
        tz = zone(name)
        if tz is not None and current.astimezone(tz).date() != default_today:
            branches[warehouse_id] = current.astimezone(tz).date()
    if not branches:
        return literal(default_today)
    return case(branches, value=warehouse_id_column, else_=literal(default_today))


def local_midnight(day: date, where=None) -> datetime:
    """where（仓库）当地 day 的 00:00（带时区的 UTC）"""
    return datetime.combine(day, time(), tzinfo=resolve_timezone(where)).astimezone(UTC)


def local_day_range(day: date, where=None):
    """where 当地 day 这一天的 [开始, 结束)（带时区的 UTC），用于按当地日期筛选时刻列"""
    return local_midnight(day, where), local_midnight(day + timedelta(days=1), where)


def stats_now(filters=None) -> datetime:
    """月度统计用的「现在」：筛了单个仓库（filters['warehouse_id']）按该仓库时区，否则默认时区。
    （月份分组由数据库按会话时区提取：PostgreSQL 为旧口径时区，SQLite 为 UTC；只在月初 / 月末的几个小时有差别）"""
    warehouse_id = (filters or {}).get('warehouse_id') if isinstance(filters, dict) else None
    return local_now(warehouse_id if isinstance(warehouse_id, int) and not isinstance(warehouse_id, bool) else None)


def filter_time_range(query, column, start=None, end=None, where=None):
    """列表按时刻列筛选（start_time / end_time 之类）：
    - date（只给日期）：where（所筛仓库）当地那一天；end 包含那一整天（< 次日 00:00）
    - datetime / ISO 字符串：不带时区的按 where 的时区（parse_input_datetime）
    """
    if start:
        query = query.filter(column >= parse_input_datetime(start, where))
    if end:
        if isinstance(end, date) and not isinstance(end, datetime):
            query = query.filter(column < local_midnight(end + timedelta(days=1), where))
        else:
            query = query.filter(column <= parse_input_datetime(end, where))
    return query


def parse_input_datetime(value, where=None):
    """用户输入的时刻 → 带时区的 UTC datetime；None / 空串 → None；格式不对 ValueError（调用方转成各自的业务码）。

    - 带偏移的 ISO 8601（含 Z）按其偏移；不带偏移的按 where 的时区（单据所属仓库的时区）解释
    - 只给日期（YYYY-MM-DD，如 PDA 签收）→ 仓库当地当天 00:00
    - datetime / date 对象同上（不带时区的 datetime 按仓库时区）
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        moment = value
    elif isinstance(value, date):
        moment = datetime.combine(value, time())
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if len(text) == 10 and text[4] == '-' and text[7] == '-':
            moment = datetime.combine(date.fromisoformat(text), time())
        else:
            if text[-1:] in ('Z', 'z'):
                text = text[:-1] + '+00:00'
            moment = datetime.fromisoformat(text)
    else:
        raise ValueError(f'not a datetime: {value!r}')
    if moment.tzinfo is None or moment.tzinfo.utcoffset(moment) is None:
        moment = moment.replace(tzinfo=resolve_timezone(where))
    return moment.astimezone(UTC)


def utc_iso(dt, timespec='auto'):
    """接口输出的时刻：UTC 的 ISO 8601（+00:00）；None → None"""
    return as_utc(dt).isoformat(timespec=timespec) if dt is not None else None


def local_iso(dt, where=None, timespec='seconds'):
    """同一时刻、where（仓库）时区偏移的 ISO 8601（webhook 的 event_time 用）；None → None"""
    return to_local(dt, where).isoformat(timespec=timespec) if dt is not None else None


def normalize_timezone_input(value, field='timezone'):
    """仓库时区的输入：None / 空串 → None（用默认时区）；否则必须是合法的 IANA 名（如 Asia/Tokyo），不合法 400 14022"""
    if value is None:
        return None
    if not isinstance(value, str):
        raise BadRequestException(f"{field} must be an IANA time zone name such as Asia/Tokyo",
                                  TIMEZONE_INVALID, field=field)
    name = value.strip()
    if not name:
        return None
    if len(name) > TIMEZONE_MAX_LENGTH or not is_valid_timezone(name):
        raise BadRequestException(f"{field} must be an IANA time zone name such as Asia/Tokyo",
                                  TIMEZONE_INVALID, field=field)
    return name
