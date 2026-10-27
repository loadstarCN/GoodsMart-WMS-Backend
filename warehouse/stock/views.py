"""库存流水、按库位、历史库存、库存分析、补货阈值的接口（doc/库存数值系统说明.md 第十–十二节）。

- /warehouse/stock-ledger/…：本模块的命名空间
- /warehouse/inventory/by-location…、/history…、/metrics…、/thresholds…：挂在库存命名空间上
权限：查看 inventory_read（阈值建议同），改阈值 inventory_edit。范围同库存列表：X-WAREHOUSE-ID / warehouse_id → 该仓库，
否则调用方可访问的仓库；按日期 / 单商品的接口要求指定一个仓库（调用方只能访问一个仓库时自动用它）。
"""
import csv
import io
from datetime import date, timedelta

from flask import g, make_response, request
from flask_restx import Resource

from extensions.db import db
from extensions.error import BadRequestException, NotFoundException
from system.common import paginate, permission_required
from warehouse.common import add_warehouse_filter, clock, require_warehouse_scope, warehouse_required
from warehouse.inventory.schemas import api_ns as inventory_ns
from .models import IN_WAREHOUSE_STOCK_TYPES, LOCATION_STOCK_TYPES, STOCK_TYPES
from .queries import EXPORT_TOO_LARGE, MAX_EXPORT_ROWS, QUERY_INVALID
from .schemas import api_ns, ledger_pagination_model, ledger_parser, summary_model, summary_parser

READ = ["all_access", "company_all_access", "inventory_read"]
EDIT = ["all_access", "company_all_access", "inventory_edit"]
MAX_SERIES_DAYS = 400
MAX_SERIES_GOODS = 100


# ---------------------------------------------------------------------------
# 参数
# ---------------------------------------------------------------------------
def _bad(message, field):
    return BadRequestException(message, QUERY_INVALID, field=field)


def _date(value, field, default=None):
    if value in (None, ''):
        return default
    try:
        return date.fromisoformat(str(value).strip())
    except ValueError:
        raise _bad(f"{field} must be a date (YYYY-MM-DD)", field)


def _int(name, default=None, low=None, high=None):
    raw = request.args.get(name)
    if raw in (None, ''):
        return default
    try:
        value = int(raw)
    except ValueError:
        raise _bad(f"{name} must be an integer", name)
    if (low is not None and value < low) or (high is not None and value > high):
        raise _bad(f"{name} must be between {low} and {high}", name)
    return value


def _bool(name, default=False):
    raw = request.args.get(name)
    if raw in (None, ''):
        return default
    return str(raw).strip().lower() in ('1', 'true', 'yes', 'on')


def _list(name):
    raw = request.args.get(name)
    return [part.strip() for part in raw.split(',') if part.strip()] if raw else None


def _int_list(name, limit=None):
    values = _list(name)
    if values is None:
        return None
    try:
        result = [int(v) for v in values]
    except ValueError:
        raise _bad(f"{name} must be comma separated integers", name)
    if limit is not None and len(result) > limit:
        raise _bad(f"at most {limit} {name}", name)
    return result


def _stock_types(name='stock_types', default=None):
    values = _list(name)
    if values is None:
        return default
    unknown = [v for v in values if v not in STOCK_TYPES]
    if unknown:
        raise _bad(f"unknown stock type(s): {', '.join(unknown)}", name)
    return values


def _page():
    page = _int('page', 1, 1)
    per_page = _int('per_page', 50, 1, 500)
    return page, per_page


def _single_warehouse():
    """按日期 / 单商品的接口：一个仓库（X-WAREHOUSE-ID / warehouse_id；调用方只能访问一个仓库时自动用它）"""
    from warehouse.warehouse.models import Warehouse
    warehouse_id = getattr(g, 'warehouse_id', None)
    if warehouse_id is None:
        accessible = getattr(g, 'accessible_warehouses', None)
        if accessible and len(accessible) == 1:
            warehouse_id = accessible[0].id
    if warehouse_id is None:
        raise _bad("warehouse_id (or the X-WAREHOUSE-ID header) is required", 'warehouse_id')
    require_warehouse_scope(warehouse_id, 'Warehouse')
    return db.session.get(Warehouse, warehouse_id)


def _goods_filter_ids(warehouse, keyword=None, goods_id=None, goods_ids=None):
    """商品筛选 → 商品 id 列表（None = 不筛）；商品必须属于仓库所在公司"""
    from warehouse.goods.models import Goods
    from .queries import _keyword_goods
    if not keyword and goods_id is None and goods_ids is None:
        return None
    query = db.session.query(Goods.id).filter(Goods.company_id == warehouse.company_id)
    if goods_id is not None:
        query = query.filter(Goods.id == goods_id)
    if goods_ids is not None:
        query = query.filter(Goods.id.in_(goods_ids))
    if keyword:
        query = _keyword_goods(query, keyword)
    return [row[0] for row in query.all()]


def _goods_map(ids):
    from warehouse.goods.models import Goods
    ids = list({i for i in ids if i is not None})
    if not ids:
        return {}
    return {goods.id: goods for goods in Goods.query.filter(Goods.id.in_(ids)).all()}


def _location_map(ids):
    from warehouse.location.models import Location
    ids = list({i for i in ids if i is not None})
    if not ids:
        return {}
    return {loc.id: loc for loc in Location.query.filter(Location.id.in_(ids)).all()}


def _goods_ref(goods):
    if goods is None:
        return None
    return {'id': goods.id, 'code': goods.code, 'name': goods.name}


def _location_ref(location):
    if location is None:
        return None
    return {'id': location.id, 'code': location.code, 'location_type': location.location_type}


