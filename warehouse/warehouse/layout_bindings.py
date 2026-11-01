"""Resolve current locations against published rack geometry without changing the snapshot."""
from extensions.db import db


def published(warehouse_id):
    from .layout import WarehouseLayout, WarehouseLayoutVersion
    current=db.session.get(WarehouseLayout,warehouse_id)
    return db.session.get(WarehouseLayoutVersion,(warehouse_id,current.published_version)) if current and current.published_version else None


def racks(layout):
    return [(floor,obj) for floor in layout.get('floors',[]) for obj in floor['objects'] if obj['type']=='rack']


def resolve(layout,locations):
    rack_rows=racks(layout)
    by_id={obj['id']:(floor,obj) for floor,obj in rack_rows}
    explicit={ident:obj['id'] for _,obj in rack_rows for ident in obj.get('location_ids',[])}
    by_code={}
    for _,obj in rack_rows:
        by_code.setdefault(obj.get('code') or obj['name'],[]).append(obj['id'])
    result={}
    for location in locations:
        rack_id=location.map_rack_id or explicit.get(location.id)
        column,level=location.rack_column,location.rack_level
        source='location' if location.map_rack_id else 'map'
        if not rack_id:
            matches=[]
            pieces=(location.code or '').rsplit('-',2)
            candidates=[]
            if len(pieces)>=2 and pieces[-1].isdigit():
                candidates.append(('-'.join(pieces[:-1]),1,int(pieces[-1])))
                if len(pieces)==3 and pieces[-2].isdigit():
                    candidates.append((pieces[0],int(pieces[-2]),int(pieces[-1])))
            for code,auto_column,auto_level in candidates:
                matches.extend((len(code),ident,auto_column,auto_level) for ident in by_code.get(code,[]))
            if matches:
                matches.sort(reverse=True)
                if len(matches)==1 or matches[0][0]>matches[1][0]:
                    _,rack_id,auto_column,auto_level=matches[0]
                    column=column or auto_column;level=level or auto_level;source='code'
        if rack_id not in by_id:
            continue
        floor,obj=by_id[rack_id]
        if (column is not None and (column<1 or obj.get('columns') and column>obj['columns'])) or (level is not None and (level<1 or obj.get('levels') and level>obj['levels'])):
            continue
        result[location.id]=dict(rack_id=rack_id,rack_name=obj['name'],floor_id=floor['id'],floor_name=floor['name'],
            column=column,level=level,source=source)
    return result
