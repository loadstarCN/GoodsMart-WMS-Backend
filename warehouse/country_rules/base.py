"""国家规则包的基类（doc/shipping-flow.md §6.3）：没有登记的国家都用这里的默认规则。

核心（报关视图、地址排版、文件模板的过滤器……）只经 registry.rules_for(国家代码) 取规则，不出现国家判断。
一个国家的专有规则 = CountryRules 的子类（warehouse/country_rules/<cc>.py），在 registry 里登记；
第三方包也可以通过 entry point 组 goodsmart_wms.country_rules 登记（值为 CountryRules 子类或实例）。

- customs_line_problems(line)：报关视图里一行商品的国家专有检查（如日本的输出统计品目番号）
- customs_problems(ctx)：报关视图的单据级国家专有检查（如日本 20 万円以上要统计番号）
- exporter_tax_label：出口单证上发货人税号的默认标签（公司没设 tax_id_label 时印它）
- address_format：地址排版（local = 本地文字，latin = 拉丁转写），每行一个模板，{字段} 占位（字段见
  warehouse/common/address_format.py 的 ADDRESS_FIELDS，另有 country_name = 英文国名）；
  一行里的占位全为空时整行不要；只含 name / organization 的行是姓名行（with_names=False 时不要）
- line_separator：排成一行时各行之间的分隔
- phone_normalize(phone)：电话号码规范成本国承运商要的写法（默认只去多余空白）
"""
import re

# 西方习惯（默认）：姓名 → 公司 → 街道 → 城市 州 邮编 → 国家
DEFAULT_ADDRESS_FORMAT = (
    '{name}',
    '{organization}',
    '{address_line1}',
    '{address_line2}',
    '{dependent_locality}',
    '{locality} {admin_area} {postal_code}',
    '{country_name}',
)

_SPACES = re.compile(r'\s+')


class CountryRules:
    """默认规则（没有登记的国家）"""

    code = None                         # ISO 3166-1 alpha-2（默认规则为 None）
    exporter_tax_label = 'Tax ID'
    address_format = {
        'local': DEFAULT_ADDRESS_FORMAT,
        'latin': DEFAULT_ADDRESS_FORMAT,
    }
    line_separator = {'local': ', ', 'latin': ', '}

    # ------------------------------------------------------------------
    # 报关
    # ------------------------------------------------------------------

    def customs_line_problems(self, line: dict) -> list:
        """一行商品（报关快照的行，含 goods_code / hs_code 及国家专有字段）的问题：
        [{code, level, message, field?}]（goods_code 由调用方补上）"""
        return []

    def customs_problems(self, ctx: dict) -> list:
        """单据级的问题。ctx：{customs（DNCustoms）, currency（大写）, goods_value（Decimal，已打包货值）,
        lines（报关视图的 lines）, export_country}。→ [{code, level, message, goods_code?, field?}]"""
        return []

    # ------------------------------------------------------------------
    # 地址 / 电话
    # ------------------------------------------------------------------

    def address_lines(self, style: str) -> tuple:
        return tuple(self.address_format.get(style) or self.address_format.get('local') or DEFAULT_ADDRESS_FORMAT)

    def separator(self, style: str) -> str:
        return self.line_separator.get(style, ', ')

    def phone_normalize(self, phone) -> str:
        """去首尾空白、合并多余空白；空值为 ''"""
        return _SPACES.sub(' ', str(phone or '')).strip()

    def describe(self) -> dict:
        return {
            'code': self.code,
            'exporter_tax_label': self.exporter_tax_label,
            'address_format': {style: list(self.address_lines(style)) for style in ('local', 'latin')},
        }
