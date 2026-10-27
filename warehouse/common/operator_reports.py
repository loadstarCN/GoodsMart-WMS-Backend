"""Completed-task quantities and elapsed task time, attributed to recorded operators."""
from collections import defaultdict
from datetime import timedelta
from statistics import median

from sqlalchemy import func
from extensions.db import db
from warehouse.common import clock, require_warehouse_scope
from warehouse.common.document_reports import report_period


def operator_report(kind, warehouse, export=False, all_operators=False):
    from warehouse.dn.models import DN
    from warehouse.picking.models import PickingTask, PickingTaskDetail
    from warehouse.packing.models import PackingTask, PackingTaskDetail
    from system.user.models import User
    require_warehouse_scope(warehouse.id, 'Warehouse')
    task, detail = (PickingTask, PickingTaskDetail) if kind == 'picking' else (PackingTask, PackingTaskDetail)
    task_key = detail.picking_task_id if kind == 'picking' else detail.packing_task_id
    quantity = detail.picked_quantity if kind == 'picking' else detail.packed_quantity
    start, end, page, per_page = report_period(warehouse)
    # One row per operator/task: multiple batches do not multiply elapsed time.
    query = (db.session.query(task.id, task.started_at, task.completed_at, detail.operator_id,
                              User.user_name, func.sum(quantity))
             .join(DN, DN.id == task.dn_id).join(detail, task_key == task.id)
             .join(User, User.id == detail.operator_id)
             .filter(DN.warehouse_id == warehouse.id, task.is_active.is_(True), task.status == 'completed',
                     task.completed_at >= clock.local_midnight(start, warehouse),
                     task.completed_at < clock.local_midnight(end + timedelta(days=1), warehouse))
             .group_by(task.id, task.started_at, task.completed_at, detail.operator_id, User.user_name))
    operators = {}
    daily = defaultdict(int)
    tasks = set()
    task_points = {}
    operator_days = defaultdict(lambda: defaultdict(int))
    for task_id, started, completed, operator_id, name, qty in query.yield_per(500):
        row = operators.setdefault(operator_id, dict(operator_id=operator_id, name=name, quantity=0,
                                                     tasks=0, timed_quantity=0, seconds=0, untimed_tasks=0))
        qty = int(qty or 0)
        row['quantity'] += qty
        row['tasks'] += 1
        tasks.add(task_id)
        day = clock.local_date(completed, warehouse).isoformat()
        daily[day] += qty
        operator_days[operator_id][day] += qty
        seconds = (completed-started).total_seconds() if started else 0
        point = task_points.setdefault(task_id, dict(id=task_id, quantity=0, seconds=seconds if seconds > 0 else None, date=day, operators=[]))
        point['quantity'] += qty
        point['operators'].append(dict(id=operator_id, name=name))
        if seconds > 0:
            row['seconds'] += seconds
            row['timed_quantity'] += qty
        else:
            row['untimed_tasks'] += 1
    rows = sorted(operators.values(), key=lambda row: (-row['quantity'], row['operator_id']))
    for row in rows:
        row['per_hour'] = row['timed_quantity']*3600/row['seconds'] if row['seconds'] else None
        row['daily'] = [dict(date=day, quantity=qty) for day, qty in sorted(operator_days[row['operator_id']].items())]
    rates = [row['per_hour'] for row in rows if row['per_hour'] is not None]
    points = sorted(task_points.values(), key=lambda point: (point['date'], point['id']), reverse=True)
    timed = [point for point in points if point['seconds'] is not None]
    task_rates = [point['quantity']*3600/point['seconds'] for point in timed]
    return dict(start_date=start.isoformat(), end_date=end.isoformat(), timezone=clock.warehouse_timezone_name(warehouse),
                kind=kind, task_chart=dict(items=timed[:500] if not export else [], total=len(timed),
                                          untimed=len(points)-len(timed),
                                          median_per_hour=median(task_rates) if task_rates else None),
                summary=dict(quantity=sum(row['quantity'] for row in rows), tasks=len(tasks),
                                       operators=len(rows), median_per_hour=median(rates) if rates else None,
                                       timed_quantity=sum(p['quantity'] for p in timed), seconds=sum(p['seconds'] for p in timed)),
                daily=[dict(date=day, quantity=qty) for day, qty in sorted(daily.items())],
                items=rows if export or all_operators else rows[(page-1)*per_page:page*per_page], total=len(rows), page=page, per_page=per_page)


def export_operator_report(kind, warehouse):
    import csv, io
    from flask import Response
    from extensions.error import BadRequestException
    data=operator_report(kind,warehouse,export=True)
    if len(data['items'])>50000:
        raise BadRequestException('Too many operators to export',14009)
    stream=io.StringIO(newline='')
    writer=csv.writer(stream)
    keys=['operator_id','name','quantity','tasks','timed_quantity','seconds','per_hour','untimed_tasks']
    writer.writerow(keys+['kind','start_date','end_date','timezone'])
    for item in data['items']:
        row=dict(item)
        name=row['name'] or ''
        if name.lstrip().startswith(('=','+','-','@')) or name.startswith(('\t','\r','\n')):
            row['name']="'"+name
        writer.writerow([row[key] for key in keys]+[kind,data['start_date'],data['end_date'],data['timezone']])
    return Response('\ufeff'+stream.getvalue(),mimetype='text/csv',headers={'Content-Disposition':f'attachment; filename="{kind}_operators.csv"'})


