"""商品导入 / 导出的列（导入模板、导入识别的表头、导出可选的列共用这一份清单）。

- 导入列（importable）：GOODS_IMPORT_COLUMNS 的 20 列，顺序 = 导入模板 = 导出预设「导入模板格式」
- 只读列（导出才有，导入时忽略）：库存（在库 / 可售 / 待上架 / 破损，按导出的仓库范围合计）与系统字段（状态、创建 / 更新时间、创建人）
- 表头按语言（zh-CN / en-US / ja-JP）；导入时三种语言的表头、列键（code、weight……）和常见别名都能识别：
  比较前做 NFKC、转小写、去掉空白 / 括号 / 下划线等（「重量 kg」「重量(kg)」「Weight (kg)」「weight」是同一列）
- 批次管理（管理方式、保质期、允收期、允发期……）的字段商品表里还没有，二期启用批次时再加
"""
import re
import unicodedata
from typing import NamedTuple

LANGS = ('zh-CN', 'en-US', 'ja-JP')
DEFAULT_LANG = 'en-US'

# 列分组（导出页的「可选列」按组列出）
GROUPS = {
    'basic': {'zh-CN': '基本', 'en-US': 'Basic', 'ja-JP': '基本'},
    'price': {'zh-CN': '价格', 'en-US': 'Price', 'ja-JP': '価格'},
    'spec': {'zh-CN': '规格', 'en-US': 'Specs', 'ja-JP': '仕様'},
    'media': {'zh-CN': '图片与日期', 'en-US': 'Images and dates', 'ja-JP': '画像と日付'},
    'stock': {'zh-CN': '库存', 'en-US': 'Stock', 'ja-JP': '在庫'},
    'system': {'zh-CN': '系统', 'en-US': 'System', 'ja-JP': 'システム'},
}


class Column(NamedTuple):
    key: str
    group: str
    labels: dict            # 语言 → 表头
    kind: str               # text / number / integer / date / datetime / country / bool / user
    importable: bool = True
    max_length: int | None = None   # 文字列的长度上限（与 goods 表的列一致）
    aliases: tuple = ()             # 导入时另外认的表头


def _labels(zh, en, ja):
    return {'zh-CN': zh, 'en-US': en, 'ja-JP': ja}


COLUMNS = (
    Column('code', 'basic', _labels('编码', 'Code', 'コード'), 'text', max_length=50,
           aliases=('商品编码', '商品编号', '货号', 'SKU', 'goods code', 'product code', 'item code', '商品コード', '品番')),
    Column('name', 'basic', _labels('名称', 'Name', '商品名'), 'text', max_length=100,
           aliases=('商品名称', '品名', 'goods name', 'product name', 'item name', '名前')),
    Column('unit', 'basic', _labels('单位', 'Unit', '単位'), 'text', max_length=20),
    Column('weight', 'spec', _labels('重量 kg', 'Weight kg', '重量 kg'), 'number', aliases=('重量', 'weight')),
    Column('length', 'spec', _labels('长 mm', 'Length mm', '長さ mm'), 'integer', aliases=('长', '长度', '長さ', 'length')),
    Column('width', 'spec', _labels('宽 mm', 'Width mm', '幅 mm'), 'integer', aliases=('宽', '宽度', '幅', 'width')),
    Column('height', 'spec', _labels('高 mm', 'Height mm', '高さ mm'), 'integer',
           aliases=('高', '高度', '高さ', 'height')),
    Column('origin_country', 'spec', _labels('原产国', 'Country of origin', '原産国'), 'country',
           aliases=('原产地', '原産地', 'origin', 'origin country', 'country')),
    Column('manufacturer', 'basic', _labels('生产商', 'Manufacturer', '製造元'), 'text', max_length=255,
           aliases=('制造商', '厂商', '厂家', 'メーカー', 'maker')),
    Column('brand', 'basic', _labels('品牌', 'Brand', 'ブランド'), 'text', max_length=255),
    Column('image_url', 'media', _labels('图片 URL', 'Image URL', '画像 URL'), 'text', max_length=512,
           aliases=('图片', '画像', 'image')),
    Column('thumbnail_url', 'media', _labels('缩略图 URL', 'Thumbnail URL', 'サムネイル URL'), 'text', max_length=512,
           aliases=('缩略图', 'サムネイル', 'thumbnail')),
    Column('category', 'basic', _labels('分类', 'Category', 'カテゴリー'), 'text', max_length=100,
           aliases=('类别', 'カテゴリ')),
    Column('tags', 'basic', _labels('标签', 'Tags', 'タグ'), 'text', max_length=255, aliases=('tag',)),
    Column('price', 'price', _labels('价格', 'Price', '価格'), 'number', aliases=('售价', '販売価格')),
    Column('discount_price', 'price', _labels('折扣价', 'Discount price', '割引価格'), 'number',
           aliases=('discount', '割引')),
    Column('currency', 'price', _labels('货币', 'Currency', '通貨'), 'text', max_length=10, aliases=('币种',)),
    Column('expiration_date', 'media', _labels('有效期至', 'Expiry date', '有効期限'), 'date',
           aliases=('有效期', '保质期至', '賞味期限', 'expiration date', 'expiry')),
    Column('production_date', 'media', _labels('生产日期', 'Production date', '製造日'), 'date',
           aliases=('manufacturing date', '生産日')),
    Column('description', 'basic', _labels('描述', 'Description', '説明'), 'text', aliases=('说明', '商品説明')),
    # 只读：导出才有，导入时识别出来也忽略（read_only_columns）
    Column('stock_on_hand', 'stock', _labels('在库', 'On hand', '在庫'), 'integer', importable=False,
           aliases=('总库存', 'total stock')),
    Column('stock_available', 'stock', _labels('可售', 'Available', '販売可能'), 'integer', importable=False,
           aliases=('可售库存', 'available for sale')),
    Column('stock_pending_putaway', 'stock', _labels('待上架', 'Awaiting put-away', '棚入れ待ち'), 'integer',
           importable=False),
    Column('stock_damaged', 'stock', _labels('破损', 'Damaged', '破損'), 'integer', importable=False),
    Column('is_active', 'system', _labels('状态', 'Status', '状態'), 'bool', importable=False),
    Column('created_at', 'system', _labels('创建时间', 'Created at', '作成日時'), 'datetime', importable=False),
    Column('updated_at', 'system', _labels('更新时间', 'Updated at', '更新日時'), 'datetime', importable=False),
    Column('created_by', 'system', _labels('创建人', 'Created by', '作成者'), 'user', importable=False),
)
COLUMNS_BY_KEY = {column.key: column for column in COLUMNS}

