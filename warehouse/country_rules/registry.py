"""国家规则注册表：按 ISO 3166-1 alpha-2 取规则，没有登记的国家用默认规则（base.CountryRules）。

内置：JP（jp.py）、US（us.py）。第三方包可以在自己的 pyproject / setup 里声明 entry point：
    [project.entry-points."goodsmart_wms.country_rules"]
    kr = "my_package.kr:KoreaRules"
值为 CountryRules 子类（无参构造）或实例，code 为国家代码；与内置的重复时内置的优先。
"""
import logging
from importlib.metadata import entry_points

from warehouse.common.countries import normalize_country

from .base import CountryRules
from .jp import JapanRules
from .us import UnitedStatesRules

logger = logging.getLogger(__name__)

ENTRY_POINT_GROUP = 'goodsmart_wms.country_rules'
DEFAULT_RULES = CountryRules()
_BUILTIN = (JapanRules, UnitedStatesRules)

_registry = None


def _instance(value):
    if isinstance(value, type) and issubclass(value, CountryRules):
        return value()
    if isinstance(value, CountryRules):
        return value
    return None


def _discover() -> dict:
    found = {}

    def add(value, source):
        rules = _instance(value)
        code = normalize_country(getattr(rules, 'code', None)) if rules is not None else ''
        if rules is None or len(code) != 2:
            logger.error("country rules %s are not CountryRules with a 2-letter code; skipped", source)
            return
        if code in found:
            logger.warning("country rules for %s are already registered; %s skipped", code, source)
            return
        found[code] = rules

    for cls in _BUILTIN:
        add(cls, cls.__name__)
    try:
        plugins = entry_points(group=ENTRY_POINT_GROUP)
    except Exception:  # noqa: BLE001 — 坏掉的发行包元数据不影响内置规则
        logger.exception("could not read country rules entry points")
        plugins = []
    for entry in plugins:
        try:
            add(entry.load(), entry.value)
        except Exception:  # noqa: BLE001 — 一个规则包装坏了不影响其它
            logger.exception("could not load country rules %s (%s)", entry.name, entry.value)
    return found


def registry() -> dict:
    """{国家代码: CountryRules 实例}（不含默认规则）"""
    global _registry
    if _registry is None:
        _registry = _discover()
    return _registry


def register(rules) -> CountryRules:
    """登记一个国家的规则（测试 / 插件用）；同一国家后登记的覆盖先登记的"""
    instance = _instance(rules)
    code = normalize_country(getattr(instance, 'code', None)) if instance is not None else ''
    if instance is None or len(code) != 2:
        raise ValueError("country rules must be CountryRules with a 2-letter code")
    registry()[code] = instance
    return instance


def rules_for(country) -> CountryRules:
    """国家代码（大小写不限）→ 规则；空值 / 没有登记的国家 → 默认规则"""
    return registry().get(normalize_country(country)) or DEFAULT_RULES
