"""Company-owned product label defaults. Printing overrides never mutate these settings."""
import math
from copy import deepcopy

from flask import request
from flask_restx import Resource
from extensions.db import db, UTCDateTime
from extensions.error import BadRequestException, ConflictException
from system.common import permission_required
from warehouse.common import clock, require_actor_user_id
from .models import Company
from .schemas import api_ns
from .services import CompanyService
from .views import _enforce_company_scope

DEFAULT_TEMPLATE = dict(width_mm=60, height_mm=40, margin_mm=2, name_font_pt=8,
                        show_name=True, barcode_format='auto')


class CompanyProductLabel(db.Model):
    __tablename__ = 'company_product_labels'
    company_id = db.Column(db.Integer, db.ForeignKey('companies.id', ondelete='CASCADE'), primary_key=True)
    revision = db.Column(db.Integer, nullable=False, default=0)
    template = db.Column(db.JSON, nullable=False)
    updated_by = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='SET NULL'))
    updated_at = db.Column(UTCDateTime, nullable=False, default=clock.now)


def validate_template(value):
    if not isinstance(value, dict) or set(value) != set(DEFAULT_TEMPLATE):
        raise BadRequestException('Invalid product label template', field='template')
    for key, low, high in [('width_mm', 40, 150), ('height_mm', 25, 150),
                          ('margin_mm', 1, 8), ('name_font_pt', 6, 14)]:
        number = value[key]
        if isinstance(number, bool) or not isinstance(number, (int, float)) or not math.isfinite(number) or not low <= number <= high:
            raise BadRequestException('Invalid label dimension', field=key)
    if not isinstance(value['show_name'], bool) or value['barcode_format'] not in ('auto', 'qr'):
        raise BadRequestException('Invalid label content', field='template')
    # Reserve two name lines, a gap and at least 12 mm for the barcode and human-readable code.
    name_height = value['name_font_pt'] * 25.4 / 72 * 2.5 + 1 if value['show_name'] else 0
    if value['height_mm'] - 2 * value['margin_mm'] - name_height < 12:
        raise BadRequestException('Label is too short for the selected text size and margins', field='height_mm')
    return deepcopy(value)


def payload(company_id, row):
    return dict(company_id=company_id, revision=row.revision if row else 0,
                template=row.template if row else deepcopy(DEFAULT_TEMPLATE),
                updated_at=clock.utc_iso(row.updated_at) if row else None)


@api_ns.route('/<int:company_id>/product-label')
class ProductLabelTemplate(Resource):
    @permission_required(['all_access', 'company_all_access', 'company_read', 'goods_read'])
    def get(self, company_id):
        _enforce_company_scope(company_id, 'view label settings for')
        CompanyService.get_company(company_id)
        return payload(company_id, db.session.get(CompanyProductLabel, company_id))

    @permission_required(['all_access', 'company_all_access', 'company_edit'])
    def put(self, company_id):
        _enforce_company_scope(company_id, 'edit label settings for')
        CompanyService.get_company(company_id)
        actor = require_actor_user_id()
        body = request.get_json(silent=True)
        if not isinstance(body, dict) or set(body) != {'revision', 'template'} or type(body.get('revision')) is not int or body['revision'] < 0:
            raise BadRequestException('Invalid template revision', field='revision')
        template = validate_template(body['template'])
        db.session.query(Company.id).filter_by(id=company_id).with_for_update().one()
        row = CompanyProductLabel.query.filter_by(company_id=company_id).with_for_update().first()
        if body['revision'] != (row.revision if row else 0):
            raise ConflictException('Company label template changed; reload before saving', field='revision')
        if row is None:
            row = CompanyProductLabel(company_id=company_id, revision=0)
            db.session.add(row)
        row.template, row.updated_by, row.updated_at = template, actor, clock.now()
        row.revision += 1
        db.session.commit()
        return payload(company_id, row)