# 商品导入（CSV / Excel）的列：模板（GET /goods/bulk_upload/template）与导入读的是同一份清单。
# 重量 kg，长宽高 mm（正整数），日期 YYYY-MM-DD
GOODS_IMPORT_COLUMNS = tuple(column.key for column in COLUMNS if column.importable)
GOODS_IMPORT_REQUIRED_COLUMNS = ('code', 'name')
GOODS_IMPORT_SAMPLE_ROW = (
    'SAMPLE-001', 'Sample goods', 'pcs', '0.328', '140', '130', '13', 'JP', 'Sample Maker', 'Sample Brand',
    'https://example.com/sample-001.jpg', '', 'Sample category', 'tag1,tag2', '1760', '1500', 'JPY', '', '2025-07-01',
    'Sample description',
)
STOCK_COLUMNS = tuple(column.key for column in COLUMNS if column.group == 'stock')

# 导出的预设：导入模板格式（改完可直接导入）/ 全部字段
EXPORT_PRESETS = {
    'import_template': GOODS_IMPORT_COLUMNS,
    'all': tuple(column.key for column in COLUMNS),
}

# 状态列（is_active）的显示
ACTIVE_LABELS = {
    'zh-CN': ('启用', '停用'), 'en-US': ('Active', 'Inactive'), 'ja-JP': ('有効', '無効'),
}
# 导入错误行文件末列的表头
ERROR_COLUMN_LABELS = {'zh-CN': '错误', 'en-US': 'Error', 'ja-JP': 'エラー'}
# Excel 的工作表名
SHEET_TITLES = {'zh-CN': '商品', 'en-US': 'Goods', 'ja-JP': '商品', None: 'Goods'}

_HEADER_STRIP = re.compile(r'[\s_\-.:：()\[\]{}（）【】/／・·]+')


def normalize_header(text) -> str:
    """表头比较用：NFKC、去首尾空白、小写，去掉空白 / 下划线 / 连字符 / 括号 / 斜线等"""
    text = unicodedata.normalize('NFKC', str(text or '')).strip().lower()
    return _HEADER_STRIP.sub('', text)


def _build_header_index():
    index = {}
    for column in COLUMNS:
        names = [column.key, *column.labels.values(), *column.aliases]
        for name in names:
            normalized = normalize_header(name)
            if not normalized:
                continue
            owner = index.setdefault(normalized, column.key)
            if owner != column.key:  # pragma: no cover - 清单写错时启动即报
                raise RuntimeError(f"Header alias {name!r} is used by both {owner} and {column.key}")
    return index


_HEADER_INDEX = _build_header_index()


def match_header(text):
    """表头文字 → 列键；认不出返回 None"""
    return _HEADER_INDEX.get(normalize_header(text))


def header_language(headers) -> str | None:
    """文件表头用的是哪种语言的标签（按完全一致的标签计数，最多的那种）；都是列键 / 别名时返回 None"""
    counts = {lang: 0 for lang in LANGS}
    for text in headers:
        cleaned = unicodedata.normalize('NFKC', str(text or '')).strip()
        for column in COLUMNS:
            for lang, label in column.labels.items():
                if cleaned == unicodedata.normalize('NFKC', label):
                    counts[lang] += 1
    best = max(LANGS, key=lambda lang: counts[lang])
    return best if counts[best] else None


def resolve_lang(value, accept_languages=None, default=DEFAULT_LANG):
    """语言参数：zh-CN / en-US / ja-JP（大小写、zh / en / ja、下划线写法都认）；没给按 Accept-Language 挑；
    都没有用 default。不认识的值返回 None（调用方报 400）"""
    if value not in (None, ''):
        text = str(value).strip().replace('_', '-').lower()
        for lang in LANGS:
            if text == lang.lower() or text == lang.split('-')[0]:
                return lang
        return None
    if accept_languages is not None:
        for name, _quality in accept_languages:
            prefix = str(name).split('-')[0].lower()
            for lang in LANGS:
                if lang.split('-')[0] == prefix:
                    return lang
    return default


def header_label(key, lang) -> str:
    """列的表头：lang 为 None 时用列键（旧模板的写法 code, name, …）"""
    if lang is None:
        return key
    return COLUMNS_BY_KEY[key].labels[lang]


def columns_catalog() -> dict:
    """导出页的列清单：分组、各语言的表头、能否导入，以及两个预设"""
    return {
        'groups': [{'key': key, 'labels': labels} for key, labels in GROUPS.items()],
        'columns': [{
            'key': column.key, 'group': column.group, 'labels': column.labels, 'importable': column.importable,
            'required': column.key in GOODS_IMPORT_REQUIRED_COLUMNS,
        } for column in COLUMNS],
        'presets': {name: list(keys) for name, keys in EXPORT_PRESETS.items()},
        'langs': list(LANGS),
    }
