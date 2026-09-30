"""出口单证 PDF 渲染：商业发票（Commercial Invoice）与装箱单（Packing List）。

- reportlab（BSD），A4 纵向，英文。
- 字体：默认 Helvetica；配置 CUSTOMS_PDF_FONT_PATH 可指定 TTF。基础字体印不出的字符
  （日文等非拉丁字符）退回 reportlab 内置的日文 CID 字体，不会变成方块或报错。
- invariant 模式：同样的输入产出逐字节相同的 PDF（便于校验 sha256）。
- 输入是 CustomsService._document_data() 产出的纯数据字典，这里只管排版。
"""
import io
import logging
from xml.sax.saxutils import escape

from flask import current_app
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

logger = logging.getLogger(__name__)

CID_FALLBACK_FONT = 'HeiseiKakuGo-W5'
PAGE_WIDTH, PAGE_HEIGHT = A4
MARGIN = 15 * mm
CONTENT_WIDTH = PAGE_WIDTH - 2 * MARGIN

_HEADER_BG = colors.HexColor('#E8E8E8')
_LABEL_BG = colors.HexColor('#F3F3F3')
_GRID = colors.HexColor('#9A9A9A')

_ttf_cache = {}
_cid_registered = False


def _ensure_cid_font():
    global _cid_registered
    if not _cid_registered:
        pdfmetrics.registerFont(UnicodeCIDFont(CID_FALLBACK_FONT))
        _cid_registered = True


def _load_ttf(path):
    """按路径注册一次 TTF；失败记日志并退回 Helvetica（单证照常生成）。"""
    if path in _ttf_cache:
        return _ttf_cache[path]
    font = None
    try:
        font = TTFont(f'CustomsDocFont{len(_ttf_cache) + 1}', path)
        pdfmetrics.registerFont(font)
    except Exception as exc:  # noqa: BLE001
        logger.warning("CUSTOMS_PDF_FONT_PATH %r could not be loaded (%s); using Helvetica", path, exc)
        font = None
    _ttf_cache[path] = font
    return font


class _Fonts:
    """基础字体 + 非拉丁字符退回 CID 字体的 Paragraph 标记生成器。"""

    def __init__(self):
        self.regular, self.bold = 'Helvetica', 'Helvetica-Bold'
        self._ttf = None
        path = current_app.config.get('CUSTOMS_PDF_FONT_PATH') if current_app else None
        if path:
            font = _load_ttf(path)
            if font is not None:
                self.regular = self.bold = font.fontName
                self._ttf = font

    def covers(self, ch: str) -> bool:
        if ch in '\n\t':
            return True
        if self._ttf is not None:
            return ord(ch) in self._ttf.face.charToGlyph
        try:
            ch.encode('cp1252')   # Helvetica 的 WinAnsi 编码
            return True
        except UnicodeEncodeError:
            return False

    def markup(self, text) -> str:
        """纯文本 → Paragraph 标记：转义 XML，基础字体印不出的连续字符包进 CID 字体。"""
        if text is None:
            return ''
        text = str(text)
        parts = []
        run, run_fallback = [], None
        for ch in text:
            fallback = not self.covers(ch)
            if run and fallback != run_fallback:
                parts.append((run_fallback, ''.join(run)))
                run = []
            run.append(ch)
            run_fallback = fallback
        if run:
            parts.append((run_fallback, ''.join(run)))

        out = []
        for fallback, chunk in parts:
            chunk = escape(chunk).replace('\n', '<br/>')
            if fallback:
                _ensure_cid_font()
                out.append(f'<font name="{CID_FALLBACK_FONT}">{chunk}</font>')
            else:
                out.append(chunk)
        return ''.join(out)


