"""商品导出（POST /goods/exports）：按商品列表的筛选导出 CSV / Excel，可选列与顺序，表头按语言。

- 条件：filters 与商品列表的查询参数同名（goods_codes、image_missing、spec_missing、keyword、category、brand、is_active
  （true / false / all）……），另有 has_stock（按库存范围有 / 无库存）、created_from / created_to / updated_from /
  updated_to、warehouse_id（库存范围；不给 = 当前仓库 X-WAREHOUSE-ID，再没有 = 可访问的全部仓库）
- 列：columns（列键，顺序即文件里的列序，必须含 code）或 preset（import_template = 与导入模板同样的 20 列；all = 全部字段）。
  库存列（在库 total_stock / 可售 available_stock_for_sale / 待上架 sorted_stock / 破损 damage_stock）按库存范围合计
- 文件：file_format csv（默认）/ xlsx；CSV 编码 utf-8（带 BOM，默认）/ shift_jis（CP932：〜 等先换成 CP932 的对应字，
  仍编码不了的写成「?」；表头写不下时用日文表头）。导出前可用 POST /goods/exports/preview 看件数与会变成「?」的商品
- ≤ 1,000 件在请求里生成（201，status completed）；更多的建任务后台生成（202，status pending → running → completed），
  进度 processed / total；进程中断的任务（heartbeat 超过 10 分钟）由定时任务重新排队（最多 3 次），失败可 POST retry
- 文件存库，保留 7 天（expires_at）后由定时任务删除内容（status expired）；记录保留，可 POST rerun 按原条件重新导出
- 单次最多 100,000 件（400 10022）
"""
import logging
import threading
import time
from datetime import timedelta
from decimal import Decimal

from flask import current_app, g, request
from sqlalchemy import func, or_, update
from sqlalchemy.orm import lazyload

from extensions import db
from extensions.error import BadRequestException, ConflictException, ForbiddenException, NotFoundException
from system.common.permissions import get_actor_company_id
from warehouse.common import check_warehouse_access, clock
from . import tabular
from .columns import (
    ACTIVE_LABELS, COLUMNS_BY_KEY, EXPORT_PRESETS, LANGS, SHEET_TITLES, STOCK_COLUMNS, header_label, resolve_lang,
)
from .filters import list_filters, normalize_stored_filters, stock_scope
from .models import Goods, GoodsExport
from .services import GoodsService

logger = logging.getLogger(__name__)

EXPORT_SYNC_LIMIT = 1000
EXPORT_MAX_ROWS = 100_000
EXPORT_RETENTION_DAYS = 7
EXPORT_BATCH_SIZE = 500
EXPORT_STALE_MINUTES = 10
EXPORT_MAX_ATTEMPTS = 3
UNENCODABLE_SAMPLE_ITEMS = 100
UNENCODABLE_SAMPLE_CHARS = 50
# 同一进程里同时在后台生成的导出数（多出来的留在 pending，由定时任务接着跑）
_THREAD_SLOTS = threading.BoundedSemaphore(2)

# 业务码（extensions/error.py、README 登记）
EXPORT_INVALID = 10021         # 400 导出请求不合法：field = filters.<键> / columns / preset / file_format / encoding / lang
EXPORT_TOO_LARGE = 10022       # 400 超过 100,000 件：details {total, max_rows}
EXPORT_NOT_READY = 16142       # 409 文件不能下载（还在生成 / 失败 / 已过期删除）：details {status}
EXPORT_NOT_RETRYABLE = 16143   # 409 只有 failed 的能重试：details {status}

FILE_FORMATS = ('csv', 'xlsx')
EXPORT_ENCODINGS = {'utf-8': 'utf-8-sig', 'cp932': 'cp932'}
_TEXT_KINDS = ('text', 'country', 'user', 'bool')


def _invalid(message, field, **details):
    return BadRequestException(message, EXPORT_INVALID, field, details=details or None)


# ------------------------------------------------------------------
# 请求 → 导出条件
# ------------------------------------------------------------------

