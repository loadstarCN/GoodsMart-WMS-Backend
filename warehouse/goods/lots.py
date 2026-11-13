"""Product lot master records for label printing; creation never changes stock."""
from datetime import date, timedelta
from uuid import uuid4
import unicodedata
from flask import request
from flask_restx import Resource
from sqlalchemy.exc import IntegrityError
from extensions.db import db
from extensions.error import BadRequestException, ConflictException, NotFoundException
from system.common import permission_required
from warehouse.common import get_company_owned, require_actor_user_id, warehouse_required, add_warehouse_filter
from warehouse.stock.models import GoodsLot
from .models import Goods
from .schemas import api_ns
from .views import _require_goods_visible


def serialize(row):
    return dict(id=row.id, goods_id=row.goods_id, lot_no=row.lot_no,
                production_date=row.production_date.isoformat() if row.production_date else None,
                expiry_date=row.expiry_date.isoformat() if row.expiry_date else None,
                status=row.status, source=row.source, date_revision=row.date_revision, status_revision=row.status_revision)


def normalize(value):
    if not isinstance(value, str):
        raise BadRequestException('Lot number is required', field='lot_no')
    value = unicodedata.normalize('NFKC', value).strip().upper()
    if not 1 <= len(value) <= 40 or any(unicodedata.category(c).startswith('C') for c in value):
        raise BadRequestException('Invalid lot number', field='lot_no')
    return value


def parse_date(body, field):
    value = body.get(field)
    if value in (None, ''):
        return None
    try:
        if not isinstance(value, str) or len(value) != 10:
            raise ValueError()
        result = date.fromisoformat(value)
        if result.isoformat() != value:
            raise ValueError()
        return result
    except ValueError:
        raise BadRequestException('Expected YYYY-MM-DD date', field=field)


@permission_required(['all_access', 'company_all_access', 'inventory_read'])
def _require_stock_read():
    pass


@api_ns.route('/<int:goods_id>/lots')
class ProductLots(Resource):
    @permission_required(['all_access', 'company_all_access', 'goods_read', 'sorting_read', 'picking_read', 'inventory_read', 'inventory_edit'])
    @warehouse_required()
    def get(self, goods_id):
        get_company_owned(Goods, goods_id)
        _require_goods_visible(goods_id)
        try:
            page = int(request.args.get('page', 1))
            per_page = int(request.args.get('per_page', 50))
            if page < 1 or not 1 <= per_page <= 100:
                raise ValueError()
        except ValueError:
            raise BadRequestException('Invalid pagination', field='page')
        query = GoodsLot.query.filter_by(goods_id=goods_id)
        keyword = request.args.get('keyword', '').strip()
        if keyword:
            query = query.filter(GoodsLot.lot_no.contains(unicodedata.normalize('NFKC', keyword).upper(), autoescape=True))
        stock_order = []
        if request.args.get('in_stock') is not None:
            _require_stock_read()
            if request.args['in_stock'] not in ('true', 'false'):
                raise BadRequestException('in_stock must be true or false', field='in_stock')
            from sqlalchemy import case, or_
            from warehouse.goods.models import GoodsLocation
            from warehouse.location.models import Location
            from warehouse.stock.models import InventoryLotStock
            scope = add_warehouse_filter({})
            shelf = db.session.query(GoodsLocation.id).join(Location).filter(
                GoodsLocation.goods_id == goods_id, GoodsLocation.lot_id == GoodsLot.id, GoodsLocation.quantity > 0)
            pending = db.session.query(InventoryLotStock.id).filter(
                InventoryLotStock.goods_id == goods_id, InventoryLotStock.lot_id == GoodsLot.id,
                InventoryLotStock.sorted_stock + InventoryLotStock.picked_stock + InventoryLotStock.packed_stock > 0)
            if scope.get('warehouse_id'):
                shelf = shelf.filter(Location.warehouse_id == scope['warehouse_id'])
                pending = pending.filter(InventoryLotStock.warehouse_id == scope['warehouse_id'])
            elif scope.get('warehouse_ids'):
                shelf = shelf.filter(Location.warehouse_id.in_(scope['warehouse_ids']))
                pending = pending.filter(InventoryLotStock.warehouse_id.in_(scope['warehouse_ids']))
            has_stock = or_(shelf.exists(), pending.exists())
            if request.args['in_stock'] == 'true':
                query = query.filter(has_stock)
            stock_order = [case((has_stock, 0), else_=1)]
        total = query.count()
        rows = query.order_by(*stock_order, GoodsLot.created_at.desc(), GoodsLot.id.desc()).offset((page-1)*per_page).limit(per_page).all()
        return dict(items=[serialize(row) for row in rows], total=total, page=page, per_page=per_page)

    @permission_required(['all_access', 'company_all_access', 'goods_edit', 'sorting_edit'])
    @warehouse_required()
    def post(self, goods_id):
        goods = get_company_owned(Goods, goods_id)
        _require_goods_visible(goods_id)
        actor = require_actor_user_id()
        body = request.get_json(silent=True)
        if not isinstance(body, dict) or set(body) - {'lot_no', 'production_date', 'expiry_date'}:
            raise BadRequestException('Invalid lot data')
        from warehouse.stock.lot_policy import effective_rules
        rules = effective_rules(goods)['values']
        automatic = rules['source'] == 'auto' and not body.get('lot_no')
        lot_no = ('L' + uuid4().hex[:24].upper()) if automatic else normalize(body.get('lot_no'))
        production, expiry = parse_date(body, 'production_date'), parse_date(body, 'expiry_date')
        if expiry is None and production and rules['shelf_life_days'] is not None:
            expiry = production + timedelta(days=rules['shelf_life_days'])
        if production and expiry and expiry < production:
            raise BadRequestException('Expiry precedes production', field='expiry_date')
        if GoodsLot.query.filter_by(goods_id=goods_id, lot_no=lot_no).first():
            raise ConflictException('Lot number already exists', field='lot_no')
        row = GoodsLot(goods_id=goods_id, lot_no=lot_no, production_date=production, expiry_date=expiry,
                       status='active', source='auto' if automatic else 'manual', created_by=actor)
        db.session.add(row)
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            if GoodsLot.query.filter_by(goods_id=goods_id, lot_no=lot_no).first():
                raise ConflictException('Lot number already exists', field='lot_no')
            raise
        return serialize(row), 201


