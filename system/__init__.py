from flask import Blueprint
from flask_restx import Api
from .user.schemas import api_ns as user_ns
from .logs.schemas import api_ns as logs_ns
from .third_party.schemas import api_ns as third_party_ns
from .limiter.schemas import api_ns as limiter_ns

blueprint = Blueprint('user_api', __name__)
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

api.add_namespace(user_ns,path='/user')
api.add_namespace(logs_ns,path='/logs')
api.add_namespace(third_party_ns,path='/third-party')
api.add_namespace(limiter_ns,path='/limiter')