class _Kit:
    """一份单证用到的字体与段落样式。"""

    def __init__(self):
        self.fonts = _Fonts()
        regular, bold = self.fonts.regular, self.fonts.bold
        self.title = ParagraphStyle('title', fontName=bold, fontSize=16, leading=20, alignment=TA_CENTER,
                                    spaceAfter=4 * mm)
        self.normal = ParagraphStyle('normal', fontName=regular, fontSize=8, leading=10)
        self.bold = ParagraphStyle('bold', parent=self.normal, fontName=bold)
        self.label = ParagraphStyle('label', fontName=bold, fontSize=7, leading=9, textColor=colors.HexColor('#444444'))
        self.heading = ParagraphStyle('heading', fontName=bold, fontSize=7.5, leading=10,
                                      textColor=colors.HexColor('#333333'), spaceAfter=1)
        self.cell = ParagraphStyle('cell', fontName=regular, fontSize=7.5, leading=9)
        self.cell_right = ParagraphStyle('cell_right', parent=self.cell, alignment=TA_RIGHT)
        self.head = ParagraphStyle('head', fontName=bold, fontSize=7, leading=8.5)
        self.head_right = ParagraphStyle('head_right', parent=self.head, alignment=TA_RIGHT)
        self.small = ParagraphStyle('small', fontName=regular, fontSize=7, leading=9)

    def p(self, text, style=None):
        return Paragraph(self.fonts.markup(text), style or self.normal)


def _info_table(kit: _Kit, pairs):
    """两组「标签 | 值」一行的信息表。"""
    rows = []
    for i in range(0, len(pairs), 2):
        row = []
        for label, value in pairs[i:i + 2]:
            row += [kit.p(label, kit.label), kit.p(value, kit.normal)]
        while len(row) < 4:
            row.append('')
        rows.append(row)
    table = Table(rows, colWidths=[44 * mm, 46 * mm, 44 * mm, 46 * mm])
    table.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('BOX', (0, 0), (-1, -1), 0.5, _GRID),
        ('INNERGRID', (0, 0), (-1, -1), 0.25, _GRID),
        ('BACKGROUND', (0, 0), (0, -1), _LABEL_BG),
        ('BACKGROUND', (2, 0), (2, -1), _LABEL_BG),
        ('TOPPADDING', (0, 0), (-1, -1), 2),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
        ('LEFTPADDING', (0, 0), (-1, -1), 4),
        ('RIGHTPADDING', (0, 0), (-1, -1), 4),
    ]))
    return table


def _party(kit: _Kit, title, lines):
    flow = [kit.p(title, kit.heading)]
    first = True
    for line in lines:
        if not line:
            continue
        flow.append(kit.p(line, kit.bold if first else kit.normal))
        first = False
    return flow


def _parties_table(kit: _Kit, data):
    exporter = data['exporter']
    consignee = data['consignee']
    shipper = _party(kit, 'SHIPPER / EXPORTER', [
        exporter['name'], exporter['address'], exporter['country'],
        f"Tel: {exporter['phone']}" if exporter['phone'] else None,
        f"Email: {exporter['email']}" if exporter['email'] else None,
        f"Contact: {exporter['contact']}" if exporter['contact'] else None,
        exporter['tax'],
    ])
    ship_to = _party(kit, 'CONSIGNEE / SHIP TO', [
        consignee['name'], consignee['company'], *consignee['address_lines'], consignee['city_line'],
        consignee['country'],
        f"Tel: {consignee['phone']}" if consignee['phone'] else None,
        consignee['tax'],
    ])
    ship_from = data['ship_from']
    left = _party(kit, 'SHIP FROM', [
        ship_from['name'], ship_from['address'], ship_from['country'],
        f"Contact: {ship_from['contact']}" if ship_from['contact'] else None,
        f"Tel: {ship_from['phone']}" if ship_from['phone'] else None,
    ]) if ship_from else ''
    sold_to = [kit.p('SOLD TO', kit.heading), kit.p('Same as Consignee', kit.normal)]

    table = Table([[shipper, ship_to], [left, sold_to]], colWidths=[CONTENT_WIDTH / 2] * 2)
    table.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('BOX', (0, 0), (-1, -1), 0.5, _GRID),
        ('INNERGRID', (0, 0), (-1, -1), 0.25, _GRID),
        ('TOPPADDING', (0, 0), (-1, -1), 3),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
    ]))
    return table


def _grid_table(kit: _Kit, header, rows, col_widths, right_cols=()):
    """带表头的明细表（表头跨页重复）；right_cols 为右对齐的列号。"""
    head = [kit.p(h, kit.head_right if i in right_cols else kit.head) for i, h in enumerate(header)]
    body = [
        [kit.p(v, kit.cell_right if i in right_cols else kit.cell) for i, v in enumerate(row)]
        for row in rows
    ]
    table = Table([head] + body, colWidths=col_widths, repeatRows=1)
    table.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('BOX', (0, 0), (-1, -1), 0.5, _GRID),
        ('INNERGRID', (0, 0), (-1, -1), 0.25, _GRID),
        ('BACKGROUND', (0, 0), (-1, 0), _HEADER_BG),
        ('TOPPADDING', (0, 0), (-1, -1), 2),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
        ('LEFTPADDING', (0, 0), (-1, -1), 3),
        ('RIGHTPADDING', (0, 0), (-1, -1), 3),
    ]))
    return table


