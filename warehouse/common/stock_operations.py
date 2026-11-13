from flask import g
from extensions.db import db
from extensions.error import BadRequestException, ConflictException, ForbiddenException
from warehouse.common import require_warehouse_scope


def page(query, model, serialize):
    from flask import request
    try:
        number = int(request.args.get('page', 1))
        size = int(request.args.get('per_page', 25))
    except (ValueError, TypeError):
        raise BadRequestException('Invalid pagination')
    if number < 1 or not 1 <= size <= 100:
        raise BadRequestException('Invalid pagination')
    if request.args.get('status'):
        query = query.filter(model.status == request.args['status'])
    keyword = request.args.get('keyword', '').strip()
    if keyword:
        conditions = [model.reason.ilike('%' + keyword + '%')]
        if keyword.isdigit():
            conditions.append(model.id == int(keyword))
        query = query.filter(db.or_(*conditions))
    result = query.order_by(model.id.desc()).paginate(page=number, per_page=size, error_out=False)
    return dict(items=[serialize(row) for row in result.items], total=result.total,
                page=number, per_page=size, pages=result.pages)


def reason(body):
    text = body.get('reason') if isinstance(body, dict) else None
    if not isinstance(text, str) or not 1 <= len(text.strip()) <= 255:
        raise BadRequestException('A reason of 1-255 characters is required', field='reason')
    return text.strip()


def warehouse_access(warehouse_id):
    """Cross-warehouse operations check assignment independently of the selected warehouse tab."""
    accessible = getattr(g, 'accessible_warehouses', None)
    if accessible is not None:
        if warehouse_id not in {w.id for w in accessible}:
            raise ForbiddenException('Warehouse is outside your assigned scope', 12001)
    # The warehouse decorator leaves this unset only for unrestricted platform
    # callers. Their selected tab is a filter, not an assignment restriction.


def event(kind, document_id, action, body, actor):
    from warehouse.transfer.order_models import StockOperationEvent
    if not isinstance(body, dict):
        raise BadRequestException('Expected an object')
    request_id = body.get('request_id')
    if not isinstance(request_id, str) or not 8 <= len(request_id) <= 64:
        raise BadRequestException('A stable request_id of 8-64 characters is required', field='request_id')
    previous = StockOperationEvent.query.filter_by(kind=kind, document_id=document_id, request_id=request_id).first()
    if previous:
        if previous.action != action or previous.payload != body:
            raise ConflictException('Request ID was already used for a different operation', 16220)
        return previous, True
    row = StockOperationEvent(kind=kind, document_id=document_id, request_id=request_id, action=action, payload=body, operator_id=actor)
    db.session.add(row)
    return row, False


def history(kind, document_id):
    from warehouse.transfer.order_models import StockOperationEvent
    from system.user.models import User
    rows = StockOperationEvent.query.filter_by(kind=kind, document_id=document_id).order_by(StockOperationEvent.id).all()
    return [dict(id=r.id, action=r.action, payload=r.payload, operator_id=r.operator_id, operator_name=(db.session.get(User,r.operator_id).user_name if r.operator_id and db.session.get(User,r.operator_id) else None), created_at=r.created_at.isoformat()) for r in rows]
