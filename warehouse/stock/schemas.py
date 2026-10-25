from flask_restx import Namespace, fields
from extensions import authorizations
from system.common import pagination_parser, create_pagination_model
from .models import MOVEMENT_TYPES, STOCK_TYPES

# 库存流水（/warehouse/stock-ledger/）；按库位 / 历史 / 分析 / 阈值的接口挂在库存命名空间（/warehouse/inventory/…）
api_ns = Namespace('stock_ledger', description='Stock ledger (every physical stock movement, per bucket)',
                   authorizations=authorizations)


def _csv(value):
    return [part.strip() for part in str(value).split(',') if part.strip()]


goods_ref_model = api_ns.model('StockGoodsRef', {
    'id': fields.Integer(), 'code': fields.String(), 'name': fields.String(),
})
location_ref_model = api_ns.model('StockLocationRef', {
    'id': fields.Integer(), 'code': fields.String(), 'location_type': fields.String(),
})
operator_ref_model = api_ns.model('StockOperatorRef', {'id': fields.Integer(), 'user_name': fields.String()})

ledger_model = api_ns.model('StockLedgerLine', {
    'id': fields.Integer(description='Line ID (lines of a bucket chain by (posted_at, id))'),
    'posted_at': fields.DateTime(description='When the movement was booked (UTC); daily balances use it'),
    'occurred_at': fields.DateTime(description='When it happened (UTC), e.g. the carrier pickup time of a shipment; '
                                               'may be earlier than posted_at'),
    'warehouse_id': fields.Integer(),
    'goods_id': fields.Integer(),
    'goods': fields.Nested(goods_ref_model, skip_none=True),
    'lot_id': fields.Integer(description='Lot (always null until lots are enabled)'),
    'location_id': fields.Integer(description='Location (only for onhand / damaged / return)'),
    'location': fields.Nested(location_ref_model, skip_none=True, allow_null=True),
    'stock_type': fields.String(enum=list(STOCK_TYPES), description=(
        'onhand (standard locations) / damaged / return / pending (waiting to be put away, good) / pending_damaged / '
        'picked / packed / in_transit (shipped, not signed; not part of total_stock)')),
    'quantity': fields.Integer(description='Signed change of this bucket'),
    'balance_after': fields.Integer(description='Balance of the same bucket after this line'),
    'goods_total_after': fields.Integer(description="The goods' total_stock in the warehouse after the movement"),
    'counter_stock_type': fields.String(description='Stock type of the other side of the movement (null = outside '
                                                    'the warehouse: inbound / outbound / reconcile)'),
    'movement_type': fields.String(enum=list(MOVEMENT_TYPES)),
    'doc_type': fields.String(description='asn / putaway / removal / transfer / picking_task / packing_task / dn / '
                                          'adjustment (null for opening / reconcile)'),
    'doc_id': fields.Integer(),
    'doc_no': fields.String(description='ASN / DN order number'),
    'doc_line_id': fields.Integer(description='ASN detail / picking detail / DN detail / adjustment detail'),
    'reason': fields.String(description='Removal reason / adjustment reason'),
    'operator_id': fields.Integer(),
    'operator': fields.Nested(operator_ref_model, skip_none=True, allow_null=True),
    'api_key_id': fields.Integer(),
    'source': fields.String(description='web / pda / api / system'),
    'trace_id': fields.String(description='Shared by every line of the same transaction'),
    'remark': fields.String(),
})
ledger_pagination_model = create_pagination_model(api_ns, ledger_model)

