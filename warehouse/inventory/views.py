from flask import g, request, Response
from flask_restx import Resource
from system.common import paginate,permission_required
from warehouse.common import warehouse_required, add_warehouse_filter, require_warehouse_scope
from .schemas import (
    api_ns, inventory_model, inventory_pagination_parser, pagination_model,
    pending_putaway_summary_model, pending_putaway_summary_parser,
)
from .availability import availability, parse_codes
from .pending import attach_pending_since
from .services import InventoryService

# 发货单批量添加要查可售量：建发货单的人（dn_edit）不一定有库存列表权限
_AVAILABILITY_PERMISSIONS = ["all_access", "company_all_access", "inventory_read", "dn_edit"]


def _pending_sources():
    from warehouse.stock.views import _bad, _bool, _int
    from .pending_sources import pending_source_rows
    source = request.args.get('source') or None
    if source not in (None, 'asn', 'unknown', 'pack_short_return', 'dn_cancel_return'):
        raise _bad('Invalid pending source', 'source')
    return pending_source_rows(g.warehouse_id, keyword=request.args.get('keyword', ''), source=source,
        damaged=_bool('damaged'), has_lot=_bool('has_lot'), overdue_days=_int('overdue_days', low=1, high=365))


@api_ns.route('/pending-putaway/sources')
class PendingPutawaySources(Resource):
    @permission_required(["all_access", "company_all_access", "inventory_read"])
    @warehouse_required(force_warehouse=True)
    def get(self):
        from warehouse.stock.views import _page
        page, per_page = _page()
        rows = _pending_sources()
        return dict(items=rows[(page-1)*per_page:page*per_page], total=len(rows), page=page, per_page=per_page,
                    summary=dict(goods_count=len({row['goods_id'] for row in rows}),
                                 quantity=sum(row['sorted_stock'] for row in rows),
                                 damaged_quantity=sum(row['sorted_damage_stock'] for row in rows),
                                 unknown_quantity=sum(row['sorted_stock'] for row in rows if row['source_type']=='unknown')))


@api_ns.route('/pending-putaway/sources/export')
class PendingPutawaySourcesExport(Resource):
    @permission_required(["all_access", "company_all_access", "inventory_read"])
    @warehouse_required(force_warehouse=True)
    def get(self):
        from warehouse.goods.tabular import write_csv
        from warehouse.stock.queries import MAX_EXPORT_ROWS, EXPORT_TOO_LARGE
        from extensions.error import BadRequestException
        rows = _pending_sources()
        if len(rows) > MAX_EXPORT_ROWS:
            raise BadRequestException('Too many pending source rows to export', EXPORT_TOO_LARGE)
        def text(value):
            value = str(value or '')
            return "'"+value if value.lstrip().startswith(('=', '+', '-', '@')) or value.startswith(('\t', '\r', '\n')) else value
        body = write_csv(['warehouse_id','goods_id','goods_code','goods_name','lot_id','lot_no','expiry_date',
                          'receipt_source_id','asn_id','dn_id','source_type','pending_since_utc','good_quantity','damaged_quantity','total_quantity'],
            ([row['warehouse_id'],row['goods_id'],text(row['goods']['code']),text(row['goods']['name']),
              row['lot_id'] or '',text(row['lot_no']),row['expiry_date'] or '',row['receipt_source_id'],
              row['asn_id'] or '',row['dn_id'] or '',row['source_type'],row['pending_since'] or '',row['good_quantity'],
              row['sorted_damage_stock'],row['sorted_stock']] for row in rows),'utf-8')
        return Response(body, mimetype='text/csv', headers={'Content-Disposition':'attachment; filename="pending-sources.csv"'})


def _inventory_filters(args):
    return add_warehouse_filter({key: args.get(key) for key in (
        'goods_id', 'goods_codes', 'low_stock_threshold', 'high_stock_threshold',
        'keyword', 'pending_putaway', 'below_low_threshold', 'above_high_threshold',
        'pending_damaged', 'pending_overdue_days', 'stock_bucket', 'category', 'brand', 'category_missing', 'brand_missing')})