def normalize_spec(body) -> dict:
    """请求体 → {filters, columns, preset, lang, file_format, encoding}（不合法 → 400 10021）"""
    if not isinstance(body, dict):
        raise _invalid("The request body must be a JSON object", None)
    try:
        filters = normalize_stored_filters(body.get('filters'))
    except ValueError as exc:
        key, reason = (exc.args + ('invalid value',))[:2]
        field = 'filters' if key == 'filters' else f'filters.{key}'
        raise _invalid(f"Invalid {field}: {reason}", field)

    preset = body.get('preset')
    if preset not in (None, '', 'custom', *EXPORT_PRESETS):
        raise _invalid(f"preset must be one of {', '.join(EXPORT_PRESETS)} or custom", 'preset')
    columns = body.get('columns')
    if columns in (None, []):
        columns = list(EXPORT_PRESETS[preset if preset in EXPORT_PRESETS else 'import_template'])
    if not isinstance(columns, list) or not all(isinstance(key, str) for key in columns):
        raise _invalid("columns must be a list of column keys", 'columns')
    unknown = [key for key in columns if key not in COLUMNS_BY_KEY]
    if unknown:
        raise _invalid(f"Unknown column(s): {', '.join(unknown)}", 'columns', unknown=unknown)
    duplicated = sorted({key for key in columns if columns.count(key) > 1})
    if duplicated:
        raise _invalid(f"Duplicated column(s): {', '.join(duplicated)}", 'columns', duplicated=duplicated)
    if 'code' not in columns:
        raise _invalid("columns must include code", 'columns')
    preset = next((name for name, keys in EXPORT_PRESETS.items() if list(keys) == columns), 'custom')

    file_format = str(body.get('file_format') or 'csv').strip().lower()
    if file_format not in FILE_FORMATS:
        raise _invalid("file_format must be csv or xlsx", 'file_format')
    encoding = None
    if file_format == 'csv':
        name = tabular.normalize_encoding(body.get('encoding') or 'utf-8')
        if name not in EXPORT_ENCODINGS:
            raise _invalid("encoding must be utf-8 or shift_jis", 'encoding')
        encoding = EXPORT_ENCODINGS[name]
    lang = resolve_lang(body.get('lang'), request.accept_languages)
    if lang is None:
        raise _invalid(f"lang must be one of {', '.join(LANGS)}", 'lang')
    return {'filters': filters, 'columns': columns, 'preset': preset, 'lang': lang, 'file_format': file_format,
            'encoding': encoding}


def resolve_scope(spec) -> dict:
    """调用方的公司与库存范围（须在 @warehouse_required() 之后调用）：
    {company_id, warehouse_id, scope_warehouse_ids, uses_stock}"""
    filters = spec['filters']
    company_id = get_actor_company_id() or filters.get('company_id')
    warehouse_id = filters.get('warehouse_id')
    if warehouse_id is not None:
        from warehouse.warehouse.models import Warehouse
        warehouse = db.session.get(Warehouse, warehouse_id)
        if warehouse is None:
            raise _invalid("Warehouse not found", 'filters.warehouse_id')
        if not check_warehouse_access(warehouse_id) or (company_id is not None and warehouse.company_id != company_id):
            raise ForbiddenException("Permission denied: warehouse is not accessible", 12001)
        scope = [warehouse_id]
    else:
        scope = stock_scope()
        warehouse_id = getattr(g, 'warehouse_id', None)
    uses_stock = bool(set(spec['columns']) & set(STOCK_COLUMNS)) or filters.get('has_stock') is not None
    return {'company_id': company_id, 'warehouse_id': warehouse_id, 'scope_warehouse_ids': scope,
            'uses_stock': uses_stock}


def _goods_query(filters, company_id, scope):
    return GoodsService.list_goods(list_filters(filters, company_id, scope))


