"""Live per-product analysis, scoped by the inventory metrics caller."""
from extensions.db import db
from warehouse.location.models import Location
from .history import realtime_balances
from .models import GoodsLot, IN_WAREHOUSE_STOCK_TYPES


def current_distribution(warehouse_id, goods_id):
    balances = realtime_balances(warehouse_id, [goods_id])
    keys = [key for key, quantity in balances.items() if quantity > 0 and key[3] in IN_WAREHOUSE_STOCK_TYPES]
    location_ids, lot_ids = {key[2] for key in keys if key[2]}, {key[1] for key in keys if key[1]}
    locations = {row.id: row for row in Location.query.filter(Location.id.in_(location_ids)).all()} if location_ids else {}
    lots = {row.id: row for row in GoodsLot.query.filter(GoodsLot.id.in_(lot_ids)).all()} if lot_ids else {}
    rows = []
    for key in keys:
        _goods, lot_id, location_id, stock_type = key
        location, lot = locations.get(location_id), lots.get(lot_id)
        rows.append(dict(location_id=location_id, location_code=location.code if location else None,
                         lot_id=lot_id, lot_no=lot.lot_no if lot else None,
                         expiry_date=lot.expiry_date.isoformat() if lot and lot.expiry_date else None,
                         stock_type=stock_type, quantity=balances[key]))
    return sorted(rows, key=lambda row: (-row['quantity'], row['location_code'] or '', row['lot_id'] or 0, row['stock_type']))


def current_age_layers(warehouse, goods_id, distribution):
    from collections import defaultdict
    from warehouse.common import clock
    from .flows import inbound_events
    from .metrics import age_layers
    quantities, entries, lots = defaultdict(int), defaultdict(list), {}
    for row in distribution:
        quantities[row['lot_id']] += row['quantity']
        lots[row['lot_id']] = row
    for _goods, at, quantity, _movement, lot in inbound_events(warehouse.id, clock.now(), [goods_id], include_lot=True):
        if at is not None:
            entries[lot].append((at, quantity))
    today, rows = clock.local_today(warehouse), []
    for lot, quantity in quantities.items():
        used, unknown = age_layers(quantity, entries[lot])
        for at, amount in used + ([(None, unknown)] if unknown else []):
            rows.append(dict(lot_id=lot, lot_no=lots[lot]['lot_no'], expiry_date=lots[lot]['expiry_date'],
                in_at=clock.utc_iso(at), quantity=amount,
                age_days=max(0, (today-clock.local_date(at, warehouse)).days) if at else None))
    return sorted(rows, key=lambda row: (row['age_days'] is None, -(row['age_days'] or 0), row['lot_id'] or 0))


