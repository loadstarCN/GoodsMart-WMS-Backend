"""Delivery-note print dialog requests; never claims physical printing succeeded."""
from uuid import UUID
from extensions import db
from extensions.error import BadRequestException, ConflictException
from extensions.transaction import transactional
from warehouse.common import get_warehouse_owned
from warehouse.common.clock import now
from warehouse.common.locks import lock_row
from .models import DN, DNPrintRequest, DNCarrierShipment
from warehouse.shipping.stage import shipping_of


def print_metadata(dn):
    shipping = shipping_of(dn.id) or {}
    shipment = DNCarrierShipment.query.filter_by(dn_id=dn.id).filter(
        DNCarrierShipment.status.in_(('active', 'cancelling'))).order_by(DNCarrierShipment.id.desc()).first()
    return {**shipping, 'service_name': shipment.service_name() if shipment else None}


def print_history(dn):
    return [row.to_dict() for row in DNPrintRequest.query.filter_by(dn_id=dn.id)
            .order_by(DNPrintRequest.id.desc()).all()]


@transactional
def record_print_request(dn_id, data, operator_id):
    if not isinstance(data, dict):
        raise BadRequestException('Expected a print request object')
    try:
        request_id = str(UUID(data.get('request_id', '')))
    except (ValueError, TypeError, AttributeError):
        raise BadRequestException('request_id must be a UUID')
    language = data.get('language')
    if language not in ('zh', 'en', 'ja'):
        raise BadRequestException('Unsupported print language')
    dn = get_warehouse_owned(DN, dn_id, what='DN')
    lock_row(dn)
    existing = DNPrintRequest.query.filter_by(dn_id=dn.id, request_id=request_id).first()
    if existing and (existing.operator_id != operator_id or existing.language != language):
        raise ConflictException('Print request ID already used with different parameters')
    if not existing:
        db.session.add(DNPrintRequest(dn_id=dn.id, request_id=request_id,
            operator_id=operator_id, language=language, requested_at=now()))
        db.session.flush()
    return {'request_id': request_id, 'created': 0 if existing else 1, 'print_requests': print_history(dn)}