def export_header(key, lang, encoding) -> str:
    """表头；Shift_JIS 写不下（中文简体字等）时用日文表头（导入照样认）"""
    label = header_label(key, lang)
    if encoding == 'cp932' and tabular.unencodable_chars(label, 'cp932'):
        return header_label(key, 'ja-JP')
    return label


def _active_labels(lang, encoding):
    labels = ACTIVE_LABELS[lang]
    if encoding == 'cp932' and any(tabular.unencodable_chars(label, 'cp932') for label in labels):
        return ACTIVE_LABELS['ja-JP']
    return labels


def preview(spec, scope) -> dict:
    """POST /goods/exports/preview：件数、是否后台生成、表头，CSV + Shift_JIS 时预估会有字符变成「?」的商品"""
    query = _goods_query(spec['filters'], scope['company_id'], scope['scope_warehouse_ids'])
    total = query.order_by(None).count()
    result = {
        'total': total,
        'background': total > EXPORT_SYNC_LIMIT,
        'sync_limit': EXPORT_SYNC_LIMIT,
        'max_rows': EXPORT_MAX_ROWS,
        'too_large': total > EXPORT_MAX_ROWS,
        'columns': [{'key': key, 'header': export_header(key, spec['lang'], spec['encoding'])}
                    for key in spec['columns']],
        'preset': spec['preset'],
        'lang': spec['lang'],
        'file_format': spec['file_format'],
        'encoding': spec['encoding'],
        'warehouse_id': scope['warehouse_id'],
        'unencodable': None,
    }
    if spec['encoding'] == 'cp932' and total <= EXPORT_MAX_ROWS:
        result['unencodable'] = _scan_unencodable(query, spec['columns'])
    return result


def _scan_unencodable(query, columns) -> dict:
    """商品的文字列里 Shift_JIS 写不下的字符：{count（商品件数）, characters, items: [{id, code, name, columns, characters}]}"""
    text_keys = [key for key in columns if COLUMNS_BY_KEY[key].kind in ('text', 'country')]
    entities = [Goods.id, Goods.code, Goods.name] + [getattr(Goods, key) for key in text_keys]
    count, characters, items = 0, [], []
    for row in query.with_entities(*entities).yield_per(1000):
        bad_columns, bad_chars = [], []
        for key, value in zip(text_keys, row[3:]):
            chars = tabular.unencodable_chars(value, 'cp932')
            if chars:
                bad_columns.append(key)
                bad_chars.extend(ch for ch in chars if ch not in bad_chars)
        if not bad_columns:
            continue
        count += 1
        for ch in bad_chars:
            if ch not in characters and len(characters) < UNENCODABLE_SAMPLE_CHARS:
                characters.append(ch)
        if len(items) < UNENCODABLE_SAMPLE_ITEMS:
            items.append({'id': row[0], 'code': row[1], 'name': row[2], 'columns': bad_columns,
                          'characters': bad_chars})
    return {'count': count, 'characters': characters, 'items': items}


# ------------------------------------------------------------------
# 建任务 / 重试 / 重新导出
# ------------------------------------------------------------------

def create(spec, scope, user_id, source_export_id=None) -> GoodsExport:
    query = _goods_query(spec['filters'], scope['company_id'], scope['scope_warehouse_ids'])
    total = query.order_by(None).count()
    if total > EXPORT_MAX_ROWS:
        raise BadRequestException(f"{total} goods match; export at most {EXPORT_MAX_ROWS} at a time", EXPORT_TOO_LARGE,
                                  details={'total': total, 'max_rows': EXPORT_MAX_ROWS})
    export = GoodsExport(
        company_id=scope['company_id'], warehouse_id=scope['warehouse_id'],
        scope_warehouse_ids=scope['scope_warehouse_ids'], uses_stock=scope['uses_stock'],
        filters=spec['filters'], columns=spec['columns'], preset=spec['preset'], lang=spec['lang'],
        file_format=spec['file_format'], encoding=spec['encoding'], status='pending',
        background=total > EXPORT_SYNC_LIMIT, total=total, processed=0, attempts=0,
        source_export_id=source_export_id, created_by=user_id,
    )
    db.session.add(export)
    db.session.commit()
    return _start(export)