def _totals_table(kit: _Kit, rows, bold_labels=()):
    body = []
    for label, value in rows:
        style = kit.bold if label in bold_labels else kit.normal
        body.append([kit.p(label, kit.label if label not in bold_labels else kit.bold),
                     Paragraph(kit.fonts.markup(value), ParagraphStyle('tv', parent=style, alignment=TA_RIGHT))])
    table = Table(body, colWidths=[50 * mm, 45 * mm], hAlign='RIGHT')
    styles = [
        ('BOX', (0, 0), (-1, -1), 0.5, _GRID),
        ('INNERGRID', (0, 0), (-1, -1), 0.25, _GRID),
        ('BACKGROUND', (0, 0), (0, -1), _LABEL_BG),
        ('TOPPADDING', (0, 0), (-1, -1), 2),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
    ]
    for index, (label, _value) in enumerate(rows):
        if label in bold_labels:
            styles.append(('BACKGROUND', (0, index), (-1, index), _HEADER_BG))
    table.setStyle(TableStyle(styles))
    return table


def _footer_callback(title: str, invoice_number: str, version: int):
    text = f"{title} {invoice_number} - Version {version}"
    text = text.encode('ascii', 'replace').decode('ascii')

    def draw(canvas, doc):
        canvas.saveState()
        canvas.setFont('Helvetica', 7)
        canvas.setFillColor(colors.HexColor('#666666'))
        canvas.drawString(MARGIN, 9 * mm, text)
        canvas.drawRightString(PAGE_WIDTH - MARGIN, 9 * mm, f"Page {doc.page}")
        canvas.restoreState()
    return draw


def _build(story, title, author, footer) -> bytes:
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=A4,
        leftMargin=MARGIN, rightMargin=MARGIN, topMargin=14 * mm, bottomMargin=16 * mm,
        title=title, author=author or '', subject=title, creator='WMS customs documents',
        invariant=1,
    )
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return buffer.getvalue()


def _common_pairs(data, meta, commercial: bool):
    pairs = [
        ('Invoice No.', data['invoice_number']),
        ('Invoice Date', meta['invoice_date']),
        ('Reference / Order No.', data['reference'] or '-'),
    ]
    if data['awb']:
        pairs.append(('AWB No.', data['awb']))
    if data['carrier']:
        pairs.append(('Carrier', data['carrier']))
    if commercial:
        pairs += [
            ('Incoterms® 2020', data['terms']),
            ('Currency', data['currency']),
            ('Reason for Export', data['export_reason']),
        ]
    pairs += [
        ('Country of Export', data['country_of_export']),
        ('Country of Ultimate Destination', data['destination']),
    ]
    return pairs