ledger_parser = pagination_parser.copy()
for name, kind, text in (
    ('warehouse_id', int, 'Warehouse (or the X-WAREHOUSE-ID header)'),
    ('company_id', int, 'Only this company (platform users)'),
    ('goods_id', int, 'Goods ID'),
    ('goods_code', str, 'Goods code (exact)'),
    ('keyword', str, 'Goods code / name / manufacturer / category / tags / brand contains'),
    ('lot_id', int, 'Lot ID'),
    ('lot_no', str, 'Lot number (exact)'),
    ('location_id', int, 'Location ID'),
    ('location_code', str, 'Location code contains'),
    ('doc_id', int, 'Source document ID (with doc_type)'),
    ('doc_no', str, 'Source document number (ASN / DN order number, exact)'),
    ('doc_line_id', int, 'Source document line ID'),
    ('operator_id', int, 'Operator user ID'),
    ('trace_id', str, 'Lines of one transaction'),
    ('start_time', str, 'From (ISO 8601; a date = that local day 00:00 of the warehouse)'),
    ('end_time', str, 'To (inclusive; a date = the whole local day)'),
):
    ledger_parser.add_argument(name, type=kind, location='args', help=text)
ledger_parser.add_argument('stock_types', type=_csv, location='args', help='Comma separated stock types')
ledger_parser.add_argument('movement_types', type=_csv, location='args', help='Comma separated movement types')
ledger_parser.add_argument('doc_type', type=_csv, location='args', help='Comma separated document types')
ledger_parser.add_argument('source', type=_csv, location='args', help='Comma separated sources (web/pda/api/system)')
ledger_parser.add_argument('time_field', type=str, location='args', choices=('occurred', 'posted'), default='occurred',
                           help='Which time start_time / end_time and the order use (default occurred)')
ledger_parser.add_argument('order', type=str, location='args', choices=('desc', 'asc'), default='desc')

summary_parser = api_ns.parser()
for name, kind, text in (
    ('warehouse_id', int, 'Warehouse (or the X-WAREHOUSE-ID header)'),
    ('company_id', int, 'Only this company (platform users)'),
    ('goods_id', int, 'One goods'),
    ('goods_code', str, 'Goods code (exact)'),
    ('keyword', str, 'Goods keyword'),
    ('location_id', int, 'Only this location (every line in / out of it counts)'),
    ('start_time', str, 'Period start (ISO 8601 or date; default the first day of this month, warehouse local)'),
    ('end_time', str, 'Period end (inclusive; default now)'),
    ('page', int, 'Page (default 1)'),
    ('per_page', int, 'Items per page (default 50, max 500)'),
):
    summary_parser.add_argument(name, type=kind, location='args', help=text)
summary_parser.add_argument('goods_ids', type=_csv, location='args', help='Comma separated goods IDs')
summary_parser.add_argument('stock_types', type=_csv, location='args',
                            help='Stock types to count (default: in the warehouse = total_stock); only movements '
                                 'crossing the boundary of this set count as in / out')
summary_parser.add_argument('group_by', type=str, location='args', choices=('goods', 'goods_stock_type'),
                            default='goods')

summary_item_model = api_ns.model('StockLedgerSummaryItem', {
    'warehouse_id': fields.Integer(),
    'goods_id': fields.Integer(),
    'goods': fields.Nested(goods_ref_model, skip_none=True),
    'stock_type': fields.String(description='Only with group_by=goods_stock_type'),
    'opening': fields.Integer(description='Balance at the start of the period'),
    'inbound': fields.Integer(description='In during the period'),
    'outbound': fields.Integer(description='Out during the period (positive number)'),
    'closing': fields.Integer(description='Balance at the end of the period'),
    'inbound_by_movement': fields.Raw(description='{movement_type: quantity}'),
    'outbound_by_movement': fields.Raw(description='{movement_type: quantity}'),
})
summary_model = api_ns.model('StockLedgerSummary', {
    'start_time': fields.DateTime(), 'end_time': fields.DateTime(),
    'stock_types': fields.List(fields.String()),
    'group_by': fields.String(),
    'total': fields.Integer(), 'page': fields.Integer(), 'per_page': fields.Integer(),
    'items': fields.List(fields.Nested(summary_item_model, skip_none=True)),
})
