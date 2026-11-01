"""Platform-only overview, aggregated without loading tenant business details."""
from datetime import timedelta
from sqlalchemy import func
from extensions.db import db
from warehouse.common import clock
from warehouse.warehouse.models import Warehouse
from warehouse.asn.models import ASN
from warehouse.dn.models import DN
from system.user.models import User, Role
from system.logs.models import ActivityLog
from .models import Company


def tenant_list(args):
    from warehouse.staff.models import Staff
    from extensions.error import BadRequestException
    from sqlalchemy import or_
    now, zone = clock.now(), clock.default_timezone_name()
    today = clock.local_today(zone)
    month = today.replace(day=1)
    next_month = (month.replace(day=28) + timedelta(days=4)).replace(day=1)
    start, end = clock.local_midnight(month, zone), clock.local_midnight(next_month, zone)
    filters = {
        'all': True,
        'active': Company.is_active.is_(True),
        'inactive': Company.is_active.is_(False),
        'expiring': (Company.is_active.is_(True) & (Company.expired_at > now)
                     & (Company.expired_at <= now + timedelta(days=30))),
        'expired': Company.is_active.is_(True) & (Company.expired_at <= now),
    }
    status = args.get('status', 'all')
    if status not in filters:
        raise BadRequestException('Invalid tenant status.', 14017)
    try:
        page, per_page = int(args.get('page', 1)), int(args.get('per_page', 20))
        if page < 1 or not 1 <= per_page <= 100:
            raise ValueError()
    except (ValueError, TypeError):
        raise BadRequestException('Invalid pagination.', 14017)
    query = Company.query
    keyword = str(args.get('keyword', '')).strip()
    if keyword:
        pattern = '%' + keyword.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%'
        query = query.filter(or_(Company.name.ilike(pattern, escape='\\'), Company.email.ilike(pattern, escape='\\')))
    counts = {key: query.filter(condition).count() for key, condition in filters.items()}
    rows = query.filter(filters[status]).order_by(Company.id.desc()).offset((page-1)*per_page).limit(per_page).all()
    ids = [row.id for row in rows]
    def counts_by_company(model):
        return dict(db.session.query(model.company_id, func.count(model.id))
                    .filter(model.company_id.in_(ids)).group_by(model.company_id).all())
    warehouses, users = counts_by_company(Warehouse), counts_by_company(Staff)
    documents = {}
    for model in (ASN, DN):
        for cid, count in db.session.query(Warehouse.company_id, func.count(model.id)).join(
                Warehouse, Warehouse.id == model.warehouse_id).filter(
                Warehouse.company_id.in_(ids), model.created_at >= start, model.created_at < end
                ).group_by(Warehouse.company_id):
            documents[cid] = documents.get(cid, 0) + count
    items = [dict(id=row.id, name=row.name, email=row.email, is_active=row.is_active,
                  expired_at=row.expired_at.isoformat() if row.expired_at else None,
                  expired=bool(row.expired_at and row.expired_at <= now),
                  created_at=row.created_at.isoformat(), warehouse_count=warehouses.get(row.id, 0),
                  user_count=users.get(row.id, 0), document_count=documents.get(row.id, 0)) for row in rows]
    return dict(items=items, total=counts[status], page=page, per_page=per_page, counts=counts,
                timezone=zone, month=month.strftime('%Y-%m'))


def tenant_summary(company_id):
    from warehouse.staff.models import Staff
    from system.third_party.models import APIKey
    from .services import CompanyService
    from sqlalchemy.orm import selectinload
    company = CompanyService.get_company(company_id)
    warehouses = Warehouse.query.filter_by(company_id=company_id).order_by(Warehouse.name, Warehouse.id).all()
    members = Staff.query.options(selectinload(Staff.roles)).filter_by(company_id=company_id).all()
    counts, admins = {}, []
    for member in members:
        for role in member.roles:
            if role.id not in counts:
                counts[role.id] = dict(id=role.id, name=role.name, display_name=role.display_name,
                                       is_active=role.is_active, count=0)
            counts[role.id]['count'] += 1
        if any(role.name == 'company_admin' and role.is_active for role in member.roles):
            admins.append(dict(id=member.id, name=member.user_name, email=member.email, is_active=member.is_active))
    zone = clock.default_timezone_name()
    month = clock.local_today(zone).replace(day=1)
    next_month = (month.replace(day=28) + timedelta(days=4)).replace(day=1)
    start, end = clock.local_midnight(month, zone), clock.local_midnight(next_month, zone)
    documents = {}
    for key, model in (('asn', ASN), ('dn', DN)):
        documents[key] = db.session.query(func.count(model.id)).join(
            Warehouse, Warehouse.id == model.warehouse_id).filter(
            Warehouse.company_id == company_id, model.created_at >= start, model.created_at < end).scalar()
    creator = db.session.get(User, company.created_by)
    keys = APIKey.query.filter_by(company_id=company_id)
    return dict(timezone=zone, month=month.strftime('%Y-%m'), documents=documents,
                api_keys=dict(total=keys.count(), active=keys.filter_by(is_active=True).count()),
                created_at=company.created_at.isoformat(), creator_name=creator.user_name if creator else None,
                staff_active=sum(bool(member.is_active) for member in members),
                warehouses=[dict(id=row.id, name=row.name, is_active=row.is_active,
                                 timezone=clock.warehouse_timezone_name(row)) for row in warehouses],
                staff_count=len(members), roles=list(counts.values()), admins=admins,
                related=CompanyService._related_counts(company_id))