def _start(export) -> GoodsExport:
    """≤ 1,000 件：在请求里生成；更多：交给后台线程（没开线程时等定时任务）。返回最新的记录"""
    export_id = export.id
    if export.background:
        _spawn(export_id)
    elif _claim(export_id):
        _run_claimed(export_id)
    db.session.expire_all()
    return db.session.get(GoodsExport, export_id)


def retry(export) -> GoodsExport:
    if export.status != 'failed':
        raise ConflictException(f"Only a failed export can be retried (status {export.status})", EXPORT_NOT_RETRYABLE,
                                details={'status': export.status})
    total = _goods_query(export.filters, export.company_id, export.scope_warehouse_ids).order_by(None).count()
    export.status = 'pending'
    export.error = None
    export.processed = 0
    export.total = total
    export.background = total > EXPORT_SYNC_LIMIT
    export.finished_at = None
    db.session.commit()
    return _start(export)


def rerun_spec(export) -> dict:
    """按某条记录的条件重新导出（文件过期 / 想要最新数据）：条件照旧，库存范围按原记录的仓库（没有则按当前调用方）"""
    filters = dict(export.filters or {})
    if export.warehouse_id and 'warehouse_id' not in filters:
        filters['warehouse_id'] = export.warehouse_id
    return {'filters': filters, 'columns': list(export.columns), 'preset': export.preset, 'lang': export.lang,
            'file_format': export.file_format, 'encoding': export.encoding}


# ------------------------------------------------------------------
# 生成
# ------------------------------------------------------------------

def _claim(export_id) -> bool:
    """pending → running（原子，后台线程与定时任务不会重复生成）"""
    now = clock.now()
    result = db.session.execute(
        update(GoodsExport)
        .where(GoodsExport.id == export_id, GoodsExport.status == 'pending')
        .values(status='running', started_at=now, heartbeat_at=now, attempts=GoodsExport.attempts + 1,
                processed=0, error=None)
        .execution_options(synchronize_session=False))
    db.session.commit()
    return result.rowcount == 1


def _run_claimed(export_id):
    export = db.session.get(GoodsExport, export_id)
    try:
        _generate(export)
        db.session.commit()
    except Exception as exc:  # noqa: BLE001 - 记成失败，界面可重试
        db.session.rollback()
        logger.exception('[GoodsExport] export %s failed', export_id)
        export = db.session.get(GoodsExport, export_id)
        export.status = 'failed'
        export.error = f"{type(exc).__name__}: {exc}"[:500]
        export.finished_at = clock.now()
        db.session.commit()


def run_export(export_id) -> bool:
    """生成一条 pending 的导出（被别人领走了返回 False）"""
    if not _claim(export_id):
        return False
    _run_claimed(export_id)
    return True


def _spawn(export_id):
    if not current_app.config.get('GOODS_EXPORT_THREAD', True):
        return
    app = current_app._get_current_object()
    threading.Thread(target=_thread_main, args=(app, export_id), daemon=True,
                     name=f'goods-export-{export_id}').start()


def _thread_main(app, export_id):
    if not _THREAD_SLOTS.acquire(blocking=False):
        return
    try:
        with app.app_context():
            try:
                run_export(export_id)
            finally:
                db.session.remove()
    finally:
        _THREAD_SLOTS.release()


def _plain_number(value):
    """Decimal → 不带多余 0 的文字（1.200 → 1.2，1760.00 → 1760）"""
    if value is None:
        return None
    text = format(Decimal(str(value)), 'f')
    if '.' in text:
        text = text.rstrip('0').rstrip('.')
    return text


