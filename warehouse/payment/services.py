from extensions.db import *
from extensions.error import BadRequestException, ForbiddenException, NotFoundException
from extensions.transaction import transactional
from warehouse.carrier.models import Carrier
from warehouse.delivery.models import DeliveryTask
from warehouse.dn.models import DN
from warehouse.inventory.services import InventoryService
from warehouse.warehouse.models import Warehouse
from .models import Payment
from datetime import datetime

class PaymentService:

    @staticmethod
    def get_payment_company_id(payment: Payment):
        """支付记录的归属公司 = 发货单所在仓库的公司（Payment 本身没有 company_id 列）"""
        delivery = payment.delivery
        dn = delivery.dn if delivery else None
        warehouse = dn.warehouse if dn else None
        return warehouse.company_id if warehouse else None

    @staticmethod
    def list_payments(filters: dict):
        """
        根据过滤条件，返回 Payment 的查询对象。

        :param filters: dict，包含可能的过滤字段
        :return: 一个已排序并过滤后的 SQLAlchemy Query 对象
        """
        query = Payment.query.order_by(Payment.id.desc())

        if filters.get('delivery_id'):
            query = query.filter(Payment.delivery_id == filters['delivery_id'])
        if filters.get('carrier_id'):
            query = query.filter(Payment.carrier_id == filters['carrier_id'])
        if filters.get('status'):
            query = query.filter(Payment.status == filters['status'])

        # 如果 filters 中没有 is_active 或其值为 None，则只返回 is_active=True
        if 'is_active' not in filters or filters['is_active'] is None:
            query = query.filter(Payment.is_active == True)
        else:
            # 否则按用户传入的值进行过滤
            query = query.filter(Payment.is_active == filters['is_active'])

        # 公司隔离：沿 delivery → dn → warehouse 找到归属公司
        if filters.get('company_id'):
            query = (
                query.join(DeliveryTask, Payment.delivery_id == DeliveryTask.id)
                     .join(DN, DeliveryTask.dn_id == DN.id)
                     .join(Warehouse, DN.warehouse_id == Warehouse.id)
                     .filter(Warehouse.company_id == filters['company_id'])
            )

        return query

    @staticmethod
    def get_payment(payment_id: int) -> Payment:
        """
        根据 payment_id 获取单个 Payment，不存在时抛出 404
        """
        payment = get_object_or_404(Payment, payment_id)
        return payment

    @staticmethod
    @transactional
    def create_payment(data: dict, created_by_id: int, actor_company_id: int | None = None) -> Payment:
        """
        创建新的 Payment（状态固定 pending）。

        :param data: 包含请求中的支付数据
        :param created_by_id: 创建者用户 ID
        :param actor_company_id: 调用方公司（平台管理员为 None）；发货单与承运商都必须属于该公司
        :return: 新创建的 Payment 对象
        """
        delivery = get_object_or_404(DeliveryTask, data.get('delivery_id'))
        carrier = get_object_or_404(Carrier, data.get('carrier_id'))

        delivery_company_id = delivery.dn.warehouse.company_id if delivery.dn and delivery.dn.warehouse else None
        if actor_company_id is not None and delivery_company_id != actor_company_id:
            raise ForbiddenException("Permission denied: delivery belongs to another company", 12001)
        if carrier.company_id != delivery_company_id:
            raise BadRequestException("Carrier does not belong to the same company as the delivery", 16027)

        new_payment = Payment(
            delivery_id=delivery.id,
            amount=data['amount'],
            currency=data.get('currency', 'JPY'),
            payment_method=data['payment_method'],
            status='pending',
            carrier_id=carrier.id,
            payment_time=data.get('payment_time'),
            remark=data.get('remark'),
            is_active=data.get('is_active', True),
            created_by=created_by_id
        )
        db.session.add(new_payment)
        # db.session.commit()
        return new_payment

    @staticmethod
    @transactional
    def update_payment(payment_id: int, data: dict) -> Payment:
        """
        更新指定 Payment。

        :param payment_id: Payment 的 ID
        :param data: 要更新的字段
        :return: 更新后的 Payment 对象
        :raises ValueError: 如果 Payment 的状态不可更新
        """
        payment = PaymentService.get_payment(payment_id)

        if payment.status != 'pending':
            raise BadRequestException("Cannot update a non-pending Payment", 16003)

        payment.payment_method = data.get('payment_method', payment.payment_method)
        payment.amount = data.get('amount', payment.amount)
        payment.currency = data.get('currency', payment.currency)
        payment.payment_time = data.get('payment_time', payment.payment_time)
        payment.remark = data.get('remark', payment.remark)
        payment.is_active = data.get('is_active', payment.is_active)

        # db.session.commit()
        return payment

    @staticmethod
    @transactional
    def delete_payment(payment_id: int):
        """
        删除指定 Payment（仅当其状态为 pending 时）
        """
        payment = PaymentService.get_payment(payment_id)
        if payment.status != 'pending':
            raise BadRequestException("Cannot delete a non-pending Payment", 16002)

        db.session.delete(payment)
        # db.session.commit()

    @staticmethod
    @transactional
    def process_payment(payment_id: int) -> Payment:
        """
        更新 Payment 的状态为 paid

        :param payment_id: Payment 的 ID
        :return: 更新后的 Payment 对象
        :raises ValueError: 如果 Payment 不处于 pending 状态
        """
        payment = PaymentService.get_payment(payment_id)
        if payment.status != 'pending':
            raise BadRequestException("Cannot process a non-pending Payment", 16007)

        payment.status = 'paid'
        payment.payment_time = datetime.now()
        # db.session.commit()
        return payment

    @staticmethod
    @transactional
    def cancel_payment(payment_id: int) -> Payment:
        """
        更新 Payment 的状态为 canceled

        :param payment_id: Payment 的 ID
        :return: 更新后的 Payment 对象
        :raises ValueError: 如果 Payment 不处于 pending 状态
        """
        payment = PaymentService.get_payment(payment_id)
        if payment.status != 'pending':
            raise BadRequestException("Cannot cancel a non-pending Payment", 16028)

        payment.status = 'canceled'
        # db.session.commit()
        return payment

