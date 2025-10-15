from flask import Blueprint
from flask_restx import Api
from .views import api_ns as task_ns

blueprint = Blueprint('task_api', __name__)
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

api.add_namespace(task_ns,path='/task')
