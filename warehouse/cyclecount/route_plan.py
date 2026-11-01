"""Read-only count-sheet ordering using the warehouse's published walking map."""
from warehouse.location.ordering import location_key
from warehouse.warehouse.operations import settings
from warehouse.picking.route_plan import route_for_rows


def route_plan(task):
    options = settings(task.warehouse)
    details = sorted(task.task_details, key=lambda row: (location_key(row.location, task.warehouse), row.id))
    rows = [dict(id=row.id, location_id=row.location_id, location=row.location.code) for row in details]
    result = dict(task_id=task.id, warehouse_id=task.warehouse_id, picking_sort=options['picking_sort'],
                  version=None, layout=None, stations=[], station_id=None, stops=[], unmapped=[], route=None, issue=None)
    route_for_rows(task.warehouse_id, options, rows, result)
    order = [leg['stop_id'] for leg in (result['route'] or {}).get('legs', [])]
    rank = {ident: index for index, ident in enumerate(order)}
    result['detail_order'] = [row['id'] for row in sorted(rows, key=lambda row: rank.get(row['location_id'], len(rank)))]
    return result
