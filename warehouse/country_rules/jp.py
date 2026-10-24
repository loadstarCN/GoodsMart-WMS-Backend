"""日本的国家规则（doc/shipping-flow.md §6.3）。

- 报关：9 位输出统计品目番号（jp_export_code，輸出申告用）的格式检查（JP_EXPORT_CODE_MISMATCH）；
  货值（FOB，不含运费 / 保险费）超过 20 万円时缺统计番号的行给警告（JP_EXPORT_CODE_MISSING）；
  报关币种不是 JPY 时没法自动判断是否超过 20 万円，有行没填统计番号就请人工确认（JP_EXPORT_VALUE_UNVERIFIED）。
  原来在 warehouse/dn/customs_services.py 里，搬过来后行为与文案不变；只在发货国（仓库 → 公司的国家）是日本时参与。
- 发货人税号的默认标签：Corporate No.（法人番号）
- 地址：〒邮编 / 都道府県市区町村 / 番地・建物（本地文字）；拉丁转写按「番地, 市区町村, 都道府県 邮编, Japan」
- 电话：+81 开头的国际写法转成 0 开头的国内号码，全角数字 / 连字符转半角（日本国内承运商都只收国内写法）
"""
import re
import unicodedata
from decimal import Decimal

from .base import CountryRules

# 日本出口申报（輸出申告）用的 9 位统计品目番号：货值（FOB，不含运费 / 保险费）超过这个金额（JPY）时提示补齐
JP_EXPORT_CODE_THRESHOLD_JPY = 200000

# 与 customs_services 的 HS 写法同一口径（去掉点 / 空白 / 连字符）
_HS_STRIP = re.compile(r'[.\s\-]')
_CURRENCY_RE = re.compile(r'^[A-Z]{3}$')
# 各种横线 → 半角连字符（全角 －、长音 ー、‐ ‑ ‒ – — ― −）
_DASHES = dict.fromkeys(map(ord, '－ー‐‑‒–—―−ｰ'), '-')


def _hs_valid(hs_code) -> bool:
    digits = _HS_STRIP.sub('', str(hs_code or ''))
    return digits.isdigit() and 6 <= len(digits) <= 10


def jp_export_code_problem(jp_export_code, hs_code):
    """9 位日本出口统计品目番号：非 9 位数字或前 6 位与 HS 不一致 → 返回原因，否则 None。"""
    digits = _HS_STRIP.sub('', str(jp_export_code or ''))
    if not (digits.isdigit() and len(digits) == 9):
        return "is not a 9-digit number"
    if _hs_valid(hs_code) and digits[:6] != _HS_STRIP.sub('', str(hs_code))[:6]:
        return "does not match the first 6 digits of the HS code"
    return None


def jp_phone(value) -> str:
    """电话号码 → 日本国内写法：全角数字 / 横线转半角，+81（可带空格 / 连字符 / 括号）换成 0，只留数字和连字符。
    例：'+81 90-1234-5678' → '090-1234-5678'，'＋８１（３）１２３４－５６７８' → '03-1234-5678'。空值为 ''。"""
    text = unicodedata.normalize('NFKC', str(value or '')).translate(_DASHES).strip()
    if not text:
        return ''
    international = False
    compact = re.sub(r'\s', '', text)
    if compact.startswith('+81'):
        text, international = compact[3:], True
    elif compact.startswith('0081'):
        text, international = compact[4:], True
    text = re.sub(r'[()]', '-', text)              # 03(1234)5678 → 03-1234-5678
    text = re.sub(r'[^0-9-]', '', text)
    text = re.sub(r'-{2,}', '-', text).strip('-')
    if international and text and not text.startswith('0'):
        text = '0' + text
    return text


class JapanRules(CountryRules):
    code = 'JP'
    exporter_tax_label = 'Corporate No.'
    address_format = {
        'local': (
            '〒{postal_code}',
            '{admin_area}{locality}{dependent_locality}',
            '{address_line1} {address_line2}',
            '{organization}',
            '{name}',
        ),
        'latin': (
            '{name}',
            '{organization}',
            '{address_line1}',
            '{address_line2}',
            '{dependent_locality}',
            '{locality}, {admin_area} {postal_code}',
            '{country_name}',
        ),
    }
    line_separator = {'local': ' ', 'latin': ', '}

    def customs_line_problems(self, line: dict) -> list:
        if not line.get('jp_export_code'):
            return []
        reason = jp_export_code_problem(line.get('jp_export_code'), line.get('hs_code'))
        if not reason:
            return []
        return [{'code': 'JP_EXPORT_CODE_MISMATCH', 'level': 'warning',
                 'message': f"Japanese export statistics code of goods {line.get('goods_code')} {reason}",
                 'field': 'jp_export_code'}]

    def customs_problems(self, ctx: dict) -> list:
        problems = []
        currency = ctx.get('currency') or ''
        goods_value = ctx.get('goods_value') or Decimal(0)
        lines = ctx.get('lines') or []
        # 日本正式出口申报（申告价格 = FOB，即货值合计超过 20 万日元）要 9 位统计品目番号：
        # 按货值判断，运费 / 保险费不算进去；缺的行给警告，不拦出单证
        if currency == 'JPY' and goods_value > JP_EXPORT_CODE_THRESHOLD_JPY:
            for line in lines:
                if line['packed_quantity'] > 0 and line['has_customs_line'] and not line['jp_export_code']:
                    problems.append({
                        'code': 'JP_EXPORT_CODE_MISSING', 'level': 'warning',
                        'message': (f"Japanese export statistics code of goods {line['goods_code']} is missing "
                                    f"(goods value exceeds {JP_EXPORT_CODE_THRESHOLD_JPY:,} JPY)"),
                        'goods_code': line['goods_code'], 'field': 'jp_export_code',
                    })
        # 非 JPY 币种：不引入汇率，没法自动判断是否超过 20 万円。有行没填 9 位统计番号时
        # 给一条单据级警告，请人工确认（不拦出单证）
        if currency != 'JPY' and _CURRENCY_RE.match(currency) and goods_value > 0:
            missing_codes = [line['goods_code'] for line in lines
                             if line['packed_quantity'] > 0 and line['has_customs_line'] and not line['jp_export_code']]
            if missing_codes:
                problems.append({
                    'code': 'JP_EXPORT_VALUE_UNVERIFIED', 'level': 'warning',
                    'message': (f"Goods value is in {currency}, so whether it exceeds "
                                f"{JP_EXPORT_CODE_THRESHOLD_JPY:,} JPY cannot be determined automatically; check "
                                "manually. If it does, the Japanese 9-digit export statistics code is required for "
                                f"goods {', '.join(str(c) for c in missing_codes)}"),
                    'field': 'jp_export_code',
                })
        return problems

    def phone_normalize(self, phone) -> str:
        return jp_phone(phone)