@api_ns.doc(security="jsonWebToken")
@api_ns.route('/pending-putaway/export')
class PendingPutawayExport(Resource):
    @permission_required(["all_access", "company_all_access", "inventory_read"])
    @warehouse_required()
    @api_ns.expect(inventory_pagination_parser)
    def get(self):
        """Export every matching pending inventory row; pagination does not limit exports."""
        from warehouse.goods.tabular import write_csv
        args = inventory_pagination_parser.parse_args()
        filters = _inventory_filters(args)
        filters['pending_putaway'] = True
        rows = InventoryService.list_inventories(filters).all()
        attach_pending_since(rows)

        def text(value):
            value = str(value or '')
            return "'" + value if value.lstrip().startswith(('=', '+', '-', '@')) or value.startswith(('\t', '\r', '\n')) else value

        body = write_csv(
            ['goods_id', 'goods_code', 'goods_name', 'warehouse_id', 'warehouse_name',
             'good_quantity', 'damaged_quantity', 'total_quantity', 'pending_since_utc'],
            ([row.goods_id, text(row.goods.code), text(row.goods.name), row.warehouse_id,
              text(row.warehouse.name), max(0, row.sorted_stock - row.sorted_damage_stock),
              row.sorted_damage_stock, row.sorted_stock,
              row.pending_since.isoformat() if row.pending_since else ''] for row in rows), 'utf-8')
        return Response(body, mimetype='text/csv', headers={
            'Content-Disposition': 'attachment; filename="pending-putaway.csv"'})


@api_ns.doc(security="jsonWebToken")
@api_ns.route('/')
class InventoryList(Resource):

    @permission_required(["all_access","company_all_access","inventory_read"])
    @warehouse_required()
    @api_ns.marshal_with(pagination_model)
    @api_ns.expect(inventory_pagination_parser)
    def get(self):
        """Get a paginated list of inventory"""
        args = inventory_pagination_parser.parse_args()
        page = args.get('page')
        per_page = args.get('per_page')
        get_all = args.get('all', False)  # Flag to get all data
        
        filters = _inventory_filters(args)

        query = InventoryService.list_inventories(filters)

        result = paginate(query, page, per_page, get_all)
        # 每行的 pending_since（最早一件待上架的进入时刻，先进先上估算）：一页几条查询
        attach_pending_since(result['items'])
        from .ui_metrics import attach_latest_metrics
        attach_latest_metrics(result['items'])
        return result, 200

    # @permission_required(["all_access","company_all_access","inventory_edit"])
    # @api_ns.expect(inventory_input_model)
    # @api_ns.marshal_with(inventory_model)
    # def post(self):
    #     """Create a new inventory record"""
    #     from .services import InventoryService
    #     data = api_ns.payload
    #     new_inventory = InventoryService.create_inventory(data)
    #     return new_inventory, 201


@api_ns.doc(security="jsonWebToken")
@api_ns.route('/pending-putaway/summary')
class InventoryPendingPutawaySummary(Resource):

    # 首页 / 侧栏的数字：做上架的角色（putaway_read / putaway_edit）也能看
    @permission_required(["all_access", "company_all_access", "inventory_read", "putaway_read", "putaway_edit"])
    @warehouse_required()
    @api_ns.expect(pending_putaway_summary_parser)
    @api_ns.marshal_with(pending_putaway_summary_model)
    def get(self):
        """Summary of the stock waiting to be put away (for the home page / sidebar badges)

        Scope is the same as GET /inventory/: the warehouse in `warehouse_id` / `X-WAREHOUSE-ID`, else (not given or
        an empty `warehouse_id=`, e.g. a company admin on "all warehouses") every warehouse the caller can access
        (staff / API keys: their own company) with `warehouse_id: null`; `company_id` narrows it further.
        `keyword` filters the goods like GET /inventory/ (code / name / manufacturer / category / tags / brand).
        Counts only rows with `sorted_stock > 0`:
        - `inventory_count`: rows (= `total` of `GET /inventory/?pending_putaway=true` with the same scope)
        - `goods_count`: distinct goods
        - `total_quantity`: items waiting (sum of `sorted_stock`)
        - `damaged_quantity`: of which damaged (sum of `sorted_damage_stock`; can only go to damaged locations)
        - `good_quantity`: `total_quantity - damaged_quantity`
        - `overdue_quantity` / `overdue_inventory_count`: items / rows waiting longer than `overdue_days`
          (query parameter, default 3, 0-365), first-in-first-out like `pending_since`
        - `oldest_pending_since`: earliest `pending_since` (UTC, +00:00)
        """
        args = pending_putaway_summary_parser.parse_args()
        filters = add_warehouse_filter({'company_id': args.get('company_id'), 'keyword': args.get('keyword')})
        return InventoryService.pending_putaway_summary(filters, args.get('overdue_days')), 200

