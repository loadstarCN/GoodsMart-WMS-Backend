import re
from extensions.error import BadRequestException


def position_fields(data, warehouse_id=None, location=None):
    result = {}
    for key in ('picking_order', 'rack_column', 'rack_level'):
        if key not in data:
            continue
        value = data[key]
        if value is not None and (type(value) is not int or not 1 <= value <= 2147483647):
            raise BadRequestException(f'{key} must be a positive integer or null')
        result[key] = value
    if 'map_rack_id' in data:
        value=data['map_rack_id']
        if value is not None and (not isinstance(value,str) or not value or len(value)>100):
            raise BadRequestException('Invalid published rack ID')
        result['map_rack_id']=value
    rack_id=result.get('map_rack_id',location.map_rack_id if location else None)
    if result and rack_id:
        from warehouse.warehouse.layout_bindings import published,racks
        snapshot=published(warehouse_id)
        rack=next((obj for _,obj in racks(snapshot.layout) if obj['id']==rack_id),None) if snapshot else None
        if rack is None:
            raise BadRequestException('Rack must belong to this warehouse published map')
        for key,limit in [('rack_column','columns'),('rack_level','levels')]:
            value=result.get(key,getattr(location,key,None))
            if value is not None and rack.get(limit) and value>rack[limit]:
                raise BadRequestException(f'{key} exceeds published rack dimensions')
    return result


def location_key(location, warehouse=None):
    from warehouse.warehouse.operations import settings
    natural = tuple((1,int(part)) if part.isdigit() else (0,part.casefold())
                    for part in re.split(r'(\d+)', location.code or ''))
    if warehouse and settings(warehouse)['picking_sort'] == 'manual':
        return (location.picking_order is None, location.picking_order or 0, natural, location.id)
    return (natural, location.id)
