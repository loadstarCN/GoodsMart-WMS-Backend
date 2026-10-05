"""商品描述（富文本）的后端 HTML 白名单清洗（nh3 / ammonia）。

Web 端用 Quill 编辑、DOMPurify 显示（GoodsMart-WMS-Web/app/utils/sanitize.ts），但 Station、Wholesale
等系统直接渲染这个字段，所以入库前在后端再按白名单清洗一次（JSON 新建 / 修改、CSV 导入都走这里）。

白名单与前端 DOMPurify 配置对齐：
- 标签：排版类（p / br / div / span / hr / strong / b / em / i / u / s / strike / sub / sup / small /
  h1-h6 / ul / ol / li / blockquote / pre / code / a / img / table / thead / tbody / tr / th / td）
- 属性（所有标签）：href target title src alt width height class style colspan rowspan；
  rel 由 nh3 统一写成 "noopener noreferrer"（前端是 target 存在时补上）
- 链接协议：http / https / mailto / tel 与相对地址；javascript: / data: 等一律去掉
- <script> / <style> 连内容一起删除；注释删除；data-* 属性不保留
比前端更严的两处：
- style 只保留排版类属性（颜色、背景色、对齐、字体、行高、宽高），position 之类的整段去掉
- 例外放行 <li data-list="bullet|ordered|checked|unchecked">：Quill 2 用它区分有序 / 无序列表，
  去掉的话编辑页再打开时无序列表会变成有序列表
"""
import nh3

ALLOWED_TAGS = frozenset({
    'p', 'br', 'div', 'span', 'hr',
    'strong', 'b', 'em', 'i', 'u', 's', 'strike', 'sub', 'sup', 'small',
    'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
    'ul', 'ol', 'li',
    'blockquote', 'pre', 'code',
    'a', 'img',
    'table', 'thead', 'tbody', 'tr', 'th', 'td',
})

ALLOWED_ATTRIBUTES = {
    '*': {
        'href', 'target', 'title',
        'src', 'alt', 'width', 'height',
        'class', 'style',
        'colspan', 'rowspan',
    },
}

ALLOWED_URL_SCHEMES = frozenset({'http', 'https', 'mailto', 'tel'})

ALLOWED_STYLE_PROPERTIES = frozenset({
    'color', 'background-color', 'text-align', 'text-decoration',
    'font-size', 'font-family', 'font-weight', 'font-style', 'line-height',
    'width', 'height',
})

QUILL_LIST_VALUES = {'li': {'data-list': {'bullet', 'ordered', 'checked', 'unchecked'}}}

_cleaner = nh3.Cleaner(
    tags=set(ALLOWED_TAGS),
    clean_content_tags={'script', 'style'},
    attributes={tag: set(attrs) for tag, attrs in ALLOWED_ATTRIBUTES.items()},
    url_schemes=set(ALLOWED_URL_SCHEMES),
    strip_comments=True,
    link_rel='noopener noreferrer',
    tag_attribute_values=QUILL_LIST_VALUES,
    filter_style_properties=set(ALLOWED_STYLE_PROPERTIES),
)


def sanitize_description(value):
    """清洗商品描述。None 原样返回；非字符串转成字符串再清洗。

    不含 "<" 的纯文本不可能带标签，原样返回——否则 nh3 会把 "A & B" 转义成 "A &amp; B"，
    把按纯文本显示该字段的地方（APP、CSV 导出等）弄乱。
    """
    if value is None:
        return None
    if not isinstance(value, str):
        value = str(value)
    if '<' not in value:
        return value
    return _cleaner.clean(value)