def operator_overview(warehouse):
    from system.common.permissions import get_actor_context
    _, permissions, _ = get_actor_context()
    kinds = [kind for kind in ('picking', 'packing') if permissions.intersection({'all_access', 'company_all_access', kind+'_read'})]
    start, end, page, per_page = report_period(warehouse)
    people, phases = {}, {}
    for kind in kinds:
        report = operator_report(kind, warehouse, all_operators=True)
        phases[kind] = dict(summary=report['summary'], task_chart=report['task_chart'])
        for item in report['items']:
            person = people.setdefault(item['operator_id'], dict(operator_id=item['operator_id'], name=item['name'], phases={}, quantity=0, days=defaultdict(int)))
            person['phases'][kind] = item
            person['quantity'] += item['quantity']
            for day in item['daily']:
                person['days'][day['date']] += day['quantity']
    rows = sorted(people.values(), key=lambda row: (-row['quantity'], row['operator_id']))
    for row in rows:
        row['daily'] = [dict(date=day, quantity=qty) for day, qty in sorted(row.pop('days').items())]
    return dict(start_date=start.isoformat(), end_date=end.isoformat(), timezone=clock.warehouse_timezone_name(warehouse),
                phases=phases, scales=dict(quantity=max([1]+[phase['quantity'] for row in rows for phase in row['phases'].values()]),
                                         rate=max([1]+[phase['per_hour'] or 0 for row in rows for phase in row['phases'].values()]+[p['task_chart']['median_per_hour'] or 0 for p in phases.values()])),
                items=rows[(page-1)*per_page:page*per_page], total=len(rows), page=page, per_page=per_page)


def operator_tasks(warehouse):
    from flask import request
    from sqlalchemy import literal, union_all, select
    from extensions.error import BadRequestException, ForbiddenException
    from system.common.permissions import get_actor_context
    from warehouse.dn.models import DN
    from warehouse.picking.models import PickingTask, PickingTaskDetail
    from warehouse.packing.models import PackingTask, PackingTaskDetail
    require_warehouse_scope(warehouse.id, 'Warehouse')
    start, end, page, per_page = report_period(warehouse)
    try:
        operator_id = int(request.args.get('operator_id', ''))
        if operator_id <= 0:
            raise ValueError()
    except (ValueError, TypeError):
        raise BadRequestException('Invalid operator ID', 14009)
    _, permissions, _ = get_actor_context()
    requested = request.args.get('kind')
    if requested is not None and requested not in ('picking','packing'):
        raise BadRequestException('Invalid operation',14009)
    kinds = [kind for kind in ('picking','packing') if permissions.intersection({'all_access','company_all_access',kind+'_read'})]
    if requested and requested not in kinds:
        raise ForbiddenException('Permission denied',12001)
    queries=[]
    for kind in ([requested] if requested else kinds):
        task, detail = (PickingTask, PickingTaskDetail) if kind=='picking' else (PackingTask, PackingTaskDetail)
        task_key=detail.picking_task_id if kind=='picking' else detail.packing_task_id
        quantity=detail.picked_quantity if kind=='picking' else detail.packed_quantity
        queries.append(db.session.query(literal(kind).label('kind'),task.id,task.dn_id,task.started_at,task.completed_at,
                                        func.sum(quantity).label('quantity')).join(DN,DN.id==task.dn_id).join(detail,task_key==task.id).filter(
            DN.warehouse_id==warehouse.id,task.is_active.is_(True),task.status=='completed',detail.operator_id==operator_id,
            task.completed_at>=clock.local_midnight(start,warehouse),
            task.completed_at<clock.local_midnight(end+timedelta(days=1),warehouse)).group_by(
            task.id,task.dn_id,task.started_at,task.completed_at).statement)
    combined=union_all(*queries).subquery()
    total=db.session.scalar(select(func.count()).select_from(combined))
    records=db.session.execute(select(combined).order_by(combined.c.completed_at.desc(),combined.c.kind,combined.c.id).offset((page-1)*per_page).limit(per_page)).mappings()
    items=[]
    for row in records:
        seconds=(row['completed_at']-row['started_at']).total_seconds() if row['started_at'] else 0
        items.append(dict(kind=row['kind'],id=row['id'],dn_id=row['dn_id'],quantity=int(row['quantity'] or 0),
                          seconds=seconds if seconds>0 else None,completed_at=clock.utc_iso(row['completed_at'])))
    return dict(items=items,total=total,page=page,per_page=per_page,timezone=clock.warehouse_timezone_name(warehouse))
