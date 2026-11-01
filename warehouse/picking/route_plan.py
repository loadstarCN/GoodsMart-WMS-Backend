"""Read-only route suggestions over an immutable published map version."""
from flask import request
from extensions.db import db
from extensions.error import BadRequestException
from system.common.permissions import get_actor_context
from warehouse.warehouse.layout import WarehouseLayout, WarehouseLayoutVersion
from .print_plan import print_plan
from .route_geometry import ordered_walk, pick_points, RouteGeometryError, WALK_METRES_PER_SECOND


def route_plan(task):
    from warehouse.warehouse.operations import settings
    options = settings(task.dn.warehouse)
    plan = print_plan(task)
    actor=get_actor_context()
    result = dict(task_id=task.id,dn_id=task.dn_id,status=task.status,warehouse_id=task.dn.warehouse_id,
        picking_sort=options['picking_sort'],
        warehouse_name=task.dn.warehouse.name,mode=plan['mode'],shortages=plan['shortages'],
        version=None,layout=None,stations=[],station_id=None,stops=[],unmapped=[],route=None,issue=None,
        strategy='nearest_next_stop',walking_speed=WALK_METRES_PER_SECOND,
        editable=bool(actor[0] or {'company_all_access','picking_edit'} & actor[1]))
    return route_for_rows(task.dn.warehouse_id, options, plan['rows'], result)


def route_for_rows(warehouse_id, options, rows, result):
    """Shared published-map traversal for picking and cycle-count documents."""
    if not options['map_enabled'] or not options['route_enabled']:
        result['issue']='route_disabled'
        result['unmapped']=rows
        return result
    current = db.session.get(WarehouseLayout,warehouse_id)
    if not current or not current.published_version:
        result['issue']='no_published_map'
        result['unmapped']=rows
        return result
    try:
        version=int(request.args.get('version',current.published_version))
        if version <= 0:
            raise ValueError()
    except (ValueError,TypeError):
        raise BadRequestException('Invalid map version')
    snapshot=db.session.get(WarehouseLayoutVersion,(warehouse_id,version))
    if snapshot is None:
        raise BadRequestException('Published map version is unavailable')
    result.update(version=version,layout=snapshot.layout)
    from warehouse.location.models import Location
    from warehouse.warehouse.layout_bindings import resolve
    resolved=resolve(snapshot.layout,Location.query.filter_by(warehouse_id=warehouse_id).all())
    objects={obj['id']:(floor,obj) for floor in snapshot.layout['floors'] for obj in floor['objects'] if obj['type']=='rack'}
    bindings={ident:objects[value['rack_id']] for ident,value in resolved.items()}
    for floor in snapshot.layout['floors']:
        for obj in floor['objects']:
            if obj['type']=='station':
                result['stations'].append(dict(id=obj['id'],name=obj['name'],floor_id=floor['id'],floor_name=floor['name'],
                    point=(floor['id'],obj['x']+obj['width']/2,obj['y']+obj['height']/2)))
    stops={}
    for row in rows:
        binding=bindings.get(row['location_id'])
        if binding is None:
            result['unmapped'].append(row)
            continue
        floor,obj=binding
        stop=stops.setdefault(row['location_id'],dict(id=row['location_id'],location=row['location'],
            floor_id=floor['id'],floor_name=floor['name'],rack_name=obj['name'],items=[],
            points=[(floor['id'],*point) for point in pick_points(obj)]))
        stop['items'].append(row)
    result['stops']=list(stops.values())
    if not result['stations']:
        result['issue']='no_start_station'
        return result
    station_id=request.args.get('station_id',options['start_station_id'] or result['stations'][0]['id'])
    station=next((row for row in result['stations'] if row['id']==station_id),None)
    if station is None:
        raise BadRequestException('Start station does not belong to this map version')
    result['station_id']=station_id
    end_station_id=request.args.get('end_station_id',options['end_station_id'] or station_id)
    end_station=next((row for row in result['stations'] if row['id']==end_station_id),None)
    if end_station is None:
        raise BadRequestException('End station does not belong to this map version')
    result['end_station_id']=end_station_id
    origin=tuple(station['point'])
    if request.args.get('start_location_id'):
        try:
            location_id=int(request.args['start_location_id'])
            face=int(request.args.get('start_face',0))
            floor,rack=bindings[location_id]
            points=pick_points(rack)
            if face<0 or face>=len(points):
                raise ValueError()
            origin=(floor['id'],*points[face])
            result['start_location_id']=location_id
        except (ValueError,KeyError,TypeError):
            raise BadRequestException('Start location or face is not bound to this published map')
    if len(stops)>200:
        result['issue']='too_many_stops'
        return result
    try:
        result['route']=ordered_walk(snapshot.layout,origin,result['stops'],return_point=tuple(end_station['point']))
        if (result['route']['legs'] or origin!=tuple(end_station['point'])) and result['route']['returning'] is None:
            result['issue']='no_return_path'
    except RouteGeometryError as exc:
        result['issue']=str(exc)
    return result
