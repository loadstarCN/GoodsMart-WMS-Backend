"""Only records a user's request to open the browser print dialog."""
from uuid import UUID
from extensions import db
from extensions.error import BadRequestException, ConflictException
from extensions.transaction import transactional
from warehouse.common import get_warehouse_owned
from warehouse.common.clock import now
from warehouse.common.locks import lock_row
from .models import PickingTask, PickingPrintRequest


@transactional
def record_print_request(data, operator_id):
    if not isinstance(data, dict):
        raise BadRequestException('Expected a print request object')
    ids = data.get('task_ids')
    if (not isinstance(ids, list) or not 1 <= len(ids) <= 50
            or any(type(i) is not int or i <= 0 for i in ids) or len(set(ids)) != len(ids)):
        raise BadRequestException('Select 1 to 50 distinct picking tasks')
    try:
        request_id = str(UUID(data.get('request_id', '')))
    except (ValueError, TypeError, AttributeError):
        raise BadRequestException('request_id must be a UUID')
    language = data.get('language')
    if language not in ('zh', 'en', 'ja'):
        raise BadRequestException('Unsupported print language')
    # Resolve every ownership check before inserting anything. Lock only task
    # rows, in ID order; this operation never acquires stock or parent DN locks.
    tasks = [get_warehouse_owned(PickingTask, i, warehouse_attr='dn.warehouse_id', what='Picking Task') for i in sorted(ids)]
    for task in tasks:
        lock_row(task)
    timestamp = now()
    created = 0
    for task in tasks:
        existing = PickingPrintRequest.query.filter_by(task_id=task.id, request_id=request_id).first()
        if existing:
            if existing.operator_id != operator_id or existing.language != language:
                raise ConflictException('Print request ID already used with different parameters')
            continue
        db.session.add(PickingPrintRequest(task_id=task.id, request_id=request_id,
            operator_id=operator_id, language=language, requested_at=timestamp))
        created += 1
    db.session.flush()
    return {'request_id': request_id, 'task_ids': sorted(ids), 'created': created}
