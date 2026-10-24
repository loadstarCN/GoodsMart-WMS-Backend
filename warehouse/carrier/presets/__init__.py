"""承运商文件方式的预设（doc/shipping-flow.md §6.1）：warehouse/carrier/presets/<country>/<code>.json。

预设代码 = 「<国家目录>.<文件名>」（如 jp.yamato_b2cloud），与文件里的 code 一致。开源社区可以照同一结构补充；
每份带版本（version）与来源（sources），verified = false 表示还没在承运商软件里实测过。
第一批（均未实测）：jp.yamato_b2cloud（ヤマト B2クラウド）、jp.sagawa_ehiden3（佐川 e飛伝Ⅲ）、
jp.japanpost_yupri_r（日本郵便 ゆうパックプリントR）。
读取时按 file_templates.validate_template 校验并规范化（shift_jis → cp932 等）；坏掉的预设记日志后跳过。
"""
import copy
import json
import logging
import os

from warehouse.carrier.file_templates import TemplateError, validate_template

logger = logging.getLogger(__name__)

PRESET_DIR = os.path.dirname(os.path.abspath(__file__))

_cache = None


def _load() -> dict:
    found = {}
    for country in sorted(os.listdir(PRESET_DIR)):
        folder = os.path.join(PRESET_DIR, country)
        if not os.path.isdir(folder) or country.startswith(('_', '.')):
            continue
        for file_name in sorted(os.listdir(folder)):
            if not file_name.endswith('.json'):
                continue
            code = f"{country}.{file_name[:-5]}"
            path = os.path.join(folder, file_name)
            try:
                with open(path, encoding='utf-8') as fh:
                    raw = json.load(fh)
                if raw.get('code') != code:
                    raise TemplateError('code', f"must be {code} (folder.file name)")
                found[code] = {'raw': raw, 'template': validate_template(raw)}
            except (OSError, ValueError) as exc:
                logger.error("carrier file preset %s is invalid and skipped: %s", path, exc)
    return found


def presets() -> dict:
    """{代码: {'raw': 文件内容, 'template': 规范化后的模板}}"""
    global _cache
    if _cache is None:
        _cache = _load()
    return _cache


def raw_preset(code):
    """预设文件内容（与承运商的 file_template 合并用）；没有这个预设为 None"""
    entry = presets().get(str(code or '').strip())
    return copy.deepcopy(entry['raw']) if entry else None


def preset_template(code):
    entry = presets().get(str(code or '').strip())
    return copy.deepcopy(entry['template']) if entry else None


def describe(code=None) -> list:
    """GET /warehouse/carrier/file-presets：[{code, name, country, version, notes, verified, sources,
    tracking_url_template, tracking_url_fallback, service_options, account_fields, columns: [{header, required,
    max_length}], export: {encoding, delimiter, quoting, header, length_unit, charset}, import: {encoding,
    encoding_fallbacks, header_rows, reference_column, tracking_column} | null}]"""
    result = []
    for preset_code, entry in sorted(presets().items()):
        if code is not None and preset_code != code:
            continue
        template = entry['template']
        export = template['export']
        imported = template['import']
        result.append({
            'code': preset_code,
            'name': template['name'],
            'country': template.get('country'),
            'version': template.get('version'),
            'notes': template.get('notes'),
            'verified': bool(template.get('verified')),
            'sources': template.get('sources') or [],
            'tracking_url_template': template.get('tracking_url_template'),
            'tracking_url_fallback': template.get('tracking_url_fallback'),
            'tracking_url_notes': template.get('tracking_url_notes'),
            'service_option': template.get('service_option'),
            'service_options': template['service_options'],
            'account_fields': template['account_fields'],
            'columns': [{'header': c['header'], 'required': c['required'], 'max_length': c['max_length']}
                        for c in export['columns']],
            'export': {k: export.get(k) for k in ('encoding', 'delimiter', 'quoting', 'header', 'line_ending',
                                                  'length_unit', 'charset')},
            'import': ({k: imported.get(k) for k in ('encoding', 'encoding_fallbacks', 'header_rows',
                                                     'reference_column', 'tracking_column', 'tracking_normalize')}
                       if imported else None),
        })
    return result