def _csv_response(header, rows, filename):
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator='\n')
    writer.writerow(header)
    writer.writerows(rows)
    response = make_response('﻿' + buffer.getvalue())
    response.headers['Content-Type'] = 'text/csv; charset=utf-8'
    response.headers['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response


def _ledger_filters(args):
    filters = {
        'company_id': args.get('company_id'), 'goods_id': args.get('goods_id'), 'goods_code': args.get('goods_code'),
        'keyword': args.get('keyword'), 'lot_id': args.get('lot_id'), 'lot_no': args.get('lot_no'),
        'location_id': args.get('location_id'), 'location_code': args.get('location_code'),
        'doc_id': args.get('doc_id'), 'doc_no': args.get('doc_no'), 'doc_line_id': args.get('doc_line_id'),
        'operator_id': args.get('operator_id'), 'trace_id': args.get('trace_id'),
        'stock_types': args.get('stock_types'), 'movement_types': args.get('movement_types'),
        'doc_types': args.get('doc_type'), 'sources': args.get('source'),
        'time_field': args.get('time_field'), 'order': args.get('order'),
        **{key: args.get(key) for key in ('category', 'brand', 'category_missing', 'brand_missing')},
    }
    for field in ('start_time', 'end_time'):
        value = args.get(field)
        if value:
            try:
                filters[field] = date.fromisoformat(value) if len(value.strip()) == 10 else value
                if not isinstance(filters[field], date):
                    clock.parse_input_datetime(value)
            except ValueError:
                raise _bad(f"{field} must be ISO 8601", field)
    unknown = [t for t in (filters['stock_types'] or []) if t not in STOCK_TYPES]
    if unknown:
        raise _bad(f"unknown stock type(s): {', '.join(unknown)}", 'stock_types')
    return add_warehouse_filter(filters)


# ---------------------------------------------------------------------------
# 库存流水
# ---------------------------------------------------------------------------
@api_ns.doc(security="jsonWebToken")
@api_ns.route('/')
class StockLedgerList(Resource):

    @permission_required(READ)
    @warehouse_required()
    @api_ns.expect(ledger_parser)
    @api_ns.marshal_with(ledger_pagination_model)
    def get(self):
        """Stock ledger: every physical stock movement, one line per bucket (source −N, destination +N)

        Buckets are warehouse × goods × lot × location (location stock types only) × stock type. Lines are
        append-only; corrections are `reconcile` lines. Default order: `occurred_at` (business time) descending;
        `time_field=posted` filters / orders by `posted_at` (booking time, the one daily balances use).
        Filters: warehouse (scope as GET /inventory/), goods, lot, location, stock type, movement type, document
        (doc_type + doc_id / doc_no), operator, source, trace_id, time range.
        """
        from .queries import ledger_query
        args = ledger_parser.parse_args()
        query = ledger_query(_ledger_filters(args))
        result = paginate(query, args.get('page'), args.get('per_page'), args.get('all', False))
        from .overview import enrich_ledger_lines
        enrich_ledger_lines(result['items'])
        return result, 200


@api_ns.doc(security="jsonWebToken")
@api_ns.route('/export')
class StockLedgerExport(Resource):

    @permission_required(READ)
    @warehouse_required()
    @api_ns.expect(ledger_parser)
    @api_ns.produces(['text/csv'])
    def get(self):
        """Export the stock ledger as CSV (UTF-8 with BOM; same filters as GET /stock-ledger/; times in the
        warehouse time zone). More than 50,000 lines → 400 15020 (narrow the filters)"""
        from .queries import ledger_query
        args = ledger_parser.parse_args()
        query = ledger_query(_ledger_filters(args))
        total = query.order_by(None).count()
        if total > MAX_EXPORT_ROWS:
            raise BadRequestException(f"{total} lines match; export at most {MAX_EXPORT_ROWS}", EXPORT_TOO_LARGE,
                                      details={'total': total, 'max_rows': MAX_EXPORT_ROWS})
        header = ['id', 'posted_at', 'occurred_at', 'warehouse_id', 'goods_code', 'goods_name', 'lot_id',
                  'location_code', 'stock_type', 'quantity', 'balance_after', 'goods_total_after', 'movement_type',
                  'doc_type', 'doc_id', 'doc_no', 'doc_line_id', 'reason', 'operator', 'source', 'trace_id', 'remark',
                  'category_balance_after', 'lot_no']
        rows = []
        from .overview import enrich_ledger_lines
        lines = query.all()
        enrich_ledger_lines(lines)
        for line in lines:
            rows.append([
                line.id, clock.local_iso(line.posted_at, line.warehouse_id),
                clock.local_iso(line.occurred_at, line.warehouse_id), line.warehouse_id,
                line.goods.code if line.goods else '', line.goods.name if line.goods else '', line.lot_id or '',
                line.location.code if line.location else '', line.stock_type, line.quantity, line.balance_after,
                line.goods_total_after, line.movement_type, line.doc_type or '', line.doc_id or '', line.doc_no or '',
                line.doc_line_id or '', line.reason or '', line.operator.user_name if line.operator else '',
                line.source or '', line.trace_id, line.remark or '', line.category_balance_after, line.lot_no or '',
            ])
        return _csv_response(header, rows, 'stock_ledger.csv')


def _summary_range(args, warehouse_id):
    now = clock.now()
    start_raw, end_raw = args.get('start_time'), args.get('end_time')
    try:
        if start_raw:
            start = clock.parse_input_datetime(start_raw, warehouse_id)
        else:
            today = clock.local_today(warehouse_id)
            start = clock.local_midnight(today.replace(day=1), warehouse_id)
        if end_raw:
            if len(end_raw.strip()) == 10:
                end = clock.local_midnight(date.fromisoformat(end_raw.strip()) + timedelta(days=1), warehouse_id)
            else:
                end = clock.parse_input_datetime(end_raw, warehouse_id) + timedelta(microseconds=1)
        else:
            end = now + timedelta(microseconds=1)
    except ValueError:
        raise _bad("start_time / end_time must be ISO 8601", 'start_time')
    if end <= start:
        raise _bad("end_time must be after start_time", 'end_time')
    return start, end


@api_ns.doc(security="jsonWebToken")
@api_ns.route('/summary')
class StockLedgerSummary(Resource):

    @permission_required(READ)
    @warehouse_required()
    @api_ns.expect(summary_parser)
    @api_ns.marshal_with(summary_model)
    def get(self):
        """In / out summary of a period (opening, inbound, outbound, closing; booking time `posted_at`)

        `stock_types` (default the in-warehouse types = total_stock) is the boundary: movements inside it (putaway,
        picking, packing …) are not counted as in / out; `inbound_by_movement` / `outbound_by_movement` break the
        totals down (sort_in / adjust_gain / reconcile … and ship / adjust_loss …). `group_by=goods_stock_type`
        gives one row per goods × stock type (each type is its own boundary). With `location_id` every line in or
        out of the location counts.
        """
        return _stock_summary_result(summary_parser.parse_args()), 200


def _stock_summary_result(args, export=False):
    from .queries import ledger_summary
    filters = add_warehouse_filter({
        'company_id': args.get('company_id'), 'goods_id': args.get('goods_id'),
        'goods_code': args.get('goods_code'), 'keyword': args.get('keyword'),
        'location_id': args.get('location_id'),
        'goods_ids': [int(v) for v in args['goods_ids']] if args.get('goods_ids') else None,
        **{key: args.get(key) for key in ('category', 'brand', 'category_missing', 'brand_missing')},
    })
    stock_types = args.get('stock_types') or list(IN_WAREHOUSE_STOCK_TYPES)
    unknown = [t for t in stock_types if t not in STOCK_TYPES]
    if unknown:
        raise _bad(f"unknown stock type(s): {', '.join(unknown)}", 'stock_types')
    start, end = _summary_range(args, filters.get('warehouse_id'))
    items = ledger_summary(filters, start, end, stock_types, args.get('group_by') or 'goods')
    totals = {key: sum(item[key] for item in items) for key in ('opening', 'inbound', 'outbound', 'closing')}
    for direction in ('inbound_by_movement', 'outbound_by_movement', 'opening_by_stock_type', 'closing_by_stock_type'):
        breakdown = {}
        for item in items:
            for movement, quantity in item[direction].items():
                breakdown[movement] = breakdown.get(movement, 0) + quantity
        totals[direction] = breakdown
    dimension = args.get('group_by') or 'goods'
    if dimension in ('category', 'brand'):
        goods = _goods_map(item['goods_id'] for item in items)
        groups = {}
        for item in items:
            value = getattr(goods[item['goods_id']], dimension) or None
            key = (item['warehouse_id'], value)
            if key not in groups:
                groups[key] = dict(warehouse_id=key[0], group_dimension=dimension, group_value=value,
                                   goods_count=0, opening=0, inbound=0, outbound=0, closing=0,
                                   inbound_by_movement={}, outbound_by_movement={},
                                   opening_by_stock_type={}, closing_by_stock_type={})
            group = groups[key]
            group['goods_count'] += 1
            for metric in ('opening', 'inbound', 'outbound', 'closing'):
                group[metric] += item[metric]
            for direction in ('inbound_by_movement', 'outbound_by_movement', 'opening_by_stock_type', 'closing_by_stock_type'):
                for movement, quantity in item[direction].items():
                    group[direction][movement] = group[direction].get(movement, 0) + quantity
        items = sorted(groups.values(), key=lambda row: (row['warehouse_id'], row['group_value'] or ''))
    page = max(1, args.get('page') or 1)
    per_page = min(500, max(1, args.get('per_page') or 50))
    if export and len(items) > MAX_EXPORT_ROWS:
        raise BadRequestException('Too many summary rows to export', EXPORT_TOO_LARGE)
    chunk = items if export else items[(page - 1) * per_page: page * per_page]
    goods = _goods_map(i.get('goods_id') for i in chunk)
    for item in chunk:
        if item.get('goods_id') is not None:
            item['goods'] = _goods_ref(goods.get(item['goods_id']))
    return {'start_time': start, 'end_time': end - timedelta(microseconds=1),
            'timezone': clock.warehouse_timezone_name(filters.get('warehouse_id')), 'stock_types': stock_types,
            'group_by': args.get('group_by') or 'goods', 'total': len(items), 'page': page, 'per_page': per_page,
            'items': chunk, 'totals': totals}


# ---------------------------------------------------------------------------
# 按库位查看库存
# ---------------------------------------------------------------------------
def _item_row(stock, goods):
    return {'goods': _goods_ref(goods), 'goods_id': goods.id, 'lot_id': stock.lot_id, 'lot': None,
            'quantity': int(stock.quantity)}


def _summary_row(summary, include_items):
    location = summary['location']
    row = {
        'location': {**_location_ref(location), 'is_active': location.is_active,
                     'warehouse_id': location.warehouse_id},
        'stock_type': summary['stock_type'], 'goods_count': summary['goods_count'], 'quantity': summary['quantity'],
        'lot_count': summary['lot_count'], 'is_empty': summary['is_empty'], 'capacity_m3': summary['capacity_m3'],
        'used_volume_m3': summary['used_volume_m3'], 'volume_utilization': summary['volume_utilization'],
        'volume_unknown_quantity': summary['volume_unknown_quantity'],
    }
    if include_items:
        row['items'] = [_item_row(stock, goods) for stock, goods in summary['items']]
    return row


def _location_filters():
    from warehouse.location.models import Location
    location_type = request.args.get('location_type')
    if location_type and location_type not in Location.LOCATION_TYPES:
        raise _bad("location_type must be standard / damaged / return", 'location_type')
    return add_warehouse_filter({
        'location_id': _int('location_id'), 'location_type': location_type,
        'keyword': request.args.get('keyword'), 'include_empty': _bool('include_empty', True),
        'include_inactive': _bool('include_inactive', False),
    })


@inventory_ns.doc(security="jsonWebToken")
@inventory_ns.route('/by-location')
class InventoryByLocation(Resource):

    @permission_required(READ)
    @warehouse_required()
    def get(self):
        """Stock by location (location × goods; × lot once lots are enabled) with occupancy

        Query: `warehouse_id` (or X-WAREHOUSE-ID; else every accessible warehouse), `location_id`, `location_type`,
        `keyword` (location code, or goods code / name on the location), `include_empty` (default true),
        `include_inactive` (default false), `include_items` (true: embed the goods rows of each location),
        `group_by=location` (default) | `shelf` (aggregate by shelf = location code prefix: `shelf_prefix_length`
        characters, or the first `shelf_levels` (default 1) parts split by `shelf_separator` (default `-`)),
        `page` / `per_page`.

        Response: `{summary: {location_count, occupied_count, empty_count, occupancy_rate (share of locations with
        stock), quantity, sized_location_count, capacity_m3, used_volume_m3, volume_utilization (only locations
        with a capacity or width × depth × height; goods volume = length × width × height)}, total, page, per_page,
        items}`; location items `{location {id, code, location_type, is_active, warehouse_id}, stock_type,
        goods_count, quantity, lot_count, is_empty, capacity_m3, used_volume_m3, volume_utilization,
        volume_unknown_quantity (items without goods dimensions), items?}`; shelf items `{shelf, location_count,
        occupied_count, occupancy_rate, quantity, goods_count, capacity_m3, used_volume_m3, volume_utilization}`
        """
        from .queries import location_stock, location_summary, occupancy, shelf_of
        filters = _location_filters()
        summaries = [location_summary(location, items) for location, items in location_stock(filters)]
        page, per_page = _page()
        group_by = request.args.get('group_by') or 'location'
        if group_by not in ('location', 'shelf'):
            raise _bad("group_by must be location / shelf", 'group_by')
        if group_by == 'shelf':
            separator = request.args.get('shelf_separator', '-')
            levels = _int('shelf_levels', 1, 1, 10)
            prefix_length = _int('shelf_prefix_length', None, 1, 50)
            shelves = {}
            for summary in summaries:
                shelves.setdefault(shelf_of(summary['location'].code, separator, levels, prefix_length), []).append(
                    summary)
            items = []
            for shelf, group in sorted(shelves.items()):
                stats = occupancy(group)
                items.append({'shelf': shelf, 'location_count': stats['location_count'],
                              'occupied_count': stats['occupied_count'], 'occupancy_rate': stats['occupancy_rate'],
                              'quantity': stats['quantity'],
                              'goods_count': len({g.id for s in group for _st, g in s['items']}),
                              'capacity_m3': stats['capacity_m3'], 'used_volume_m3': stats['used_volume_m3'],
                              'volume_utilization': stats['volume_utilization']})
        else:
            include_items = _bool('include_items', False)
            items = [_summary_row(summary, include_items) for summary in summaries]
        return {'summary': occupancy(summaries), 'total': len(items), 'page': page, 'per_page': per_page,
                'items': items[(page - 1) * per_page: page * per_page]}, 200


@inventory_ns.doc(security="jsonWebToken")
@inventory_ns.route('/by-location/items')
class InventoryByLocationItems(Resource):

    @permission_required(READ)
    @warehouse_required()
    def get(self):
        """Location × goods rows (× lot once lots are enabled), sorted by location code then goods code

        Same query parameters as GET /inventory/by-location (empty locations are not listed) plus `goods_id`.
        Items `{location {id, code, location_type}, goods {id, code, name}, goods_id, lot_id, lot, quantity,
        stock_type}`
        """
        from .queries import location_stock
        filters = _location_filters()
        filters['include_empty'] = False
        goods_id = _int('goods_id')
        rows = []
        for location, items in location_stock(filters):
            for stock, goods in items:
                if goods_id is not None and goods.id != goods_id:
                    continue
                rows.append({'location': _location_ref(location), **_item_row(stock, goods),
                             'stock_type': LOCATION_STOCK_TYPES.get(location.location_type)})
        page, per_page = _page()
        return {'total': len(rows), 'page': page, 'per_page': per_page,
                'items': rows[(page - 1) * per_page: page * per_page]}, 200


# ---------------------------------------------------------------------------
# 历史库存
# ---------------------------------------------------------------------------
HISTORY_GROUPS = ('goods', 'goods_location', 'location', 'goods_lot')


def _history_rows(warehouse, balances, group_by, stock_types):
    from .history import group_balances
    grouped = group_balances(balances, group_by)
    goods = _goods_map(key[0] for key in grouped if group_by != 'location')
    locations = _location_map(key[0] if group_by == 'location' else (key[1] if group_by == 'goods_location' else None)
                              for key in grouped)
    rows = []
    for key, values in grouped.items():
        if stock_types:
            values = {t: (values[t] if t in stock_types else 0) for t in STOCK_TYPES}
            values['total'] = sum(values[t] for t in IN_WAREHOUSE_STOCK_TYPES)
        if not any(values[t] for t in STOCK_TYPES):
            continue
        row = {'warehouse_id': warehouse.id}
        if group_by == 'location':
            row['location'] = _location_ref(locations.get(key[0]))
        else:
            row['goods'] = _goods_ref(goods.get(key[0]))
            if group_by == 'goods_location':
                row['location'] = _location_ref(locations.get(key[1]))
            if group_by == 'goods_lot':
                row['lot'] = None if key[1] is None else {'id': key[1]}
        row.update({t: values[t] for t in STOCK_TYPES})
        row['total'] = values['total']
        rows.append(row)
    rows.sort(key=lambda r: ((r.get('goods') or {}).get('code') or '', (r.get('location') or {}).get('code') or ''))
    return rows


def _history_args(warehouse):
    group_by = request.args.get('group_by') or 'goods'
    if group_by not in HISTORY_GROUPS:
        raise _bad(f"group_by must be one of {', '.join(HISTORY_GROUPS)}", 'group_by')
    goods_ids = _goods_filter_ids(warehouse, request.args.get('keyword'), _int('goods_id'))
    return group_by, goods_ids, _stock_types(), _int('location_id')


def _filter_location(balances, location_id):
    if location_id is None:
        return balances
    return {k: v for k, v in balances.items() if k[2] == location_id}


def _history_result(warehouse):
    from .history import HISTORY_START, balances_on
    day = _date(request.args.get('date'), 'date')
    if day is None:
        raise _bad("date is required (YYYY-MM-DD, warehouse local date)", 'date')
    today = clock.local_today(warehouse)
    if day > today:
        raise _bad("date is in the future", 'date')
    group_by, goods_ids, stock_types, location_id = _history_args(warehouse)
    if goods_ids == []:
        balances, source, coverage = {}, 'none', 'none'
    else:
        balances, source, coverage = balances_on(warehouse.id, day, goods_ids)
    rows = _history_rows(warehouse, _filter_location(balances, location_id), group_by, stock_types)
    return {'date': day.isoformat(), 'warehouse_id': warehouse.id, 'coverage': coverage, 'source': source,
            'history_start': HISTORY_START.isoformat(), 'group_by': group_by}, rows


@inventory_ns.doc(security="jsonWebToken")
@inventory_ns.route('/history')
class InventoryHistory(Resource):

    @permission_required(READ)
    @warehouse_required()
    def get(self):
        """Stock at the end of a warehouse-local day (24:00), every stock type

        Query: `date` (YYYY-MM-DD, required; today = live stock), `warehouse_id` (or X-WAREHOUSE-ID; required unless
        the caller has one warehouse), `goods_id`, `keyword`, `location_id`, `stock_types` (comma separated; others
        shown as 0), `group_by` = goods (default) | goods_location | location | goods_lot, `page` / `per_page`.

        Response `{date, warehouse_id, coverage, source, history_start, group_by, total, page, per_page, items}`:
        `coverage` full (every stock type) / on_shelf_only (from the old daily snapshots: location stock only, no
        pending / picked / packed) / none; `source` realtime / ledger / derived / computed / legacy_snapshot / none.
        Items `{goods {id, code, name} | location {id, code, location_type}, onhand, damaged, return, pending,
        pending_damaged, picked, packed, in_transit, total}` (total = in the warehouse, without in_transit).
        Booking time decides the day: a shipment recorded later with an earlier shipped time counts on the day it
        was recorded (see the ledger for the actual time).
        """
        warehouse = _single_warehouse()
        meta, rows = _history_result(warehouse)
        page, per_page = _page()
        return {**meta, 'total': len(rows), 'page': page, 'per_page': per_page,
                'items': rows[(page - 1) * per_page: page * per_page]}, 200


@inventory_ns.doc(security="jsonWebToken")
@inventory_ns.route('/history/export')
class InventoryHistoryExport(Resource):

    @permission_required(READ)
    @warehouse_required()
    @inventory_ns.produces(['text/csv'])
    def get(self):
        """Export GET /inventory/history as CSV (UTF-8 with BOM, same query parameters, all rows)"""
        warehouse = _single_warehouse()
        meta, rows = _history_result(warehouse)
        header = ['date', 'coverage', 'goods_code', 'goods_name', 'location_code', *STOCK_TYPES, 'total']
        data = [[meta['date'], meta['coverage'], (r.get('goods') or {}).get('code', ''),
                 (r.get('goods') or {}).get('name', ''), (r.get('location') or {}).get('code', ''),
                 *[r[t] for t in STOCK_TYPES], r['total']] for r in rows]
        return _csv_response(header, data, f"inventory_history_{meta['date']}.csv")


@inventory_ns.doc(security="jsonWebToken")
@inventory_ns.route('/history/at')
class InventoryHistoryAt(Resource):

    @permission_required(READ)
    @warehouse_required()
    def get(self):
        """Stock at any moment (sum of the ledger booked before it)

        Query: `time` (ISO 8601, required; without offset = warehouse time zone), warehouse, `goods_id`, `keyword`,
        `location_id`, `stock_types`, `group_by`, `page` / `per_page`. Before the ledger starts → coverage none.
        """
        from .history import balances_at_time
        warehouse = _single_warehouse()
        raw = request.args.get('time')
        if not raw:
            raise _bad("time is required (ISO 8601)", 'time')
        try:
            moment = clock.parse_input_datetime(raw, warehouse)
        except ValueError:
            raise _bad("time must be ISO 8601", 'time')
        if moment > clock.now():
            raise _bad("time is in the future", 'time')
        group_by, goods_ids, stock_types, location_id = _history_args(warehouse)
        balances, coverage = ({}, 'none') if goods_ids == [] else balances_at_time(warehouse.id, moment, goods_ids)
        rows = _history_rows(warehouse, _filter_location(balances, location_id), group_by, stock_types)
        page, per_page = _page()
        return {'time': clock.utc_iso(moment), 'warehouse_id': warehouse.id, 'coverage': coverage,
                'group_by': group_by, 'total': len(rows), 'page': page, 'per_page': per_page,
                'items': rows[(page - 1) * per_page: page * per_page]}, 200


def _series_range(warehouse, default_days=90):
    today = clock.local_today(warehouse)
    date_to = _date(request.args.get('to'), 'to', today)
    date_from = _date(request.args.get('from'), 'from', date_to - timedelta(days=default_days - 1))
    if date_to > today:
        date_to = today
    if date_from > date_to:
        raise _bad("from must not be after to", 'from')
    if (date_to - date_from).days + 1 > MAX_SERIES_DAYS:
        raise _bad(f"at most {MAX_SERIES_DAYS} days", 'from')
    return date_from, date_to


@inventory_ns.doc(security="jsonWebToken")
@inventory_ns.route('/history/series')
class InventoryHistorySeries(Resource):

    @permission_required(READ)
    @warehouse_required()
    def get(self):
        """Daily end-of-day stock series (trend charts, per-goods sparklines)

        Query: warehouse, `from` / `to` (YYYY-MM-DD, default the last 90 days, at most 400 days; today = live),
        `goods_id` or `goods_ids` (comma separated, at most 100; none = the whole warehouse), `stock_types`
        (default the in-warehouse types).
        Response `{warehouse_id, from, to, stock_types, series: [{date, total}]}` (whole warehouse / one goods) or
        `{…, goods: [{goods_id, series: [{date, total}]}]}` with goods_ids. Days before the ledger with only old
        snapshots count location stock only; every point includes coverage full / on_shelf_only / none.
        For compatibility totals remain 0 when coverage is none; charts must render these as gaps.
        """
        from .history import daily_totals, daily_coverage
        warehouse = _single_warehouse()
        date_from, date_to = _series_range(warehouse)
        stock_types = _stock_types(default=list(IN_WAREHOUSE_STOCK_TYPES))
        goods_id = _int('goods_id')
        goods_ids = _int_list('goods_ids', MAX_SERIES_GOODS)
        ids = goods_ids if goods_ids is not None else ([goods_id] if goods_id is not None else None)
        if ids is not None:
            allowed = set(_goods_filter_ids(warehouse, goods_ids=ids) or [])
            ids = [i for i in ids if i in allowed]
        totals = daily_totals(warehouse.id, date_from, date_to, ids, stock_types)
        coverage = daily_coverage(warehouse.id, date_from, date_to, ids, per_goods=goods_ids is not None)
        days = []
        day = date_from
        while day <= date_to:
            days.append(day)
            day += timedelta(days=1)
        result = {'warehouse_id': warehouse.id, 'from': date_from.isoformat(), 'to': date_to.isoformat(),
                  'stock_types': stock_types}
        if goods_ids is not None:
            result['goods'] = [{'goods_id': gid, 'series': [{'date': d.isoformat(),
                                                             'total': totals.get(d, {}).get(gid, 0), 'coverage': coverage[gid][d]} for d in days]}
                               for gid in ids]
        else:
            result['series'] = [{'date': d.isoformat(), 'total': sum(totals.get(d, {}).values()), 'coverage': coverage[d]} for d in days]
        return result, 200


@inventory_ns.doc(security="jsonWebToken")
@inventory_ns.route('/history/flows')
class InventoryHistoryFlows(Resource):

    @permission_required(READ)
    @warehouse_required()
    def get(self):
        """Daily inbound / outbound quantities (trend charts)

        Query: warehouse, `from` / `to` (default the last 30 days, at most 400), `goods_id`, `keyword`.
        Items `{date, inbound (= sort_in + adjust_gain), sort_in, adjust_gain, outbound (shipping completed),
        adjust_loss}` by the warehouse-local date of the business time; days before the ledger come from the
        documents (ASN completion, DN shipping).
        """
        from .queries import daily_flows
        warehouse = _single_warehouse()
        date_from, date_to = _series_range(warehouse, default_days=30)
        goods_ids = _goods_filter_ids(warehouse, request.args.get('keyword'), _int('goods_id'))
        items = [] if goods_ids == [] else daily_flows(warehouse.id, date_from, date_to, goods_ids)
        return {'warehouse_id': warehouse.id, 'from': date_from.isoformat(), 'to': date_to.isoformat(),
                'items': items}, 200


# ---------------------------------------------------------------------------
# 库存分析
# ---------------------------------------------------------------------------
METRIC_FIELDS = ('total_stock', 'available_stock_for_sale', 'out_qty_7d', 'out_qty_30d', 'out_qty_90d',
                 'sales_qty_30d', 'avg_stock_30d', 'turnover_days_30d', 'days_of_supply', 'weighted_age_days',
                 'out_qty_90d_rank', 'abc_class', 'idle_days', 'status')
METRIC_SORTS = ('turnover_days_30d', 'weighted_age_days', 'out_qty_30d', 'out_qty_90d', 'total_stock', 'idle_days',
                'days_of_supply', 'out_qty_90d_rank')


def _metric_row(row):
    from .metrics import AGE_BUCKETS
    item = {'goods': _goods_ref(row.goods), 'goods_id': row.goods_id}
    item.update({field: getattr(row, field) for field in METRIC_FIELDS})
    item['last_in_at'] = clock.utc_iso(row.last_in_at)
    item['last_out_at'] = clock.utc_iso(row.last_out_at)
    item['oldest_in_at'] = clock.utc_iso(row.oldest_in_at)
    item['age_buckets'] = {column: getattr(row, column) for _low, _high, column in AGE_BUCKETS}
    item['age_buckets']['unknown'] = row.age_unknown
    return item


def _metric_chart_summary(rows):
    """Warehouse-wide chart totals; independent of table filters and pagination."""
    from .metrics import AGE_BUCKETS
    buckets = {column: sum(getattr(row, column) or 0 for row in rows) for _, _, column in AGE_BUCKETS}
    buckets['unknown'] = sum(row.age_unknown or 0 for row in rows)
    sku_counts = {column: sum(1 for row in rows if (getattr(row, column) or 0) > 0)
                  for _, _, column in AGE_BUCKETS}
    sku_counts['unknown'] = sum(1 for row in rows if (row.age_unknown or 0) > 0)
    aged = sorted((row for row in rows if row.age_over_180 > 0),
                  key=lambda row: (-row.age_over_180, row.goods_id))
    aged_top = [dict(goods_id=row.goods_id, goods=_goods_ref(row.goods), quantity=row.age_over_180,
                     weighted_age_days=row.weighted_age_days, last_out_at=clock.utc_iso(row.last_out_at))
                for row in aged[:4]]
    quantities = sorted((max(0, row.out_qty_90d or 0) for row in rows), reverse=True)
    outbound = sum(quantities)
    cumulative = 0
    curve = [{'sku_share': 0, 'outbound_share': 0}]
    for index, quantity in enumerate(quantities if outbound else [], 1):
        cumulative += quantity
        # At most 101 points, including the final point, for large catalogues.
        if index == len(quantities) or index * 100 // len(quantities) > (index - 1) * 100 // len(quantities):
            curve.append({'sku_share': round(index / len(quantities), 4),
                          'outbound_share': round(cumulative / outbound, 4)})
    return {'total_stock': sum(row.total_stock for row in rows), 'age_buckets': buckets, 'age_sku_counts': sku_counts, 'aged_top': aged_top,
            'out_qty_90d': outbound, 'abc_curve': curve, 'abc_sku_total': len(rows),
            'abc_unclassified': sum(1 for row in rows if row.abc_class not in ('A', 'B', 'C')),
            'abc_outbound': {label: sum(row.out_qty_90d for row in rows if row.abc_class == label)
                             for label in ('A', 'B', 'C')}}


@inventory_ns.doc(security="jsonWebToken")
@inventory_ns.route('/metrics')
class InventoryMetrics(Resource):

    @permission_required(READ)
    @warehouse_required()
    def get(self):
        """Stock analysis from the nightly summary (data as of the end of `metric_date`, normally yesterday)

        Query: warehouse, `date` (default the latest summary), `keyword`, `status` (normal / dead / empty), `abc`
        (A / B / C), `age_min` (weighted age ≥ N days), `sort` (turnover_days_30d / weighted_age_days / out_qty_30d /
        out_qty_90d / total_stock / idle_days / days_of_supply / out_qty_90d_rank; default goods code), `order`
        (asc / desc), `page` / `per_page`.
        Rules: outbound = shipping completed; dead = stock, no outbound in 90 days and the oldest layer older than
        90 days; ABC by the 90-day outbound (cumulative 70% A / 90% B / rest C; none without outbound); age buckets
        0–30 / 31–90 / 91–180 / 180+ days (first in, first out over sort-in / adjust-gain layers; `unknown` = items
        older than every record); turnover days = 30-day average stock ÷ 30-day average daily outbound.
        Response `{metric_date, warehouse_id, summary {sku_with_stock, moving_sku (outbound in 30 days),
        moving_rate, dead_sku, abc {A, B, C}}, total, page, per_page, items}`.
        """
        from warehouse.goods.models import Goods
        from .metrics import latest_metric_date
        from .models import InventoryMetricsDaily
        from .queries import _keyword_goods
        warehouse = _single_warehouse()
        day = _date(request.args.get('date'), 'date') or latest_metric_date(warehouse.id)
        age_bucket = request.args.get('age_bucket')
        age_columns = {'age_0_30': 'age_0_30', 'age_31_90': 'age_31_90',
                       'age_91_180': 'age_91_180', 'age_over_180': 'age_over_180', 'unknown': 'age_unknown'}
        if age_bucket and age_bucket not in age_columns:
            raise _bad('Invalid age bucket', 'age_bucket')
        page, per_page = _page()
        empty = {'metric_date': None if day is None else day.isoformat(), 'warehouse_id': warehouse.id,
                 'summary': {**_metric_chart_summary([]), 'sku_with_stock': 0, 'moving_sku': 0, 'moving_rate': None, 'dead_sku': 0,
                             'abc': {'A': 0, 'B': 0, 'C': 0}},
                 'total': 0, 'page': page, 'per_page': per_page, 'items': []}
        if day is None:
            return empty, 200
        base = InventoryMetricsDaily.query.filter(InventoryMetricsDaily.warehouse_id == warehouse.id,
                                                  InventoryMetricsDaily.metric_date == day)
        all_rows = base.all()
        with_stock = [r for r in all_rows if r.total_stock > 0]
        moving = [r for r in with_stock if r.out_qty_30d > 0]
        summary = {**_metric_chart_summary(all_rows), 'sku_with_stock': len(with_stock), 'moving_sku': len(moving),
                   'moving_rate': round(len(moving) / len(with_stock), 4) if with_stock else None,
                   'dead_sku': sum(1 for r in all_rows if r.status == 'dead'),
                   'abc': {label: sum(1 for r in all_rows if r.abc_class == label) for label in ('A', 'B', 'C')}}
        query = base.join(Goods, Goods.id == InventoryMetricsDaily.goods_id)
        if request.args.get('keyword'):
            query = _keyword_goods(query, request.args['keyword'])
        status = request.args.get('status')
        if status:
            if status not in ('normal', 'dead', 'empty'):
                raise _bad("status must be normal / dead / empty", 'status')
            query = query.filter(InventoryMetricsDaily.status == status)
        abc = request.args.get('abc')
        if abc:
            if abc not in ('A', 'B', 'C'):
                raise _bad("abc must be A / B / C", 'abc')
            query = query.filter(InventoryMetricsDaily.abc_class == abc)
        if age_bucket:
            query = query.filter(getattr(InventoryMetricsDaily, age_columns[age_bucket]) > 0)
        age_min = _int('age_min', None, 0)
        if age_min is not None:
            query = query.filter(InventoryMetricsDaily.weighted_age_days >= age_min)
        sort = request.args.get('sort')
        order = request.args.get('order') or 'desc'
        if sort:
            if sort not in METRIC_SORTS:
                raise _bad(f"sort must be one of {', '.join(METRIC_SORTS)}", 'sort')
            column = getattr(InventoryMetricsDaily, sort)
            query = query.order_by(column.is_(None), column.asc() if order == 'asc' else column.desc(), Goods.code)
        else:
            query = query.order_by(Goods.code)
        total = query.count()
        rows = query.offset((page - 1) * per_page).limit(per_page).all()
        return {**empty, 'summary': summary, 'total': total, 'items': [_metric_row(r) for r in rows]}, 200


@inventory_ns.doc(security="jsonWebToken")
@inventory_ns.route('/metrics/goods/<int:goods_id>')
class InventoryMetricsGoods(Resource):

    @permission_required(READ)
    @warehouse_required()
    def get(self, goods_id):
        """One goods, live: inbound layers making up the current stock (age), daily outbound and end-of-day stock of
        the last 90 days (sparklines), and the latest nightly summary

        Response `{goods, warehouse_id, total_stock, layers: [{in_at, quantity, age_days, movement_type}],
        age_unknown, daily: [{date, outbound, stock}], latest_metrics}`
        """
        from warehouse.goods.models import Goods
        from warehouse.inventory.models import Inventory
        from .flows import inbound_events, outbound_events
        from .history import daily_totals, daily_coverage
        from .metrics import age_layers, latest_metric_date
        from .models import InventoryMetricsDaily
        warehouse = _single_warehouse()
        goods = db.session.get(Goods, goods_id)
        if goods is None or goods.company_id != warehouse.company_id:
            raise NotFoundException(f"Goods with id {goods_id} not found", 13001)
        inventory = db.session.get(Inventory, (goods_id, warehouse.id))
        total = inventory.total_stock if inventory is not None else 0
        events = inbound_events(warehouse.id, clock.now(), [goods_id])
        movements = {(at, quantity): movement for _g, at, quantity, movement in events}
        used, unknown = age_layers(total, [(at, quantity) for _g, at, quantity, _m in events])
        today = clock.local_today(warehouse)
        date_from = today - timedelta(days=89)
        out_by_day = {}
        for _g, at, quantity, _dn in outbound_events(warehouse.id, clock.local_midnight(date_from, warehouse),
                                                     clock.now(), [goods_id]):
            day = clock.local_date(at, warehouse)
            out_by_day[day] = out_by_day.get(day, 0) + quantity
        stock = daily_totals(warehouse.id, date_from, today, [goods_id])
        daily = []
        day = date_from
        while day <= today:
            daily.append({'date': day.isoformat(), 'outbound': out_by_day.get(day, 0),
                          'stock': stock.get(day, {}).get(goods_id, 0)})
            day += timedelta(days=1)
        latest_day = latest_metric_date(warehouse.id)
        latest = None
        if latest_day is not None:
            row = db.session.get(InventoryMetricsDaily, (latest_day, warehouse.id, goods_id))
            latest = {'metric_date': latest_day.isoformat(), **_metric_row(row)} if row is not None else None
        return {
            'goods': _goods_ref(goods), 'warehouse_id': warehouse.id, 'total_stock': total,
            'layers': [{'in_at': clock.utc_iso(at), 'quantity': quantity,
                        'age_days': (today - clock.local_date(at, warehouse)).days,
                        'movement_type': movements.get((at, quantity))} for at, quantity in used],
            'age_unknown': unknown, 'daily': daily, 'latest_metrics': latest,
        }, 200


# ---------------------------------------------------------------------------
# 补货阈值
# ---------------------------------------------------------------------------
def _body():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise BadRequestException("request body must be a JSON object", 15017)
    return data


def _body_warehouse(data):
    if data.get('warehouse_id') is not None and getattr(g, 'warehouse_id', None) is None:
        from warehouse.warehouse.models import Warehouse
        warehouse_id = data['warehouse_id']
        if isinstance(warehouse_id, bool) or not isinstance(warehouse_id, int):
            raise BadRequestException("warehouse_id must be an integer", 15017, field='warehouse_id')
        require_warehouse_scope(warehouse_id, 'Warehouse')
        warehouse = db.session.get(Warehouse, warehouse_id)
        if warehouse is None:
            raise NotFoundException(f"Warehouse with id {warehouse_id} not found", 13001)
        return warehouse
    return _single_warehouse()


@inventory_ns.doc(security="jsonWebToken")
@inventory_ns.route('/thresholds')
class InventoryThresholds(Resource):

    @permission_required(EDIT)
    @warehouse_required()
    def put(self):
        """Set replenishment thresholds of many goods at once (all or nothing)

        Body `{warehouse_id?, items: [{goods_id | goods_code, low_stock_threshold?, high_stock_threshold?}],
        dry_run?}` (only the given fields change; -1 disables; at most 1,000 items). A goods without an inventory
        row in the warehouse gets an all-zero row.
        Response `{updated, created, unchanged, dry_run, items: [{goods_id, goods_code, goods_name, before {low,
        high}, after {low, high}, changed}]}`. Any invalid item → 400 15017 `details {errors: [{index, goods_id,
        goods_code, field, code (15013 low < -1 / 15014 high < -1 / 15015 high ≤ low / 16030 goods not found /
        15017), message}]}` and nothing changes.
        """
        from .thresholds import apply_thresholds
        data = _body()
        warehouse = _body_warehouse(data)
        return apply_thresholds(warehouse, data.get('items'), dry_run=bool(data.get('dry_run'))), 200


@inventory_ns.doc(security="jsonWebToken")
@inventory_ns.route('/thresholds/by-filter')
class InventoryThresholdsByFilter(Resource):

    @permission_required(EDIT)
    @warehouse_required()
    def put(self):
        """Set thresholds of every goods in a range: one fixed value, or the suggested values

        Body `{warehouse_id?, filter: {keyword?, goods_ids? (ticked rows), category?, brand?, below_low_only?},
        low_stock_threshold?, high_stock_threshold?, suggest?: {window_days (30), cover_days (14),
        overstock_days (90), min_quantity (1), apply: low | high | both}, dry_run?}`.
        Suggested low = max(min_quantity, ceil(average daily outbound of window_days × cover_days)), high =
        ceil(average × overstock_days) (> low); goods without outbound in 90 days get no suggestion (listed in
        `skipped` with reason NO_OUTBOUND_90D). Response as PUT /inventory/thresholds plus `skipped`; errors 400 15017.
        """
        from .thresholds import apply_by_filter
        data = _body()
        warehouse = _body_warehouse(data)
        return apply_by_filter(warehouse, data.get('filter') or {}, data.get('low_stock_threshold'),
                               data.get('high_stock_threshold'), data.get('suggest'),
                               dry_run=bool(data.get('dry_run'))), 200


@inventory_ns.doc(security="jsonWebToken")
@inventory_ns.route('/thresholds/suggest')
class InventoryThresholdsSuggest(Resource):

    @permission_required(READ)
    @warehouse_required()
    def get(self):
        """Suggested thresholds from the outbound quantity (preview; live)

        Query: warehouse, `keyword`, `goods_ids`, `category`, `brand`, `window_days` (30), `cover_days` (14),
        `overstock_days` (90), `min_quantity` (1), `page` / `per_page`.
        Items `{goods {id, code, name}, avg_daily_out, out_qty_window, out_qty_90d, suggested_low, suggested_high,
        current_low, current_high, available_stock_for_sale}`; suggested_* null when nothing went out in 90 days.
        """
        from warehouse.inventory.models import Inventory
        from .metrics import suggest_thresholds
        from .thresholds import goods_in_filter, suggest_params
        warehouse = _single_warehouse()
        params = suggest_params({key: _int(key) for key in ('window_days', 'cover_days', 'overstock_days',
                                                            'min_quantity')})
        goods = goods_in_filter(warehouse, {'keyword': request.args.get('keyword'),
                                            'goods_ids': _int_list('goods_ids'),
                                            'category': request.args.get('category'),
                                            'brand': request.args.get('brand')})
        page, per_page = _page()
        chunk = goods[(page - 1) * per_page: page * per_page]
        suggestions = suggest_thresholds(warehouse.id, [g.id for g in chunk], **params)
        inventories = {inv.goods_id: inv for inv in Inventory.query.filter(
            Inventory.warehouse_id == warehouse.id, Inventory.goods_id.in_([g.id for g in chunk])).all()} \
            if chunk else {}
        items = []
        for goods_row in chunk:
            inventory = inventories.get(goods_row.id)
            items.append({'goods': _goods_ref(goods_row), **suggestions.get(goods_row.id, {}),
                          'current_low': inventory.low_stock_threshold if inventory else -1,
                          'current_high': inventory.high_stock_threshold if inventory else -1,
                          'available_stock_for_sale': inventory.available_stock_for_sale if inventory else 0})
        return {'warehouse_id': warehouse.id, 'params': params, 'total': len(goods), 'page': page,
                'per_page': per_page, 'items': items}, 200


@api_ns.route('/overview')
class StockLedgerOverview(Resource):
    @permission_required(READ)
    @warehouse_required()
    @api_ns.expect(ledger_parser)
    def get(self):
        """Warehouse ledger overview: include shelf/pending/picked/packed, exclude transit.

        Movement/document filters apply to detail lines only. Summary and daily series
        share warehouse/goods/lot/location/type scope and booking-time boundaries.
        Totals and series include all matching lots, independent of row pagination.
        """
        from .overview import ledger_overview
        warehouse = _single_warehouse()
        args = ledger_parser.parse_args()
        filters = _ledger_filters(args)
        start, end = _summary_range(args, warehouse.id)
        end = min(end, clock.now() + timedelta(microseconds=1))
        if start >= end:
            raise _bad('Overview start must not be in the future', 'start_time')
        if (clock.local_date(end - timedelta(microseconds=1), warehouse) - clock.local_date(start, warehouse)).days >= 400:
            raise _bad('At most 400 days', 'start_time')
        page, per_page = _page()
        return ledger_overview(warehouse, filters, start, end, page, per_page)


@api_ns.route('/summary/export')
class StockSummaryExport(Resource):
    @permission_required(READ)
    @warehouse_required()
    @api_ns.expect(summary_parser)
    def get(self):
        """CSV for the complete selected summary dimension, independent of pagination."""
        result = _stock_summary_result(summary_parser.parse_args(), export=True)
        incoming = sorted(result['totals']['inbound_by_movement'])
        outgoing = sorted(result['totals']['outbound_by_movement'])
        header = ['row_type', 'warehouse_id', 'group_dimension', 'group_value', 'goods_code', 'goods_name',
                  'stock_type', 'start_time_utc', 'end_time_utc', 'opening']
        header += ['inbound_' + key for key in incoming] + ['inbound']
        header += ['outbound_' + key for key in outgoing] + ['outbound', 'closing']
        # Append breakdown columns so existing CSV columns retain their positions.
        types = list(dict.fromkeys(result['stock_types']))
        header += ['opening_' + key for key in types] + ['closing_' + key for key in types]
        def text_cell(value):
            text = str(value or '')
            return "'" + text if text.lstrip().startswith(('=', '+', '-', '@')) or text.startswith(('\t','\r','\n')) else text
        def values(item):
            return [item['opening']] + [item['inbound_by_movement'].get(key, 0) for key in incoming] + [item['inbound']] + [item['outbound_by_movement'].get(key, 0) for key in outgoing] + [item['outbound'], item['closing']] + [item['opening_by_stock_type'].get(key, 0) for key in types] + [item['closing_by_stock_type'].get(key, 0) for key in types]
        dates = [clock.utc_iso(result['start_time']), clock.utc_iso(result['end_time'])]
        rows = []
        for item in result['items']:
            goods = item.get('goods') or {}
            rows.append(['item', item['warehouse_id'], result['group_by'], text_cell(item.get('group_value')),
                         text_cell(goods.get('code')), text_cell(goods.get('name')), item.get('stock_type', '')] + dates + values(item))
        rows.append(['total', '', result['group_by'], '', '', '', ''] + dates + values(result['totals']))
        return _csv_response(header, rows, 'stock_summary.csv')