def flow_distribution(warehouse, goods_id, start, end):
    """Attribute actual flow quantities, using legacy documents only before ledger cutover."""
    from collections import defaultdict
    from sqlalchemy import func
    from warehouse.asn.models import ASN, ASNDetail
    from warehouse.dn.models import DN, DNDetail
    from warehouse.recipient.models import Recipient
    from warehouse.supplier.models import Supplier
    from system.third_party.models import APIKey
    from .models import StockLedger
    from .flows import _cutover
    from warehouse.common import clock
    outbound, inbound = defaultdict(int), defaultdict(int)
    ledger = StockLedger.query.filter(StockLedger.warehouse_id == warehouse.id,
        StockLedger.goods_id == goods_id, StockLedger.occurred_at >= start, StockLedger.occurred_at < end,
        StockLedger.quantity > 0, StockLedger.movement_type.in_(('ship', 'sort_in', 'adjust_gain')))
    adjustment = 0
    for row in ledger.yield_per(500):
        if row.movement_type == 'ship' and row.stock_type == 'in_transit':
            outbound[row.doc_id if row.doc_type == 'dn' else None] += row.quantity
        elif row.movement_type == 'sort_in':
            inbound[row.doc_id if row.doc_type == 'asn' else None] += row.quantity
        elif row.movement_type == 'adjust_gain':
            adjustment += row.quantity
    cutover = _cutover(warehouse.id)
    if cutover is None or start < cutover:
        upper = min(end, cutover) if cutover else end
        for ident, quantity in db.session.query(DN.id, func.sum(DNDetail.delivered_quantity)).join(DNDetail).filter(
                DN.warehouse_id == warehouse.id, DN.is_active.is_(True), DN.status.in_(('delivered','completed')),
                DNDetail.goods_id == goods_id, DN.delivered_at >= start, DN.delivered_at < upper).group_by(DN.id):
            outbound[ident] += int(quantity or 0)
        at = func.coalesce(ASN.completed_at, ASN.created_at)
        for ident, quantity in db.session.query(ASN.id, func.sum(ASNDetail.actual_quantity)).join(ASNDetail).filter(
                ASN.warehouse_id == warehouse.id, ASN.is_active.is_(True), ASN.status == 'completed',
                ASNDetail.goods_id == goods_id, at >= start, at < upper).group_by(ASN.id):
            inbound[ident] += int(quantity or 0)
        from warehouse.adjustment.models import Adjustment, AdjustmentDetail
        adjustment += int(db.session.query(func.coalesce(func.sum(AdjustmentDetail.adjustment_quantity), 0)).join(
            Adjustment, Adjustment.id == AdjustmentDetail.adjustment_id).filter(Adjustment.warehouse_id == warehouse.id,
            Adjustment.is_active.is_(True), Adjustment.status == 'completed', AdjustmentDetail.goods_id == goods_id,
            AdjustmentDetail.adjustment_quantity > 0, Adjustment.completed_at >= start, Adjustment.completed_at < upper).scalar())
    dns = {row.id: row for row in DN.query.filter(DN.id.in_([key for key in outbound if key]), DN.warehouse_id == warehouse.id).all()}
    asns = {row.id: row for row in ASN.query.filter(ASN.id.in_([key for key in inbound if key]), ASN.warehouse_id == warehouse.id).all()}
    recipients = {row.id: row.name for row in Recipient.query.filter(Recipient.id.in_({row.recipient_id for row in dns.values()})).all()} if dns else {}
    suppliers = {row.id: row.name for row in Supplier.query.filter(Supplier.id.in_({row.supplier_id for row in asns.values()})).all()} if asns else {}
    sources = {row.id: row.system_name for row in APIKey.query.filter(APIKey.id.in_({row.api_key_id for row in dns.values() if row.api_key_id})).all()} if dns else {}
    groups = {key: {} for key in ('recipient','country','source','supplier')}
    def add(dimension, ident, name, quantity, document=None):
        if quantity <= 0:
            return
        row = groups[dimension].setdefault(ident, dict(id=ident, name=name, quantity=0, entries=[]))
        row['quantity'] += quantity
        row['entries'].append(dict(id=document.id if document else None,
                                  order_number=document.order_number if document else None,
                                  quantity=quantity))
    for ident, quantity in outbound.items():
        dn = dns.get(ident)
        recipient = dn.recipient_id if dn else None
        country = str((dn.ship_to or {}).get('country') or '').strip().upper() if dn else ''
        source = dn.api_key_id if dn else None
        add('recipient', recipient, recipients.get(recipient), quantity, dn)
        add('country', country or None, country or None, quantity, dn)
        add('source', source, sources.get(source), quantity, dn)
    for ident, quantity in inbound.items():
        asn = asns.get(ident)
        supplier = asn.supplier_id if asn else None
        add('supplier', supplier, suppliers.get(supplier), quantity, asn)
        if quantity > 0:
            row = groups['supplier'][supplier]
            row.setdefault('documents', 0)
            row.setdefault('dated_documents', 0)
            row.setdefault('on_time', 0)
            if asn:
                row['documents'] += 1
                if asn.received_at is not None and asn.expected_arrival_date is not None:
                    row['dated_documents'] += 1
                    row['on_time'] += int(clock.local_date(asn.received_at, warehouse) <= asn.expected_arrival_date)
            row['on_time_rate'] = row['on_time']/row['dated_documents'] if row['dated_documents'] else None
    return dict(outbound=sum(outbound.values()), inbound=sum(inbound.values()), adjustment_gain=adjustment,
                **{key: sorted(values.values(), key=lambda row: (-row['quantity'], str(row['id'] or ''))) for key, values in groups.items()})
