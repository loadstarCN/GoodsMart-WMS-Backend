"""Preview and atomically create code ranges; existing locations are never modified."""
from sqlalchemy.exc import IntegrityError
from extensions.db import db
from extensions.error import BadRequestException, ConflictException
from extensions.transaction import transactional
from warehouse.common import require_positive_int
from .models import Location
from .services import LocationService


def codes(body):
    zone = body.get('zone', '')
    if not isinstance(zone, str) or len(zone.strip()) > 20 or any(ord(c)<32 for c in zone):
        raise BadRequestException('Zone must contain at most 20 characters')
    zone = zone.strip()
    values = {key:require_positive_int(body.get(key),key) for key in ('rack_from','rack_to','level_from','level_to')}
    pad = body.get('rack_digits',2)
    if type(pad) is not int or not 1<=pad<=6:
        raise BadRequestException('rack_digits must be between 1 and 6')
    count=(values['rack_to']-values['rack_from']+1)*(values['level_to']-values['level_from']+1)
    if values['rack_to']<values['rack_from'] or values['level_to']<values['level_from'] or not 1<=count<=500:
        raise BadRequestException('Select increasing ranges producing 1 to 500 locations')
    result=[]
    for rack in range(values['rack_from'],values['rack_to']+1):
        for level in range(values['level_from'],values['level_to']+1):
            code='-'.join(filter(None,[zone,str(rack).zfill(pad),str(level)]))
            if len(code)>50:
                raise BadRequestException('Generated location code exceeds 50 characters')
            result.append((code,level))
    return result


@transactional
def generate(warehouse_id, body, operator_id):
    if type(body.get('execute',False)) is not bool:
        raise BadRequestException('execute must be a boolean')
    generated=codes(body)
    kind=body.get('location_type','standard')
    if kind not in Location.LOCATION_TYPES:
        raise BadRequestException('Invalid location type')
    numbers=LocationService._clean_numbers(body)
    description=body.get('description') or None
    if description is not None and (not isinstance(description,str) or len(description)>255):
        raise BadRequestException('Description must contain at most 255 characters')
    existing={row.code:row for row in Location.query.filter(Location.warehouse_id==warehouse_id,
        Location.code.in_([code for code,_ in generated])).all()}
    new=[]
    if body.get('execute'):
        for code,level in generated:
            if code not in existing:
                row=Location(warehouse_id=warehouse_id,code=code,location_type=kind,description=description,
                    rack_column=1,rack_level=level,created_by=operator_id,is_active=True,**numbers)
                db.session.add(row);new.append(row)
        try: db.session.flush()
        except IntegrityError:
            raise ConflictException('Locations changed during generation; preview again')
    created={row.code:row.id for row in new}
    return dict(total=len(generated),existing=len(existing),created=len(new),
        pending=len(generated)-len(existing) if not body.get('execute') else 0,
        items=[dict(code=code,existing=code in existing,id=existing[code].id if code in existing else created.get(code),
                    rack_column=1,rack_level=level) for code,level in generated])
