"""Receipts for individual bin operations. The receipt and stock ledger commit together."""
import json
from uuid import UUID

from flask import g
from flask_restx import Resource, fields
from sqlalchemy.exc import IntegrityError

from extensions.db import db
from extensions.error import BadRequestException, ConflictException, ForbiddenException
from extensions.transaction import transactional
from system.common import permission_required
from warehouse.common import (
    check_goods_access, require_actor_user_id, require_company_goods,
    require_positive_int, warehouse_required,
)


def _receipt_query(kind, warehouse_id, request_id):
    from warehouse.transfer.order_models import StockOperationEvent
    return StockOperationEvent.query.filter_by(kind=kind, document_id=warehouse_id, request_id=request_id)


def _receipt(row):
    return dict(status='applied', request_id=row.request_id, warehouse_id=row.document_id,
                record_id=row.result_id, payload=row.payload)


def _replay(row, actor, body):
    if row.operator_id != actor or json.dumps(row.payload, sort_keys=True) != json.dumps(body, sort_keys=True):
        raise ConflictException('Request ID already used for a different stock operation', 16220)
    return _receipt(row), 200


@transactional
def _apply(kind, request_id, body, actor, location_fields, create_record):
    from warehouse.location.models import Location
    from warehouse.transfer.order_models import StockOperationEvent

    previous = _receipt_query(kind, g.warehouse_id, request_id).first()
    if previous:
        return _replay(previous, actor, body)

    goods_id = require_positive_int(body.get('goods_id'), 'goods_id')
    require_company_goods(goods_id, g.warehouse.company_id)
    if not check_goods_access(goods_id):
        raise ForbiddenException('Goods outside warehouse scope', 12001)
    for field in location_fields:
        location = db.session.get(Location, require_positive_int(body.get(field), field))
        if not location or location.warehouse_id != g.warehouse_id:
            raise ForbiddenException('Location outside selected warehouse', 12001)

    row = StockOperationEvent(kind=kind, document_id=g.warehouse_id, request_id=request_id,
                              action='create', payload=body, operator_id=actor)
    db.session.add(row)
    db.session.flush()  # Claim the unique key before any stock movement.
    record = create_record(body, actor)
    row.result_id = record.id
    return _receipt(row), 201


def register_stock_requests(namespace, input_model, create_record, *location_fields):
    """Use a distinct route so a legacy server cannot silently ignore the request ID."""
    kind = 'bin_' + namespace.name
    permissions = ['all_access', 'company_all_access', namespace.name + '_edit']
    request_model = namespace.model(namespace.name.title() + 'Request', {
        **{key: field for key, field in input_model.items() if key != 'operator_id'},
        'request_id': fields.String(required=True, description='Stable UUID for this operation'),
    })

    @namespace.route('/requests', endpoint=kind + '_requests')
    class Requests(Resource):
        @permission_required(permissions)
        @warehouse_required(force_warehouse=True)
        @namespace.expect(request_model)
        def post(self):
            """Apply a bin operation or retrieve the receipt for an identical retry."""
            body = namespace.payload
            if not isinstance(body, dict) or not isinstance(body.get('request_id'), str):
                raise BadRequestException('Expected a request_id UUID', field='request_id')
            try:
                request_id = str(UUID(body['request_id']))
            except ValueError:
                raise BadRequestException('Invalid request_id UUID', field='request_id')
            actor = require_actor_user_id()
            try:
                return _apply(kind, request_id, body, actor, location_fields, create_record)
            except IntegrityError:
                # A concurrent claimant may have committed during our INSERT. _apply
                # has rolled back, so this read sees its receipt in a new transaction.
                previous = _receipt_query(kind, g.warehouse_id, request_id).first()
                if previous is None:
                    raise
                return _replay(previous, actor, body)

    @namespace.route('/requests/<uuid:request_id>', endpoint=kind + '_receipt')
    class Receipt(Resource):
        @permission_required(permissions)
        @warehouse_required(force_warehouse=True)
        def get(self, request_id):
            """Check the current operator's receipt in the selected warehouse."""
            row = _receipt_query(kind, g.warehouse_id, str(request_id)).filter_by(
                operator_id=require_actor_user_id()).first()
            if row:
                return _receipt(row)
            return dict(status='not_recorded', request_id=str(request_id), warehouse_id=g.warehouse_id)