def _stock_rows(goods_ids, scope) -> dict:
    from warehouse.inventory.models import Inventory
    query = db.session.query(
        Inventory.goods_id,
        func.sum(Inventory.total_stock),
        func.sum(Inventory.onhand_stock - Inventory.locked_stock - Inventory.dn_stock),
        func.sum(Inventory.sorted_stock),
        func.sum(Inventory.damage_stock),
    ).filter(Inventory.goods_id.in_(goods_ids))
    if scope is not None:
        query = query.filter(Inventory.warehouse_id.in_(scope))
    return {row[0]: {'stock_on_hand': int(row[1] or 0), 'stock_available': int(row[2] or 0),
                     'stock_pending_putaway': int(row[3] or 0), 'stock_damaged': int(row[4] or 0)}
            for row in query.group_by(Inventory.goods_id).all()}


def _user_names(user_ids) -> dict:
    from system.user.models import User
    ids = [user_id for user_id in user_ids if user_id is not None]
    if not ids:
        return {}
    return dict(db.session.query(User.id, User.user_name).filter(User.id.in_(ids)).all())


def _cell(key, goods, stock, users, fmt, active_labels):
    column = COLUMNS_BY_KEY[key]
    if column.group == 'stock':
        return (stock or {}).get(key, 0)
    if key == 'is_active':
        return active_labels[0] if goods.is_active else active_labels[1]
    if key == 'created_by':
        return users.get(goods.created_by)
    value = getattr(goods, key)
    if value is None:
        return None
    if column.kind == 'datetime':
        local = clock.to_local(value)
        return local.replace(tzinfo=None) if fmt == 'xlsx' else local.strftime('%Y-%m-%d %H:%M:%S')
    if column.kind == 'date':
        return value if fmt == 'xlsx' else value.isoformat()
    if column.kind == 'number':
        return Decimal(_plain_number(value)) if fmt == 'xlsx' else _plain_number(value)
    return value


def _generate(export):
    columns, lang, encoding, fmt = export.columns, export.lang, export.encoding, export.file_format
    header = [export_header(key, lang, encoding) for key in columns]
    if fmt == 'xlsx':
        kinds = ['text' if COLUMNS_BY_KEY[key].kind in _TEXT_KINDS else None for key in columns]
        writer = tabular.XlsxWriter(SHEET_TITLES.get(lang, 'Goods'), header, kinds)
    else:
        writer = tabular.CsvWriter(header, encoding)
    active_labels = _active_labels(lang, encoding)
    wants_stock = bool(set(columns) & set(STOCK_COLUMNS))
    base = _goods_query(export.filters, export.company_id, export.scope_warehouse_ids)
    processed = unencodable = 0
    last_id = None
    while True:
        ids_query = base.with_entities(Goods.id)
        if last_id is not None:
            ids_query = ids_query.filter(Goods.id < last_id)
        ids = [row[0] for row in ids_query.limit(EXPORT_BATCH_SIZE).all()]
        if not ids:
            break
        last_id = ids[-1]
        goods_by_id = {goods.id: goods for goods in
                       Goods.query.options(lazyload('*')).filter(Goods.id.in_(ids)).all()}
        stock = _stock_rows(ids, export.scope_warehouse_ids) if wants_stock else {}
        users = _user_names({goods.created_by for goods in goods_by_id.values()}) if 'created_by' in columns else {}
        for goods_id in ids:
            goods = goods_by_id[goods_id]
            row = [_cell(key, goods, stock.get(goods_id), users, fmt, active_labels) for key in columns]
            if encoding == 'cp932' and any(isinstance(value, str) and tabular.unencodable_chars(value, 'cp932')
                                           for value in row):
                unencodable += 1
            writer.append(row)
        processed += len(ids)
        export.processed = processed
        export.heartbeat_at = clock.now()
        db.session.commit()
        for goods in goods_by_id.values():   # 大批量时不让会话里的商品越积越多
            db.session.expunge(goods)
    content = writer.save()
    finished = clock.now()
    export.content = content
    export.file_size = len(content)
    export.row_count = processed
    export.processed = processed
    export.total = max(export.total or 0, processed)
    export.unencodable_count = unencodable if encoding == 'cp932' else None
    export.file_name = f"goods_{clock.to_local(finished):%Y%m%d-%H%M%S}.{fmt}"
    export.status = 'completed'
    export.finished_at = finished
    export.expires_at = finished + timedelta(days=EXPORT_RETENTION_DAYS)


