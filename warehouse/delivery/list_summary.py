"""Batch pickup summaries for delivery list rows; no carrier network requests."""
from warehouse.carrier.models import Carrier
from warehouse.dn.models import CarrierPickup, DNCarrierShipment


def attach_pickups(items):
    items = list(items)
    if not items:
        return items
    carrier_ids = {(getattr(item, '_shipping', None) or {}).get('carrier_id') for item in items}
    methods = {row.id: row.pickup_method for row in Carrier.query.filter(Carrier.id.in_(carrier_ids)).all()}
    bookings = {}
    # The latest live waybill is also the one used for the shipping-stage tracking number.
    for shipment, pickup in (DNCarrierShipment.query.add_entity(CarrierPickup)
            .outerjoin(CarrierPickup, CarrierPickup.id == DNCarrierShipment.pickup_id)
            .filter(DNCarrierShipment.dn_id.in_({item.dn_id for item in items}),
                    DNCarrierShipment.status.in_(('active', 'cancelling')))
            .order_by(DNCarrierShipment.id.desc()).all()):
        if shipment.dn_id not in bookings:
            bookings[shipment.dn_id] = (dict(id=pickup.id, status=pickup.status, reason=pickup.reason,
                date=pickup.pickup_date.isoformat(), ready_time=pickup.ready_time, close_time=pickup.close_time)
                if pickup else None)
    for item in items:
        shipping = getattr(item, '_shipping', None) or {}
        item._pickup_summary = dict(method=methods.get(shipping.get('carrier_id')), booking=bookings.get(item.dn_id))
    return items
