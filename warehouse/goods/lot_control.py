"""Versioned lot holds, audit history and warehouse-scoped impact inspection."""
from flask import request, g
from flask_restx import Resource
from extensions.db import db
from extensions.error import BadRequestException, ConflictException, NotFoundException, ForbiddenException
from extensions.transaction import transactional
from system.common import permission_required
from warehouse.common import get_company_owned, warehouse_required, require_actor_user_id, add_warehouse_filter, require_warehouse_scope
from warehouse.common.locks import lock_row, lock_inventory_pairs
from warehouse.stock.models import GoodsLot, GoodsLotStatusChange, DNStockAllocation
from warehouse.goods.models import Goods, GoodsLocation
from warehouse.goods.schemas import api_ns


def product_lot(goods_id, lot_id):
    product = get_company_owned(Goods, goods_id)
    lot = GoodsLot.query.filter_by(id=lot_id, goods_id=goods_id).first()
    if lot is None:
        raise NotFoundException('Lot not found', 13001)
    return product, lot


@api_ns.route('/<int:goods_id>/lots/<int:lot_id>/control')
class LotControl(Resource):
    @permission_required(['all_access', 'company_all_access', 'inventory_read', 'inventory_edit'])
    @warehouse_required()
    def get(self, goods_id, lot_id):
        from warehouse.dn.models import DN, DNDetail
        from warehouse.location.models import Location
        from warehouse.stock.models import InventoryLotStock
        _, lot = product_lot(goods_id, lot_id)
        scope = add_warehouse_filter({})
        def scoped(query, column):
            if scope.get('warehouse_id'):
                return query.filter(column == scope['warehouse_id'])
            if scope.get('warehouse_ids'):
                return query.filter(column.in_(scope['warehouse_ids']))
            return query
        bins = scoped(db.session.query(GoodsLocation, Location).join(Location), Location.warehouse_id).filter(
            GoodsLocation.goods_id == goods_id, GoodsLocation.lot_id == lot_id, GoodsLocation.quantity > 0).all()
        transit = scoped(InventoryLotStock.query, InventoryLotStock.warehouse_id).filter_by(goods_id=goods_id, lot_id=lot_id).all()
        allocated = db.session.query(DNStockAllocation.dn_id).filter_by(goods_id=goods_id, lot_id=lot_id)
        planned = db.session.query(DNDetail.dn_id).filter_by(goods_id=goods_id)
        dns = scoped(DN.query, DN.warehouse_id).filter(DN.is_active.is_(True), DN.status.in_(('pending','in_progress','picked','packed')),
            db.or_(DN.id.in_(allocated), db.and_(DN.status.in_(('pending','in_progress')), DN.id.in_(planned)))).order_by(DN.id).all()
        history = GoodsLotStatusChange.query.filter_by(lot_id=lot_id).order_by(GoodsLotStatusChange.id.desc()).limit(100).all()
        return dict(id=lot.id, lot_no=lot.lot_no, status=lot.status, revision=lot.status_revision,
            bins=[dict(warehouse_id=loc.warehouse_id, location_id=loc.id, location_code=loc.code, quantity=row.quantity, stock_type=loc.location_type) for row,loc in bins],
            stages=[dict(warehouse_id=r.warehouse_id, pending=r.sorted_stock, picked=r.picked_stock, packed=r.packed_stock, in_transit=r.delivered_stock) for r in transit],
            orders=[dict(id=d.id, warehouse_id=d.warehouse_id, status=d.status, purpose=d.dn_type,
                possible=d.status in ('pending','in_progress'), blocked=d.dn_type not in ('return_to_supplier','damage_to_supplier')) for d in dns],
            history=[dict(status=r.status, previous_status=r.previous_status, reason=r.reason, operator_id=r.operator_id,
                revision=r.revision, created_at=r.created_at.isoformat()) for r in history])

    @permission_required(['all_access', 'company_all_access', 'inventory_edit'])
    @warehouse_required()
    @transactional
    def post(self, goods_id, lot_id):
        from warehouse.warehouse.models import Warehouse
        from warehouse.inventory.models import Inventory
        product, lot = product_lot(goods_id, lot_id)
        body = request.get_json(silent=True)
        if not isinstance(body, dict) or body.get('status') not in ('active', 'hold') or type(body.get('revision')) is not int:
            raise BadRequestException('Status and integer revision are required')
        reason = body.get('reason')
        if not isinstance(reason, str) or not 1 <= len(reason.strip()) <= 255:
            raise BadRequestException('A reason of 1-255 characters is required', field='reason')
        # Lot identity is company-wide. A warehouse-scoped operator must control every warehouse affected.
        accessible = getattr(g, 'accessible_warehouses', None)
        if accessible is not None:
            required = {w.id for w in Warehouse.query.filter_by(company_id=product.company_id, is_active=True).all()}
            if not required.issubset({w.id for w in accessible}):
                raise ForbiddenException('Company-wide lot control requires access to all company warehouses', 12001)
        pairs = db.session.query(Inventory.goods_id, Inventory.warehouse_id).filter_by(goods_id=goods_id).all()
        lock_inventory_pairs(pairs)
        lock_row(lot)
        if lot.status_revision != body['revision']:
            raise ConflictException('Lot status changed; reload before saving', 16210)
        if lot.status == body['status']:
            return dict(status=lot.status, revision=lot.status_revision)
        db.session.add(GoodsLotStatusChange(lot_id=lot.id, previous_status=lot.status, status=body['status'],
            revision=lot.status_revision+1, reason=reason.strip(), operator_id=require_actor_user_id()))
        lot.status, lot.status_revision = body['status'], lot.status_revision+1
        return dict(status=lot.status, revision=lot.status_revision)
