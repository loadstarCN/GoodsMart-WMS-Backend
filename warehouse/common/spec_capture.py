"""收货规格采集的取值与设置校验（仓库默认规则、收货通知的 spec_capture 共用；业务逻辑在 warehouse/asn/spec_capture.py）

- 采集项 SPEC_CAPTURE_FIELDS：weight = 重量（kg）；dimensions = 长宽高（mm，三项一起）；origin_country = 原产国
- 采集模式 SPEC_CAPTURE_MODES：missing = 只补商品资料里缺的项（默认）；remeasure = 全部重新测量
- 仓库的提示 / 拦截 SPEC_CAPTURE_ENFORCEMENTS：warn = 分拣完成照常、响应里带未采集清单（默认）；block = 409 17021
"""
from extensions.error import BadRequestException

SPEC_CAPTURE_FIELDS = ('weight', 'dimensions', 'origin_country')
SPEC_CAPTURE_MODES = ('missing', 'remeasure')
SPEC_CAPTURE_ENFORCEMENTS = ('warn', 'block')
DEFAULT_SPEC_CAPTURE_MODE = 'missing'
DEFAULT_SPEC_CAPTURE_ENFORCEMENT = 'warn'

# 业务码（extensions/error.py、README 登记）
SPEC_CAPTURE_SETTINGS_INVALID = 17020   # 400 采集设置不合法（收货通知的 spec_capture、仓库默认规则）
SPEC_CAPTURE_BLOCKED = 17021            # 409 强制模式：分拣完成时还有未采集、也没填跳过原因的行
SPEC_CAPTURE_REQUEST_INVALID = 17022    # 400 采集请求不合法
SPEC_CAPTURE_NOT_ALLOWED = 17023        # 409 收货通知当前状态不能采集（只有 received / completed 可以）

# 仓库新建 / 修改可写的列
WAREHOUSE_SPEC_FIELDS = ('default_spec_capture_fields', 'default_spec_capture_mode', 'spec_capture_enforcement')


def normalize_spec_fields(value, field='spec_capture.fields') -> list:
    """采集项：null / 列表，元素是 SPEC_CAPTURE_FIELDS 之一（大小写、首尾空白不计）；去重并按固定顺序返回。
    null = 空列表（不采集）。不合法 → 400 17020（field = 字段名）"""
    if value is None:
        return []
    if not isinstance(value, (list, tuple)) or not all(isinstance(item, str) for item in value):
        raise BadRequestException(f"{field} must be a list of {', '.join(SPEC_CAPTURE_FIELDS)}",
                                  SPEC_CAPTURE_SETTINGS_INVALID, field)
    names = {item.strip().lower() for item in value}
    unknown = sorted(names - set(SPEC_CAPTURE_FIELDS))
    if unknown:
        raise BadRequestException(
            f"{field} has unknown item(s) {', '.join(unknown)}; allowed: {', '.join(SPEC_CAPTURE_FIELDS)}",
            SPEC_CAPTURE_SETTINGS_INVALID, field, details={'allowed': list(SPEC_CAPTURE_FIELDS), 'unknown': unknown})
    return [name for name in SPEC_CAPTURE_FIELDS if name in names]


def _choice(value, choices, field):
    if isinstance(value, str) and value.strip().lower() in choices:
        return value.strip().lower()
    raise BadRequestException(f"{field} must be one of {', '.join(choices)}", SPEC_CAPTURE_SETTINGS_INVALID, field,
                              details={'allowed': list(choices)})


def normalize_spec_mode(value, field='spec_capture.mode') -> str:
    """采集模式 missing / remeasure；不合法 → 400 17020"""
    return _choice(value, SPEC_CAPTURE_MODES, field)


def normalize_spec_enforcement(value, field='spec_capture_enforcement') -> str:
    """提示 / 拦截 warn / block；不合法 → 400 17020"""
    return _choice(value, SPEC_CAPTURE_ENFORCEMENTS, field)


def warehouse_spec_settings(data: dict) -> dict:
    """仓库新建 / 修改请求里的规格采集默认规则 → {列: 值}，只含请求里带了的列。
    default_spec_capture_fields 为 null = 不采集；模式 / 提示拦截为 null 视为没传（新建时取列默认值，修改时不变）"""
    settings = {}
    if 'default_spec_capture_fields' in data:
        settings['default_spec_capture_fields'] = normalize_spec_fields(
            data['default_spec_capture_fields'], 'default_spec_capture_fields')
    if data.get('default_spec_capture_mode') is not None:
        settings['default_spec_capture_mode'] = normalize_spec_mode(
            data['default_spec_capture_mode'], 'default_spec_capture_mode')
    if data.get('spec_capture_enforcement') is not None:
        settings['spec_capture_enforcement'] = normalize_spec_enforcement(data['spec_capture_enforcement'])
    return settings
