"""承运商对接插件注册表。

内置插件在 _BUILTIN 里列出；第三方包可以在自己的 pyproject / setup 里声明 entry point：
    [project.entry-points."goodsmart_wms.carrier_integrations"]
    dhl = "my_package.dhl:DhlAdapter"
装好后重启 WMS 即可在承运商编辑页选到。插件代码（CarrierAdapter.code）重复时内置的优先。
接口见 base.CarrierAdapter。
"""
import logging
from importlib import import_module
from importlib.metadata import entry_points

from .base import CarrierAdapter

logger = logging.getLogger(__name__)

ENTRY_POINT_GROUP = 'goodsmart_wms.carrier_integrations'
_BUILTIN = (
    'warehouse.carrier.integrations.fedex:FedexAdapter',
)

_registry = None


def _load(path: str):
    module_name, _, attr = path.partition(':')
    return getattr(import_module(module_name), attr)


def _discover() -> dict:
    found = {}

    def add(cls, source):
        if not (isinstance(cls, type) and issubclass(cls, CarrierAdapter) and cls.code and cls.name):
            logger.error("carrier integration %s is not a CarrierAdapter with code and name; skipped", source)
            return
        code = str(cls.code).strip().lower()
        if code in found:
            logger.warning("carrier integration %s (%s) is already registered; %s skipped", code,
                           found[code].__module__, source)
            return
        found[code] = cls

    for path in _BUILTIN:
        add(_load(path), path)
    try:
        plugins = entry_points(group=ENTRY_POINT_GROUP)
    except Exception:  # noqa: BLE001 — 坏掉的发行包元数据不影响内置插件
        logger.exception("could not read carrier integration entry points")
        plugins = []
    for entry in plugins:
        try:
            add(entry.load(), entry.value)
        except Exception:  # noqa: BLE001 — 一个插件装坏了不影响其它
            logger.exception("could not load carrier integration %s (%s)", entry.name, entry.value)
    return found


def registry() -> dict:
    """{code: CarrierAdapter 子类}"""
    global _registry
    if _registry is None:
        _registry = _discover()
    return _registry


def adapter_class(code):
    """插件代码 → 适配器类；没有这个插件返回 None"""
    return registry().get(str(code or '').strip().lower())


def display_name(code) -> str:
    """插件代码 → 显示名（建单记录里的 carrier 列）；插件已卸载时原样返回代码"""
    cls = adapter_class(code)
    return cls.name if cls else (str(code or '') or 'carrier')


def portal_name(code) -> str:
    cls = adapter_class(code)
    return (cls.portal_name if cls else None) or f"the {display_name(code)} website"