@api_ns.doc(security="jsonWebToken")
@api_ns.route('/availability', '/availability/')
class InventoryAvailability(Resource):
    """发货单批量添加：按仓库批量查可售量（warehouse/inventory/availability.py）"""

    @permission_required(_AVAILABILITY_PERMISSIONS)
    @warehouse_required(force_warehouse=True)
    def get(self):
        """Available-for-sale quantity of many goods codes in one warehouse (adding goods to a DN in bulk)

        Warehouse: `warehouse_id` or the X-WAREHOUSE-ID header (required, else 400 14003). `goods_codes`: comma
        separated; trimmed, de-duplicated, at most 1,000; exact match on the goods of the warehouse's company
        (POST with a JSON body {goods_codes: [...]} for long lists).
        → {warehouse_id, items: [{goods_id, code, name, unit, is_active, thumbnail_url, available_stock_for_sale
        (= onhand_stock - locked_stock - dn_stock, standard locations only; 0 without an inventory row), onhand_stock,
        dn_stock, locked_stock}] (goods found, in the requested order), not_found: [codes not found]}.
        Permission: inventory_read or dn_edit.
        400 15019: goods_codes missing / more than 1,000 (field goods_codes).
        """
        return availability(g.warehouse, parse_codes(request.args.get('goods_codes'))), 200

    @permission_required(_AVAILABILITY_PERMISSIONS)
    @warehouse_required(force_warehouse=True)
    def post(self):
        """Same as GET, codes in the JSON body {goods_codes: [...]} (warehouse: warehouse_id query / X-WAREHOUSE-ID)"""
        body = request.get_json(silent=True)
        return availability(g.warehouse, parse_codes(body.get('goods_codes') if isinstance(body, dict) else None),
            purpose=body.get('dn_type', 'shipping'), pool=body.get('stock_source', 'standard')), 200


@api_ns.doc(security="jsonWebToken")
@api_ns.route('/goods/<int:goods_id>/warehouse/<int:warehouse_id>/pending-lots')
class PendingLotStock(Resource):
    @permission_required(['all_access', 'company_all_access', 'inventory_read', 'putaway_read', 'putaway_edit'])
    @warehouse_required()
    def get(self, goods_id, warehouse_id):
        from warehouse.common import get_company_owned
        from warehouse.goods.models import Goods
        from warehouse.stock.models import InventoryLotStock, GoodsLot
        from extensions.db import db
        require_warehouse_scope(warehouse_id, 'Inventory')
        get_company_owned(Goods, goods_id)
        rows = InventoryLotStock.query.filter_by(goods_id=goods_id, warehouse_id=warehouse_id).filter(
            InventoryLotStock.sorted_stock > 0).order_by(InventoryLotStock.lot_id).all()
        result = []
        for row in rows:
            lot = db.session.get(GoodsLot, row.lot_id) if row.lot_id else None
            from warehouse.putaway.provenance import sources
            receipt_rows = sources(goods_id, warehouse_id, row.lot_id)
            base = dict(lot_id=row.lot_id, lot_no=lot.lot_no if lot else None,
                        expiry_date=lot.expiry_date.isoformat() if lot and lot.expiry_date else None)
            for source in receipt_rows:
                result.append(dict(**base, receipt_source_id=source.id, asn_id=source.asn_id, dn_id=source.dn_id, source_type=source.source_type,
                    sorted_stock=source.good_remaining+source.damaged_remaining,
                    sorted_damage_stock=source.damaged_remaining))
            unknown = row.sorted_stock-sum(source.good_remaining+source.damaged_remaining for source in receipt_rows)
            unknown_bad = row.sorted_damage_stock-sum(source.damaged_remaining for source in receipt_rows)
            if unknown > 0:
                result.append(dict(**base, receipt_source_id=0, asn_id=None,
                    sorted_stock=unknown, sorted_damage_stock=unknown_bad))
        return dict(items=result)


