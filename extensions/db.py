import datetime as _dt
from zoneinfo import ZoneInfo as _ZoneInfo

from flask import current_app as _current_app, has_app_context as _has_app_context
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from sqlalchemy import types as _types
from extensions.error import NotFoundException

db = SQLAlchemy()          # 数据库扩展
migrate = Migrate()        # 数据库迁移扩展

_UTC = _dt.timezone.utc
# 不能存时区的方言：存不带时区的 UTC，读出按 UTC 补上
_NAIVE_UTC_DIALECTS = ('sqlite', 'mysql', 'mariadb')


def legacy_timezone():
    """旧口径时区（WMS_LEGACY_TIMEZONE → WMS_TIMEZONE → UTC，见 config.py）：不带时区的输入、PostgreSQL 上还没转换的
    timestamp 列读出的值都按它的墙上时间解释。有应用上下文时取 app.config（测试可改），否则取 config 模块的值。"""
    name = None
    if _has_app_context():
        name = _current_app.config.get('WMS_LEGACY_TIMEZONE')
    if not name:
        import config as _config
        name = _config.WMS_LEGACY_TIMEZONE
    try:
        return _ZoneInfo(name)
    except Exception:  # noqa: BLE001 — 启动时已校验；万一测试里配错按 UTC
        return _UTC


def utc_now():
    """当前时刻（带时区的 UTC）。warehouse 代码用 warehouse/common/clock.now()；模型默认值与 system 模块用它
    （避免 import warehouse 包造成循环引用）"""
    return _dt.datetime.now(_UTC)


def as_utc(value):
    """datetime → 带时区的 UTC：带时区的换算；不带时区的按旧口径时区（legacy_timezone）的墙上时间解释"""
    if value is None:
        return None
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        value = value.replace(tzinfo=legacy_timezone())
    return value.astimezone(_UTC)


class UTCDateTime(_types.TypeDecorator):
    """时刻列（doc/timezone-utc.md §1）。Python 侧一律是带时区的 UTC datetime。

    - 写入：带时区的值换成 UTC；不带时区的值按旧口径时区（WMS_LEGACY_TIMEZONE）的墙上时间解释再换成 UTC
      （兼容还没改完的调用方）；只给 date 的按旧口径时区当天 00:00；ISO 8601 字符串先解析。
      PostgreSQL 绑定带时区的 UTC 值：timestamptz 列原样存；还没转换的 timestamp 列由数据库按会话时区（config.py
      把 PostgreSQL 连接的会话时区设成旧口径时区）换成墙上时间，与读出时的解释一致。
      SQLite / MySQL 不能存时区：存不带时区的 UTC。
    - 读出：带时区的值（PostgreSQL timestamptz）换成 UTC；不带时区的值在 PostgreSQL 上来自还没转换的 timestamp 列，
      按旧口径时区解释；SQLite / MySQL 上就是 UTC。
    所以代码在生产库转换（flask wms convert-utc / scripts/manual/migrate_utc.py）前后都能正确工作。
    """
    impl = _types.DateTime
    cache_ok = True

    def __init__(self, *args, **kwargs):
        kwargs.setdefault('timezone', True)
        super().__init__(*args, **kwargs)

    @staticmethod
    def _coerce(value):
        if isinstance(value, str):
            text = value.strip()
            if not text:
                return None
            value = _dt.datetime.fromisoformat(text[:-1] + '+00:00' if text[-1] in 'Zz' else text)
        elif isinstance(value, _dt.date) and not isinstance(value, _dt.datetime):
            value = _dt.datetime.combine(value, _dt.time())
        if not isinstance(value, _dt.datetime):
            raise TypeError(f'UTCDateTime expects a datetime, got {type(value).__name__}')
        return as_utc(value)

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        value = self._coerce(value)
        if value is None:
            return None
        if dialect.name in _NAIVE_UTC_DIALECTS:
            return value.replace(tzinfo=None)
        return value

    def process_literal_param(self, value, dialect):
        return self.process_bind_param(value, dialect)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        if isinstance(value, str):          # 个别驱动 / 原生 SQL 可能回字符串
            text = value.strip()
            value = _dt.datetime.fromisoformat(text[:-1] + '+00:00' if text[-1:] in ('Z', 'z') else text)
        if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
            if dialect.name == 'postgresql':
                # 还没转换成 timestamptz 的 timestamp 列：旧口径时区的墙上时间
                return value.replace(tzinfo=legacy_timezone()).astimezone(_UTC)
            return value.replace(tzinfo=_UTC)
        return value.astimezone(_UTC)


def get_object_or_404(model, object_id, error_message=None):
    """
    安全获取对象或抛出 NotFound 异常
    """
    obj = db.session.get(model, object_id)
    if not obj:
        if not error_message:
            error_message = f"{model.__name__} with id {object_id} not found"
        raise NotFoundException(error_message,13001)
    return obj

def get_object_or_none(model, object_id):
    """
    安全获取对象，找不到时返回 None
    """
    return db.session.get(model, object_id)