def overview():
    now = clock.now()
    zone = clock.default_timezone_name()
    start = clock.local_midnight(clock.local_today(zone), zone)
    end = clock.local_midnight(clock.local_today(zone) + timedelta(days=1), zone)

    def active_counts(model):
        total = db.session.query(func.count(model.id)).scalar()
        active = db.session.query(func.count(model.id)).filter(model.is_active.is_(True)).scalar()
        return dict(total=total, active=active, inactive=total-active)

    def document_counts(model, timestamp):
        return dict(db.session.query(Warehouse.company_id, func.count(model.id))
                    .join(Warehouse, Warehouse.id == model.warehouse_id)
                    .filter(timestamp >= start, timestamp < end)
                    .group_by(Warehouse.company_id).all())

    outbound = document_counts(DN, DN.created_at)
    inbound = document_counts(ASN, ASN.created_at)
    shipped = document_counts(DN, DN.delivered_at)
    company_ids = set(outbound) | set(inbound) | set(shipped)
    names = dict(db.session.query(Company.id, Company.name).filter(Company.id.in_(company_ids)).all())
    volume = [dict(id=cid, name=names.get(cid), dn=outbound.get(cid, 0), asn=inbound.get(cid, 0),
                   shipped=shipped.get(cid, 0), total=outbound.get(cid, 0)+inbound.get(cid, 0))
              for cid in company_ids]
    volume.sort(key=lambda row: (-row['total'], row['id']))
    documents = dict(dn=sum(outbound.values()), asn=sum(inbound.values()), shipped=sum(shipped.values()))
    documents['total'] = documents['dn'] + documents['asn']
    users = dict(total=User.query.count(), platform=User.query.filter_by(type='user').count(),
                 staff=User.query.filter_by(type='staff').count())
    expiring_query = Company.query.filter(Company.is_active.is_(True), Company.expired_at.isnot(None),
                                           Company.expired_at <= now + timedelta(days=30))
    expiring_count = expiring_query.count()
    expiring = [dict(id=row.id, name=row.name, expired_at=row.expired_at.isoformat(),
                     expired=row.expired_at <= now,
                     remaining_days=max(0, (row.expired_at-now).days))
                for row in expiring_query.order_by(Company.expired_at, Company.id).limit(20)]
    errors_query = ActivityLog.query.filter(ActivityLog.created_at >= now-timedelta(hours=24),
                                            ActivityLog.created_at <= now, ActivityLog.status_code >= 400)
    status_counts = dict(errors_query.with_entities(ActivityLog.status_code, func.count(ActivityLog.id))
                         .group_by(ActivityLog.status_code).all())
    groups = errors_query.with_entities(ActivityLog.actor, ActivityLog.endpoint, ActivityLog.method,
        ActivityLog.ip_address, ActivityLog.status_code, func.count(ActivityLog.id), func.max(ActivityLog.created_at))
    groups = groups.group_by(ActivityLog.actor, ActivityLog.endpoint, ActivityLog.method,
                             ActivityLog.ip_address, ActivityLog.status_code)
    error_groups = groups.count()
    recent_errors = [dict(actor=actor, endpoint=endpoint, method=method, ip=ip, status=status,
                         count=count, last_at=last_at.isoformat())
                     for actor, endpoint, method, ip, status, count, last_at in
                     groups.order_by(func.max(ActivityLog.created_at).desc(), ActivityLog.endpoint,
                                     ActivityLog.method, ActivityLog.actor, ActivityLog.ip_address,
                                     ActivityLog.status_code).limit(20)]
    return dict(timezone=zone, day=clock.local_today(zone).isoformat(), generated_at=now.isoformat(),
        tenants=active_counts(Company), users=users, roles=active_counts(Role), documents=documents,
        volume=volume[:6], other_volume={key:sum(row[key] for row in volume[6:]) for key in ('dn','asn','shipped','total')},
        other_tenants=max(0, len(volume)-6), expiring=expiring, expiring_count=expiring_count,
        errors=dict(status_counts=status_counts, items=recent_errors, groups=error_groups,
                    total=sum(status_counts.values()), logged_methods=list(ActivityLog.LOGGED_METHODS)))