@api_ns.route('/goods/<int:goods_id>/warehouse/<int:warehouse_id>')
class InventoryDetail(Resource):

    @permission_required(["all_access","company_all_access","inventory_read"])
    @warehouse_required()
    @api_ns.marshal_with(inventory_model)
    def get(self,goods_id,warehouse_id):
        """
        Get inventory details by Goods ID.
        - `goods_id`: Required, the ID of the goods.
        - `warehouse_id`: Required, the ID of the warehouse. This can be provided either as `warehouse_id` in the request or via the `X-Warehouse-ID` header.
        """
        # 路径里的 warehouse_id 必须在调用方可访问范围内（B-24）
        require_warehouse_scope(warehouse_id, 'Inventory')
        inventory = InventoryService.get_inventory(goods_id, warehouse_id)
        attach_pending_since([inventory])
        return inventory, 200

    # @permission_required(["all_access","company_all_access","inventory_edit"])
    # @api_ns.expect(inventory_input_model)
    # @api_ns.marshal_with(inventory_model)
    # def put(self, goods_id, warehouse_id):
    #     """Update inventory details"""
    #     from .services import InventoryService
    #     data = api_ns.payload
    #     inventory = InventoryService.update_inventory(goods_id, warehouse_id, data)
    #     return inventory

    # @permission_required(["all_access","company_all_access","inventory_delete"])
    # def delete(self, goods_id, warehouse_id):
    #     """Delete an inventory record by Goods ID"""
    #     from .services import InventoryService
    #     InventoryService.delete_inventory(goods_id, warehouse_id)
    #     return {"message": "Inventory record deleted successfully"}, 200
@api_ns.route('/summary')
class InventorySummary(Resource):
    @permission_required(['all_access', 'company_all_access', 'inventory_read'])
    @warehouse_required()
    @api_ns.expect(inventory_pagination_parser)
    def get(self):
        """Aggregate the same authorized, filtered rows as the inventory list (not just one page)."""
        from sqlalchemy import func
        from .models import Inventory
        args = inventory_pagination_parser.parse_args()
        filters = _inventory_filters(args)
        query = InventoryService.list_inventories(filters).order_by(None)
        keys = ('total_stock', 'onhand_stock', 'locked_stock', 'damage_stock', 'return_stock',
                'asn_stock', 'received_stock', 'sorted_stock', 'sorted_damage_stock',
                'dn_stock', 'picked_stock', 'packed_stock', 'delivered_stock')
        columns = [func.coalesce(func.sum(getattr(Inventory, key)), 0).label(key) for key in keys]
        row = query.with_entities(*columns, func.count().label('inventory_count'),
                                  func.count(func.distinct(Inventory.goods_id)).label('goods_count')).one()
        result = {key: int(getattr(row, key)) for key in (*keys, 'inventory_count', 'goods_count')}
        from warehouse.stock.lot_policy import blocked_stock_map
        blocked = sum(blocked_stock_map(query.with_entities(Inventory.goods_id, Inventory.warehouse_id).all()).values())
        result['unshippable_stock'] = blocked
        result['available_stock_for_sale'] = result['onhand_stock'] - result['locked_stock'] - result['dn_stock'] - blocked
        return result, 200
