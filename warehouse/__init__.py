from flask import Blueprint
from flask_restx import Api
from .company.schemas import api_ns as company_ns
from .department.schemas import api_ns as department_ns
from .staff.schemas import api_ns as staff_ns
from .warehouse.schemas import api_ns as warehouse_ns
from .location.schemas import api_ns as location_ns
from .supplier.schemas import api_ns as supplier_ns
from .recipient.schemas import api_ns as recipient_ns
from .carrier.schemas import api_ns as carrier_ns
from .goods.schemas import api_ns as goods_ns
from .inventory.schemas import api_ns as inventory_ns
from .asn.schemas import api_ns as asn_ns
from .sorting.schemas import api_ns as sorting_ns
from .putaway.schemas import api_ns as putaway_ns
from .removal.schemas import api_ns as removal_ns
from .dn.schemas import api_ns as dn_ns
from .picking.schemas import api_ns as picking_ns
from .packing.schemas import api_ns as packing_ns
from .delivery.schemas import api_ns as delivery_ns
from .cyclecount.schemas import api_ns as cyclecount_ns
from .adjustment.schemas import api_ns as adjustment_ns
from .transfer.schemas import api_ns as transfer_ns
from .payment.schemas import api_ns as payment_ns

blueprint = Blueprint('warehouse_api', __name__)
api = Api(
    blueprint,
    version='0.1',
    title='GoodsMart WMS API',
    description=(
        "GoodsMart Warehouse Management System (WMS) API\n\n"
        "This API provides a robust and scalable solution for managing warehouse operations, "
        "including inventory tracking, order processing, shipment management, and reporting.\n\n"
        "**Key Features:**\n"
        "- Inventory management: track stock levels, locations, and statuses\n"
        "- Order processing: handle incoming and outgoing orders\n"
        "- Shipment management: schedule and monitor shipments\n"
        "- User role management: restrict access based on user roles (e.g., admin, warehouse staff)\n\n"
        "**Documentation:**\n"
        "Visit `/doc` for detailed API documentation and usage examples."
    ),
    doc='/doc',
    
)


api.add_namespace(company_ns, path='/company')
api.add_namespace(department_ns, path='/department')
api.add_namespace(warehouse_ns, path='/warehouse')
api.add_namespace(location_ns, path='/location')
api.add_namespace(staff_ns, path='/staff')
api.add_namespace(supplier_ns, path='/supplier')
api.add_namespace(recipient_ns, path='/recipient')
api.add_namespace(carrier_ns, path='/carrier')
api.add_namespace(goods_ns, path='/goods')
api.add_namespace(inventory_ns, path='/inventory')
api.add_namespace(asn_ns, path='/asn')
api.add_namespace(sorting_ns, path='/sorting')
api.add_namespace(putaway_ns, path='/putaway')
api.add_namespace(removal_ns, path='/removal')
api.add_namespace(dn_ns, path='/dn')
api.add_namespace(picking_ns, path='/picking')
api.add_namespace(packing_ns, path='/packing')
api.add_namespace(delivery_ns, path='/delivery')
api.add_namespace(cyclecount_ns, path='/cyclecount')
api.add_namespace(adjustment_ns, path='/adjustment')
api.add_namespace(transfer_ns, path='/transfer')
api.add_namespace(payment_ns, path='/payment')