def render_commercial_invoice(data: dict, meta: dict) -> bytes:
    kit = _Kit()
    currency = data['currency']
    story = [
        kit.p('COMMERCIAL INVOICE', kit.title),
        _info_table(kit, _common_pairs(data, meta, commercial=True)),
        Spacer(1, 3 * mm),
        _parties_table(kit, data),
        Spacer(1, 4 * mm),
    ]

    show_net = data['show_net_weight']
    header = ['No.', 'Description', 'HS Code', 'Country of Origin', 'JAN', 'Qty', 'Unit',
              f'Unit Value ({currency})', f'Amount ({currency})']
    widths = [8, 42, 17, 27, 25, 11, 10, 19, 21]
    right_cols = (5, 7, 8)
    if show_net:
        header.append('Net Wt (kg)')
        widths = [8, 36, 16, 26, 24, 10, 10, 17, 19, 14]
        right_cols = (5, 7, 8, 9)
    rows = []
    for index, item in enumerate(data['items'], start=1):
        row = [str(index), item['description'], item['hs_code'], item['origin'], item['goods_code'],
               f"{item['quantity']:,}", item['unit'], item['unit_value'], item['amount']]
        if show_net:
            row.append(item['net_weight_kg'])
        rows.append(row)
    story.append(_grid_table(kit, header, rows, [w * mm for w in widths], right_cols=right_cols))
    story.append(Spacer(1, 3 * mm))

    totals = data['totals']
    total_rows = [
        ('Total Quantity', f"{totals['quantity']:,}"),
        ('Goods Value', f"{currency} {totals['goods_value']}"),
        ('Freight', f"{currency} {totals['freight']}"),
    ]
    if totals.get('insurance') is not None:     # 投保且保险费 > 0 时才有
        total_rows.append(('Insurance', f"{currency} {totals['insurance']}"))
    total_rows += [
        ('Total Invoice Value', f"{currency} {totals['invoice_total']}"),
        ('Number of Packages', str(totals['package_count'])),
        ('Total Gross Weight', f"{totals['gross_weight_kg']} kg"),
    ]
    if totals['net_weight_kg'] is not None:
        total_rows.append(('Total Net Weight', f"{totals['net_weight_kg']} kg"))
    story.append(_totals_table(kit, total_rows, bold_labels=('Total Invoice Value',)))
    story.append(Spacer(1, 6 * mm))

    signature = Table([
        [kit.p('Signature', kit.label), ''],
        [kit.p('Name', kit.label), kit.p(data['signatory_name'] or '', kit.normal)],
        [kit.p('Title', kit.label), kit.p(data['signatory_title'] or '', kit.normal)],
        [kit.p('Date', kit.label), kit.p(meta['invoice_date'], kit.normal)],
    ], colWidths=[25 * mm, 70 * mm], rowHeights=[12 * mm, None, None, None], hAlign='LEFT')
    signature.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'BOTTOM'),
        ('LINEBELOW', (1, 0), (1, 0), 0.6, colors.black),
        ('LINEBELOW', (1, 1), (1, -1), 0.25, _GRID),
    ]))
    story.append(KeepTogether([
        kit.p(data['declaration'], kit.normal),
        Spacer(1, 2 * mm),
        signature,
    ]))

    return _build(
        story,
        title=f"Commercial Invoice {data['invoice_number']}",
        author=data['exporter']['name'],
        footer=_footer_callback('Commercial Invoice', data['invoice_number'], meta['version']),
    )


def render_packing_list(data: dict, meta: dict) -> bytes:
    kit = _Kit()
    story = [
        kit.p('PACKING LIST', kit.title),
        _info_table(kit, _common_pairs(data, meta, commercial=False)),
        Spacer(1, 3 * mm),
        _parties_table(kit, data),
        Spacer(1, 4 * mm),
        kit.p('PACKAGES', kit.heading),
    ]
    package_rows = [
        [p['label'], p['dimensions_cm'], p['gross_weight_kg'], p['remark'] or '']
        for p in data['packages']
    ]
    story.append(_grid_table(
        kit, ['Package', 'Dimensions L x W x H (cm)', 'Gross Weight (kg)', 'Remark'],
        package_rows, [22 * mm, 50 * mm, 32 * mm, 76 * mm], right_cols=(2,),
    ))
    story.append(Spacer(1, 4 * mm))
    story.append(kit.p('CONTENTS', kit.heading))

    show_net = data['show_net_weight']
    header = ['No.', 'JAN', 'Description', 'Qty', 'Unit']
    widths = [10, 32, 104, 18, 16]
    right_cols = (3,)
    if show_net:
        header.append('Net Wt (kg)')
        widths = [10, 30, 90, 17, 14, 19]
        right_cols = (3, 5)
    rows = []
    for index, item in enumerate(data['items'], start=1):
        row = [str(index), item['goods_code'], item['description'], f"{item['quantity']:,}", item['unit']]
        if show_net:
            row.append(item['net_weight_kg'])
        rows.append(row)
    story.append(_grid_table(kit, header, rows, [w * mm for w in widths], right_cols=right_cols))
    story.append(Spacer(1, 3 * mm))

    totals = data['totals']
    total_rows = [
        ('Number of Packages', str(totals['package_count'])),
        ('Total Gross Weight', f"{totals['gross_weight_kg']} kg"),
    ]
    if totals['net_weight_kg'] is not None:
        total_rows.append(('Total Net Weight', f"{totals['net_weight_kg']} kg"))
    total_rows.append(('Total Quantity', f"{totals['quantity']:,}"))
    story.append(_totals_table(kit, total_rows))

    return _build(
        story,
        title=f"Packing List {data['invoice_number']}",
        author=data['exporter']['name'],
        footer=_footer_callback('Packing List', data['invoice_number'], meta['version']),
    )
