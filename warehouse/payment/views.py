from flask import g
from flask_restx import Resource,abort
from system.common import permission_required,paginate
from system.common.permissions import get_actor_company_id
from warehouse.common import require_company_scope, require_actor_user_id

from .schemas import (
    api_ns,
    payment_model,
    payment_input_model,
    payment_update_model,
    pagination_model,
    pagination_parser

)
from .services import PaymentService


def _get_owned_payment(payment_id: int):
    """支付记录没有自己的 company_id，归属跟随发货单（delivery → dn → warehouse → company）"""
    payment = PaymentService.get_payment(payment_id)
    require_company_scope(PaymentService.get_payment_company_id(payment), 'payment')
    return payment


@api_ns.doc(security="jsonWebToken")
@api_ns.route('/')
class PaymentList(Resource):

    @permission_required(["all_access","company_all_access","payment_read"])
    @api_ns.expect(pagination_parser)
    @api_ns.marshal_with(pagination_model)
    def get(self):
        """
        获取所有支付记录，支持过滤和分页
        """
        args = pagination_parser.parse_args()
        page = args.get('page')
        per_page = args.get('per_page')

        filters = {
            'delivery_id': args.get('delivery_id'),
            'carrier_id': args.get('carrier_id'),
            'status': args.get('status'),
            'is_active': args.get('is_active'),
            # 员工 / 公司级 API Key 只能看本公司的支付记录
            'company_id': get_actor_company_id(),
        }

        query = PaymentService.list_payments(filters)
        return paginate(query, page, per_page)

    @permission_required(["all_access","company_all_access","payment_edit"])
    @api_ns.expect(payment_input_model)
    @api_ns.marshal_with(payment_model)
    def post(self):
        """
        创建新的支付记录（状态固定为 pending，之后走 process / cancel 动作）
        """
        data = api_ns.payload
        # 状态只能由 process / cancel 动作推进，不接受客户端直接指定
        data.pop('status', None)
        data.pop('created_by', None)
        created_by = require_actor_user_id()
        new_payment = PaymentService.create_payment(data, created_by, get_actor_company_id())
        return new_payment, 201


@api_ns.doc(security="jsonWebToken")
@api_ns.route('/<int:payment_id>')
class PaymentDetailView(Resource):

    @permission_required(["all_access","company_all_access","payment_read"])
    @api_ns.marshal_with(payment_model)
    def get(self, payment_id):
        """
        获取单个支付记录的详细信息
        """
        return _get_owned_payment(payment_id)

    @permission_required(["all_access","company_all_access","payment_edit"])
    @api_ns.expect(payment_update_model)
    @api_ns.marshal_with(payment_model)
    def put(self, payment_id):
        """
        更新指定的支付记录
        """
        data = api_ns.payload
        _get_owned_payment(payment_id)
        updated_payment = PaymentService.update_payment(payment_id, data)
        return updated_payment

    @permission_required(["all_access","company_all_access","payment_delete"])
    def delete(self, payment_id):
        """
        删除指定的支付记录（仅限状态为 pending 时）
        """
        _get_owned_payment(payment_id)
        PaymentService.delete_payment(payment_id)
        return {"message": "Payment deleted successfully"}, 200


@api_ns.doc(security="jsonWebToken")
@api_ns.route('/<int:payment_id>/process/')
class PaymentProcess(Resource):

    @permission_required(["all_access","company_all_access","payment_edit"])
    @api_ns.marshal_with(payment_model)
    def put(self, payment_id):
        """
        处理支付记录，将状态更新为 "paid"
        """
        _get_owned_payment(payment_id)
        updated_payment = PaymentService.process_payment(payment_id)
        return updated_payment


@api_ns.doc(security="jsonWebToken")
@api_ns.route('/<int:payment_id>/cancel/')
class PaymentCancel(Resource):

    @permission_required(["all_access","company_all_access","payment_edit"])
    @api_ns.marshal_with(payment_model)
    def put(self, payment_id):
        """
        取消支付记录，将状态更新为 "canceled"
        """
        _get_owned_payment(payment_id)
        updated_payment = PaymentService.cancel_payment(payment_id)
        return updated_payment

