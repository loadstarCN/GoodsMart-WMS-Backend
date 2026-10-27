"""Accuracy from immutable completed count snapshots, never today's live balance."""
from datetime import date, timedelta
from sqlalchemy import func, case, and_
from extensions.db import db
from warehouse.common import clock, require_warehouse_scope
from warehouse.common.document_reports import report_period
from .models import CycleCountTask as Task, CycleCountTaskDetail as Detail


def accuracy_report(warehouse):
    require_warehouse_scope(warehouse.id, 'Warehouse')
    start,end,page,per_page=report_period(warehouse)
    asof=min(end,clock.local_today(warehouse))
    months={}
    for offset in range(5,-1,-1):
        n=asof.year*12+asof.month-1-offset
        month=date(n//12,n%12+1,1).isoformat()[:7]
        months[month]=dict(month=month,lines=0,different=0,tasks=0,partial=month==asof.isoformat()[:7])
    first=min(start,date.fromisoformat(next(iter(months))+'-01'))
    valid=and_(Detail.status=='completed',Detail.system_quantity.isnot(None),Detail.difference.isnot(None))
    query=db.session.query(Task.id,Task.task_name,Task.completed_at,
        func.count(Detail.id).label('all_lines'),func.sum(case((valid,1),else_=0)).label('lines'),
        func.sum(case((and_(valid,Detail.difference!=0),1),else_=0)).label('different'),
        func.sum(case((valid,Detail.system_quantity),else_=0)).label('quantity'),
        func.sum(case((valid,func.abs(Detail.difference)),else_=0)).label('difference')).outerjoin(Detail,Detail.task_id==Task.id).filter(
        Task.warehouse_id==warehouse.id,Task.is_active.is_(True),Task.status=='completed',
        Task.completed_at>=clock.local_midnight(first,warehouse),Task.completed_at<clock.local_midnight(max(end,asof)+timedelta(days=1),warehouse)
    ).group_by(Task.id,Task.task_name,Task.completed_at).order_by(Task.completed_at.desc(),Task.id.desc())
    summary=dict(tasks=0,lines=0,different=0,quantity=0,difference=0,unverified=0)
    items=[]
    for row in query.yield_per(500):
        day=clock.local_date(row.completed_at,warehouse)
        if day<=asof and day.isoformat()[:7] in months:
            bucket=months[day.isoformat()[:7]]
            bucket['tasks']+=1;bucket['lines']+=row.lines or 0;bucket['different']+=row.different or 0
        if start<=day<=end:
            summary['tasks']+=1
            for key in ('lines','different','quantity','difference'):summary[key]+=getattr(row,key) or 0
            summary['unverified']+=(row.all_lines or 0)-(row.lines or 0)
            if (page-1)*per_page<=summary['tasks']-1<page*per_page:
                items.append(dict(id=row.id,name=row.task_name,date=day.isoformat(),lines=row.lines or 0,different=row.different or 0,
                    difference=row.difference or 0,unverified=(row.all_lines or 0)-(row.lines or 0)))
    summary['line_accuracy']=1-summary['different']/summary['lines'] if summary['lines'] else None
    summary['quantity_accuracy']=1-summary['difference']/summary['quantity'] if summary['quantity'] else None
    return dict(summary=summary,monthly=[dict(**b,accuracy=1-b['different']/b['lines'] if b['lines'] else None) for b in months.values()],
                items=items,total=summary['tasks'],page=page,per_page=per_page,timezone=clock.warehouse_timezone_name(warehouse))