@api_ns.route('/<int:goods_id>/lots/<int:lot_id>')
class ProductLot(Resource):
    @permission_required(['all_access', 'company_all_access', 'goods_read', 'sorting_read', 'picking_read', 'inventory_read', 'inventory_edit'])
    @warehouse_required()
    def get(self, goods_id, lot_id):
        get_company_owned(Goods, goods_id)
        _require_goods_visible(goods_id)
        row = GoodsLot.query.filter_by(id=lot_id, goods_id=goods_id).first()
        if row is None:
            raise NotFoundException('Lot not found for this product', 13001)
        return serialize(row)

    @permission_required(['all_access', 'company_all_access', 'goods_edit'])
    @warehouse_required()
    def put(self, goods_id, lot_id):
        from warehouse.common.locks import lock_row
        from warehouse.stock.models import GoodsLotDateChange
        get_company_owned(Goods, goods_id)
        _require_goods_visible(goods_id)
        row = GoodsLot.query.filter_by(id=lot_id, goods_id=goods_id).first()
        if row is None:
            raise NotFoundException('Lot not found for this product', 13001)
        body = request.get_json(silent=True)
        if not isinstance(body, dict) or set(body) != {'date_revision', 'production_date', 'expiry_date', 'reason'}:
            raise BadRequestException('Invalid date correction')
        revision, reason = body['date_revision'], body['reason']
        if type(revision) is not int or revision < 0:
            raise BadRequestException('Invalid date revision', field='date_revision')
        if not isinstance(reason, str) or not 1 <= len(reason.strip()) <= 255:
            raise BadRequestException('A reason of 1-255 characters is required', field='reason')
        production, expiry = parse_date(body, 'production_date'), parse_date(body, 'expiry_date')
        if production and expiry and expiry < production:
            raise BadRequestException('Expiry precedes production', field='expiry_date')
        lock_row(row)
        if row.date_revision != revision:
            raise ConflictException('Lot dates changed; reload before saving', field='date_revision')
        if (row.production_date, row.expiry_date) == (production, expiry):
            return serialize(row)
        change = GoodsLotDateChange(lot_id=row.id, revision=revision+1,
            before_production_date=row.production_date, before_expiry_date=row.expiry_date,
            production_date=production, expiry_date=expiry, reason=reason.strip(),
            operator_id=require_actor_user_id())
        row.production_date, row.expiry_date, row.date_revision = production, expiry, revision+1
        db.session.add(change)
        db.session.commit()
        return serialize(row)


@api_ns.route('/<int:goods_id>/lots/<int:lot_id>/date-history')
class ProductLotDateHistory(Resource):
    @permission_required(['all_access', 'company_all_access', 'goods_read', 'inventory_read', 'inventory_edit'])
    @warehouse_required()
    def get(self, goods_id, lot_id):
        from warehouse.stock.models import GoodsLotDateChange
        from warehouse.common import clock
        get_company_owned(Goods, goods_id)
        _require_goods_visible(goods_id)
        if not GoodsLot.query.filter_by(id=lot_id, goods_id=goods_id).first():
            raise NotFoundException('Lot not found for this product', 13001)
        try:
            page = int(request.args.get('page', 1))
            if page < 1:
                raise ValueError()
        except ValueError:
            raise BadRequestException('Invalid page', field='page')
        query = GoodsLotDateChange.query.filter_by(lot_id=lot_id)
        iso = lambda value: value.isoformat() if value else None
        return dict(total=query.count(), page=page, per_page=20, items=[dict(id=row.id, revision=row.revision,
            before_production_date=iso(row.before_production_date), before_expiry_date=iso(row.before_expiry_date),
            production_date=iso(row.production_date), expiry_date=iso(row.expiry_date), reason=row.reason,
            operator_name=row.operator.user_name if row.operator else None, created_at=clock.utc_iso(row.created_at))
            for row in query.order_by(GoodsLotDateChange.revision.desc()).offset((page-1)*20).limit(20).all()])


