"""Warehouse-specific outbound and navigation settings, preserving legacy defaults."""
from extensions.error import BadRequestException

DEFAULTS = dict(allow_unassigned=None, map_enabled=True, route_enabled=True,
                start_station_id=None, end_station_id=None, picking_sort='code')


def settings(warehouse):
    return {**DEFAULTS, **(warehouse.operations or {})}


def validate(value, warehouse=None):
    # Older records serialize this nullable field as null in full-record edits.
    # Explicit null restores inherited/default operation settings.
    if value is None:
        return dict(DEFAULTS)
    if not isinstance(value, dict) or set(value) - set(DEFAULTS):
        raise BadRequestException('Invalid warehouse operations settings')
    result = {**(settings(warehouse) if warehouse else DEFAULTS), **value}
    if result['allow_unassigned'] is not None and type(result['allow_unassigned']) is not bool:
        raise BadRequestException('allow_unassigned must be true, false or null')
    for key in ('map_enabled', 'route_enabled'):
        if type(result[key]) is not bool:
            raise BadRequestException(f'{key} must be a boolean')
    if result['route_enabled'] and not result['map_enabled']:
        raise BadRequestException('Enable warehouse maps before route planning')
    if result['picking_sort'] not in ('code', 'manual'):
        raise BadRequestException('Invalid picking sort')
    chosen = []
    for key in ('start_station_id', 'end_station_id'):
        ident = result[key]
        if ident is not None:
            if not isinstance(ident, str) or not ident or len(ident) > 100:
                raise BadRequestException('Invalid route station')
            chosen.append(ident)
    if chosen:
        from extensions.db import db
        from .layout import WarehouseLayout, WarehouseLayoutVersion
        current = db.session.get(WarehouseLayout, warehouse.id) if warehouse else None
        version = db.session.get(WarehouseLayoutVersion, (warehouse.id, current.published_version)) if current and current.published_version else None
        station_ids = {obj['id'] for floor in version.layout['floors'] for obj in floor['objects'] if obj['type'] == 'station'} if version else set()
        if not set(chosen) <= station_ids:
            raise BadRequestException('Route stations must belong to this warehouse published map')
    return result
