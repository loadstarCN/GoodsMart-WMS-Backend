from flask import g
from flask_restx import Resource,abort
from extensions.error import ForbiddenException
from system.common import permission_required,paginate
from warehouse.common import warehouse_required,add_warehouse_filter,check_warehouse_access
from .schemas import (
    api_ns,
    delivery_task_model,
    delivery_task_input_model,
    delivery_task_complete_input_model,
    delivery_task_signed_input_model,
    delivery_pagination_model,
    delivery_pagination_parser,
    delivery_monthly_stats_parser
)
from .services import DeliveryTaskService


@api_ns.doc(security="jsonWebToken")
@api_ns.route('/')
class DeliveryTaskList(Resource):

    @permission_required(["all_access","company_all_access","delivery_read"])
    @warehouse_required()
    @api_ns.expect(delivery_pagination_parser)
    @api_ns.marshal_with(delivery_pagination_model)
    def get(self):
        """
        Get all Deliveries with optional filters & pagination
        """
        args = delivery_pagination_parser.parse_args()
        page = args.get('page')
        per_page = args.get('per_page')

        # Filter parameters
        filters = {
            'dn_id': args.get('dn_id'),
            'recipient_id': args.get('recipient_id'),
            'carrier_id': args.get('carrier_id'),
            'operator_id': args.get('operator_id'),
            'shipping_address': args.get('shipping_address'),
            'tracking_number': args.get('tracking_number'),
            'order_number': args.get('order_number'),
            'transportation_mode': args.get('transportation_mode'),
            'status': args.get('status'),
            'expected_shipping_date': args.get('expected_shipping_date'),
            'actual_shipping_date': args.get('actual_shipping_date'),
            'is_active': args.get('is_active'),
            'keyword': args.get('keyword')
        }
        filters = add_warehouse_filter(filters)
        query = DeliveryTaskService.list_tasks(filters)
        return paginate(query, page, per_page)

    @permission_required(["all_access","company_all_access","delivery_edit"])
    @api_ns.expect(delivery_task_input_model)
    @api_ns.marshal_with(delivery_task_model)
    def post(self):
        """
        Create a new Delivery
        """
        data = api_ns.payload
        created_by = g.current_user.id
        new_delivery = DeliveryTaskService.create_task(data, created_by)
        return new_delivery, 201


@api_ns.doc(security="jsonWebToken")
@api_ns.route('/<int:task_id>')
class DeliveryTaskDetailView(Resource):

    @permission_required(["all_access","company_all_access","delivery_read"])
    @warehouse_required()
    @api_ns.marshal_with(delivery_task_model)
    def get(self, task_id):
        """
        Get details of a specific Delivery
        """
        task = DeliveryTaskService.get_task(task_id)
        if not check_warehouse_access(task.dn.warehouse_id):
            raise ForbiddenException("You do not have access to this Delivery Task", 12001)
        
        return task, 200

    @permission_required(["all_access","company_all_access","delivery_edit"])
    @api_ns.expect(delivery_task_input_model)
    @api_ns.marshal_with(delivery_task_model)
    def put(self, task_id):
        """
        Update a specific Delivery
        """
        data = api_ns.payload
        updated_delivery = DeliveryTaskService.update_task(task_id, data)

        return updated_delivery

    @permission_required(["all_access","company_all_access","delivery_delete"])
    def delete(self, task_id):
        """
        Delete a specific Delivery
        """
        DeliveryTaskService.delete_task(task_id)
        return {"message": "Delivery deleted successfully"}, 200


@api_ns.doc(security="jsonWebToken")
@api_ns.route('/<int:task_id>/process/')
class DeliveryTaskProcess(Resource):
    
    @permission_required(["all_access","company_all_access","delivery_edit"])
    @warehouse_required()
    @api_ns.marshal_with(delivery_task_model)
    def put(self, task_id):
        """
        Process a specific Delivery (set status to shipping)
        """
        operator_id = g.current_user.id
        task = DeliveryTaskService.get_task(task_id)
        if not check_warehouse_access(task.dn.warehouse_id):
            raise ForbiddenException("You do not have access to this Delivery Task", 12001)

        updated_delivery = DeliveryTaskService.process_task(task_id,operator_id)
        
        return updated_delivery


@api_ns.doc(security="jsonWebToken")
@api_ns.route('/<int:task_id>/complete/')
class DeliveryTaskComplete(Resource):
    
    @permission_required(["all_access","company_all_access","delivery_edit"])
    @warehouse_required()
    @api_ns.expect(delivery_task_complete_input_model)
    @api_ns.marshal_with(delivery_task_model)
    def put(self, task_id):
        """
        Complete a specific Delivery (set status to completed)
        """
        data = api_ns.payload
        operator_id = g.current_user.id
        task = DeliveryTaskService.get_task(task_id)
        if not check_warehouse_access(task.dn.warehouse_id):
            raise ForbiddenException("You do not have access to this Delivery Task", 12001)
        
        updated_delivery = DeliveryTaskService.complete_task(task_id,data=data,operator_id=operator_id)
        
        return updated_delivery


@api_ns.doc(security="jsonWebToken")
@api_ns.route('/<int:task_id>/sign/')
class DeliveryTaskSign(Resource):
    
    @permission_required(["all_access","company_all_access","delivery_edit"])
    @warehouse_required()
    @api_ns.expect(delivery_task_signed_input_model)
    @api_ns.marshal_with(delivery_task_model)
    def put(self, task_id):
        """
        Sign a specific Delivery (set status to signed)
        """
        data = api_ns.payload
        operator_id = g.current_user.id
        task = DeliveryTaskService.get_task(task_id)
        if not check_warehouse_access(task.dn.warehouse_id):
            raise ForbiddenException("You do not have access to this Delivery Task", 12001)
        
        updated_delivery = DeliveryTaskService.sign_task(task_id,data=data,operator_id=operator_id)
        
        return updated_delivery
    


@api_ns.doc(security="jsonWebToken")
@api_ns.route('/monthly-stats')
class DeliveryMonthlyStats(Resource):
    """
    Get monthly Sorting statistics
    """
    @permission_required(["all_access","company_all_access","sorting_read"])
    @warehouse_required()
    @api_ns.expect(delivery_monthly_stats_parser)
    def get(self):
        # 解析请求参数
        args = delivery_monthly_stats_parser.parse_args()
        months = args.get('months',6)  # 默认值为 6 个月

        # 将筛选参数打包到 dict 中
        filters = {
            'months': args.get('months'),
        }

        filters = add_warehouse_filter(filters)
        stats = DeliveryTaskService.get_delivery_monthly_stats(months=months,filters=filters)
        return stats, 200
    
@api_ns.doc(security="jsonWebToken")
@api_ns.route('/status-overview-stats')
class PickingStatusOverviewStats(Resource):
    """
    Get Sorting status overview statistics
    """
    @permission_required(["all_access","company_all_access","sorting_read"])
    @warehouse_required()
    def get(self):
        # 解析请求参数
        # 解析请求参数
        args = delivery_monthly_stats_parser.parse_args()

        # 将筛选参数打包到 dict 中
        filters = {}
        filters = add_warehouse_filter(filters)
        stats = DeliveryTaskService.get_status_overview(filters=filters)
        return stats, 200
