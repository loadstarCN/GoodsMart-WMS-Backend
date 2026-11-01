from flask import request
from flask_restx import Resource
from extensions.db import db
from extensions.error import BadRequestException, ConflictException
from system.common import permission_required
from warehouse.stock.models import CompanyLotRules
from warehouse.stock.lot_policy import DEFAULTS, validate_rules, effective_rules, assert_can_disable, revision_of
from .models import Company
from .schemas import api_ns
from .views import _enforce_company_scope
from .services import CompanyService


def payload(row):
    return dict(revision=row.revision if row else 0, defaults={**DEFAULTS, **(row.defaults if row else {})},
                categories=row.categories if row else {})


@api_ns.route('/<int:company_id>/lot-rules')
class LotRules(Resource):
    @permission_required(['all_access', 'company_all_access', 'company_read', 'goods_read'])
    def get(self, company_id):
        _enforce_company_scope(company_id, 'view lot rules for')
        CompanyService.get_company(company_id)
        return payload(db.session.get(CompanyLotRules, company_id))

    @permission_required(['all_access', 'company_all_access', 'company_edit'])
    def put(self, company_id):
        _enforce_company_scope(company_id, 'edit lot rules for')
        CompanyService.get_company(company_id)
        body = request.get_json(silent=True)
        revision = revision_of(body)
        defaults = validate_rules(body.get('defaults'))
        categories = body.get('categories', {})
        if not isinstance(categories, dict) or len(categories) > 200 or any(not isinstance(key, str) or not 1 <= len(key) <= 100 for key in categories):
            raise BadRequestException('Invalid category rules', field='categories')
        categories = {key: validate_rules(value) for key, value in categories.items()}
        db.session.query(Company.id).filter_by(id=company_id).with_for_update().one()
        row = db.session.get(CompanyLotRules, company_id)
        if revision != (row.revision if row else 0):
            raise ConflictException('Lot rules changed; reload before saving', field='revision')
        candidate = CompanyLotRules(company_id=company_id, defaults=defaults, categories=categories)
        from warehouse.goods.models import Goods
        for goods in Goods.query.filter_by(company_id=company_id).all():
            assert_can_disable(goods, effective_rules(goods, company_rules=candidate))
        if row is None:
            row = CompanyLotRules(company_id=company_id, revision=0)
            db.session.add(row)
        row.defaults, row.categories = defaults, categories
        row.revision += 1
        db.session.commit()
        return payload(row)
