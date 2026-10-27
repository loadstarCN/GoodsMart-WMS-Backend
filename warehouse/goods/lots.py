"""Product lot master records for label printing; creation never changes stock."""
from datetime import date
import unicodedata
from flask import request
from flask_restx import Resource
from sqlalchemy.exc import IntegrityError
from extensions.db import db
from extensions.error import BadRequestException, ConflictException, NotFoundException
from system.common import permission_required
from warehouse.common import get_company_owned, require_actor_user_id, warehouse_required
from warehouse.stock.models import GoodsLot
from .models import Goods
from .schemas import api_ns
from .views import _require_goods_visible


def serialize(row):
    return dict(id=row.id, goods_id=row.goods_id, lot_no=row.lot_no,
                production_date=row.production_date.isoformat() if row.production_date else None,
                expiry_date=row.expiry_date.isoformat() if row.expiry_date else None,
                status=row.status, source=row.source)


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


@api_ns.route('/<int:goods_id>/lots')
class ProductLots(Resource):
    @permission_required(['all_access', 'company_all_access', 'goods_read'])
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
        total = query.count()
        rows = query.order_by(GoodsLot.created_at.desc(), GoodsLot.id.desc()).offset((page-1)*per_page).limit(per_page).all()
        return dict(items=[serialize(row) for row in rows], total=total, page=page, per_page=per_page)

    @permission_required(['all_access', 'company_all_access', 'goods_edit'])
    @warehouse_required()
    def post(self, goods_id):
        get_company_owned(Goods, goods_id)
        _require_goods_visible(goods_id)
        actor = require_actor_user_id()
        body = request.get_json(silent=True)
        if not isinstance(body, dict) or set(body) - {'lot_no', 'production_date', 'expiry_date'}:
            raise BadRequestException('Invalid lot data')
        lot_no = normalize(body.get('lot_no'))
        production, expiry = parse_date(body, 'production_date'), parse_date(body, 'expiry_date')
        if production and expiry and expiry < production:
            raise BadRequestException('Expiry precedes production', field='expiry_date')
        if GoodsLot.query.filter_by(goods_id=goods_id, lot_no=lot_no).first():
            raise ConflictException('Lot number already exists', field='lot_no')
        row = GoodsLot(goods_id=goods_id, lot_no=lot_no, production_date=production, expiry_date=expiry,
                       status='active', source='manual', created_by=actor)
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
    @permission_required(['all_access', 'company_all_access', 'goods_read'])
    @warehouse_required()
    def get(self, goods_id, lot_id):
        get_company_owned(Goods, goods_id)
        _require_goods_visible(goods_id)
        row = GoodsLot.query.filter_by(id=lot_id, goods_id=goods_id).first()
        if row is None:
            raise NotFoundException('Lot not found for this product', 13001)
        return serialize(row)