# ------------------------------------------------------------------
# 查询（公司隔离；限仓库的员工只看自己仓库的、或不涉及库存的导出）
# ------------------------------------------------------------------

def _restricted_staff() -> bool:
    user = g.get('current_user')
    return user is not None and user.type == 'staff' and not user.has_role('company_admin')


def visible_query(company_id=None):
    query = GoodsExport.query
    actor_company_id = get_actor_company_id()
    if actor_company_id is not None:
        query = query.filter(GoodsExport.company_id == actor_company_id)
    elif company_id is not None:
        query = query.filter(GoodsExport.company_id == company_id)
    if _restricted_staff():
        query = query.filter(or_(GoodsExport.uses_stock.is_(False),
                                 GoodsExport.warehouse_id == getattr(g, 'warehouse_id', None)))
    return query


def get_visible(export_id) -> GoodsExport:
    export = visible_query().filter(GoodsExport.id == export_id).first()
    if export is None:
        raise NotFoundException(f"GoodsExport with id {export_id} not found", 13001)
    return export


def download(export):
    """→ (字节, 文件名, Content-Type)；不是 completed → 409 16142"""
    if export.status != 'completed':
        raise ConflictException(f"The export file is not available (status {export.status})", EXPORT_NOT_READY,
                                details={'status': export.status})
    content = db.session.query(GoodsExport.content).filter(GoodsExport.id == export.id).scalar()
    if content is None:
        raise ConflictException("The export file is not available (status expired)", EXPORT_NOT_READY,
                                details={'status': 'expired'})
    return content, export.file_name, tabular.content_type(export.file_format, export.encoding)


# ------------------------------------------------------------------
# 定时任务（scheduler.py goods_files，每分钟）：接着跑没生成的、恢复中断的、清理到期文件
# ------------------------------------------------------------------

def recover_stale(now=None) -> dict:
    now = now or clock.now()
    cutoff = now - timedelta(minutes=EXPORT_STALE_MINUTES)
    requeued = failed = 0
    stale = (GoodsExport.query.options(lazyload('*'))
             .filter(GoodsExport.status == 'running',
                     or_(GoodsExport.heartbeat_at.is_(None), GoodsExport.heartbeat_at < cutoff)).all())
    for export in stale:
        if (export.attempts or 0) < EXPORT_MAX_ATTEMPTS:
            export.status = 'pending'
            requeued += 1
        else:
            export.status = 'failed'
            export.error = 'Interrupted: the export stopped responding'
            export.finished_at = now
            failed += 1
    if stale:
        db.session.commit()
    return {'requeued': requeued, 'failed': failed}


def purge_expired(now=None) -> int:
    now = now or clock.now()
    expired = (GoodsExport.query
               .filter(GoodsExport.status == 'completed', GoodsExport.expires_at.isnot(None),
                       GoodsExport.expires_at <= now)
               .limit(500).all())
    for export in expired:
        export.content = None
        export.status = 'expired'
    if expired:
        db.session.commit()
    return len(expired)


def run_goods_file_jobs(time_budget=50) -> dict:
    """定时任务：中断的重新排队 → 依次生成 pending 的（时间预算内）→ 删到期的导出文件与导入文件"""
    from .importer import purge_expired_files
    started = time.monotonic()
    stale = recover_stale()
    generated = 0
    while time.monotonic() - started < time_budget:
        export = (GoodsExport.query.options(lazyload('*')).filter(GoodsExport.status == 'pending')
                  .order_by(GoodsExport.id).first())
        if export is None:
            break
        if run_export(export.id):
            generated += 1
    return {'generated': generated, **stale, 'expired_exports': purge_expired(),
            'purged_imports': purge_expired_files()}