@api_ns.route('/<int:goods_id>/lot-rules')
class ProductLotRules(Resource):
    @permission_required(['all_access', 'company_all_access', 'goods_read', 'sorting_read', 'picking_read', 'inventory_read', 'inventory_edit'])
    @warehouse_required()
    def get(self, goods_id):
        from warehouse.stock.models import GoodsLotRules
        from warehouse.stock.lot_policy import effective_rules
        goods = get_company_owned(Goods, goods_id)
        _require_goods_visible(goods_id)
        row = db.session.get(GoodsLotRules, goods_id)
        return dict(revision=row.revision if row else 0, overrides=row.overrides if row else {},
                    has_lots=GoodsLot.query.filter_by(goods_id=goods_id).first() is not None, **effective_rules(goods))

    @permission_required(['all_access', 'company_all_access', 'goods_edit'])
    @warehouse_required()
    def put(self, goods_id):
        from warehouse.stock.models import GoodsLotRules
        from warehouse.stock.lot_policy import effective_rules, validate_rules, assert_can_disable, revision_of
        goods = get_company_owned(Goods, goods_id)
        _require_goods_visible(goods_id)
        body = request.get_json(silent=True)
        revision = revision_of(body)
        overrides = validate_rules(body.get('overrides'))
        db.session.query(Goods.id).filter_by(id=goods_id).with_for_update().one()
        row = db.session.get(GoodsLotRules, goods_id)
        if revision != (row.revision if row else 0):
            raise ConflictException('Lot rules changed; reload before saving', field='revision')
        assert_can_disable(goods, effective_rules(goods, overrides=overrides))
        if row is None:
            row = GoodsLotRules(goods_id=goods_id, revision=0)
            db.session.add(row)
        row.overrides = overrides
        row.revision += 1
        db.session.commit()
        return dict(revision=row.revision, overrides=row.overrides,
                    has_lots=GoodsLot.query.filter_by(goods_id=goods_id).first() is not None, **effective_rules(goods))


@api_ns.route('/<int:goods_id>/putaway-locations')
class PutawayLocations(Resource):
    @permission_required(['all_access', 'company_all_access', 'putaway_edit'])
    @warehouse_required()
    def get(self, goods_id):
        """Active choices ordered by existing stock of the selected lot, before pagination."""
        from sqlalchemy import case, func
        from warehouse.location.models import Location
        from warehouse.goods.models import GoodsLocation
        from warehouse.stock.views import _single_warehouse
        warehouse = _single_warehouse()
        goods = get_company_owned(Goods, goods_id)
        _require_goods_visible(goods_id)
        if goods.company_id != warehouse.company_id:
            raise NotFoundException('Goods not found', 13001)
        try:
            lot_id = int(request.args['lot_id']) if request.args.get('lot_id') else None
            page, per_page = int(request.args.get('page', 1)), int(request.args.get('per_page', 50))
            if page < 1 or not 1 <= per_page <= 100 or (lot_id is not None and lot_id < 1):
                raise ValueError()
        except ValueError:
            raise BadRequestException('Invalid location query')
        if lot_id is not None and not GoodsLot.query.filter_by(id=lot_id, goods_id=goods_id).first():
            raise NotFoundException('Lot not found for this product', 13001)
        same = GoodsLocation.lot_id.is_(None) if lot_id is None else GoodsLocation.lot_id == lot_id
        stock = (db.session.query(GoodsLocation.location_id.label('location_id'),
                    func.sum(case((same, GoodsLocation.quantity), else_=0)).label('same_quantity'),
                    func.sum(GoodsLocation.quantity).label('quantity'))
                 .join(Location, Location.id == GoodsLocation.location_id)
                 .filter(GoodsLocation.goods_id == goods_id, GoodsLocation.quantity > 0,
                         Location.warehouse_id == warehouse.id)
                 .group_by(GoodsLocation.location_id).subquery())
        query = (db.session.query(Location, func.coalesce(stock.c.same_quantity, 0),
                                  func.coalesce(stock.c.quantity, 0))
                 .outerjoin(stock, stock.c.location_id == Location.id)
                 .filter(Location.warehouse_id == warehouse.id, Location.is_active.is_(True)))
        code = request.args.get('code', '').strip()
        if code:
            query = query.filter(Location.code.contains(code, autoescape=True))
        total = query.count()
        rows = query.order_by(func.coalesce(stock.c.same_quantity, 0).desc(), Location.code, Location.id) \
                    .offset((page - 1) * per_page).limit(per_page).all()
        return dict(items=[dict(id=loc.id, code=loc.code, warehouse_id=warehouse.id,
                    warehouse_name=warehouse.name, location_type=loc.location_type,
                    same_lot_quantity=int(same_qty), other_lot_quantity=int(quantity-same_qty))
                    for loc, same_qty, quantity in rows], total=total, page=page, per_page=per_page)

from . import lot_control  # register lot hold routes
