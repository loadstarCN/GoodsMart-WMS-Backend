from extensions.db import *
from extensions.error import BadRequestException, NotFoundException
from extensions.transaction import transactional
from warehouse.inventory.services import InventoryService
from .models import Payment
from datetime import datetime

class PaymentService:

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
    def create_payment(data: dict, created_by_id: int) -> Payment:
        """
        创建新的 Payment。

        :param data: 包含请求中的支付数据
        :param created_by_id: 创建者用户 ID
        :return: 新创建的 Payment 对象
        """
        new_payment = Payment(
            delivery_id=data['delivery_id'],
            amount=data['amount'],
            currency=data.get('currency', 'JPY'),
            payment_method=data['payment_method'],
            status=data['status'],
            carrier_id=data['carrier_id'],
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

