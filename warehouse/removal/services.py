from flask_restx import abort
from sqlalchemy import or_
from extensions.db import *
from extensions.error import BadRequestException, NotFoundException
from extensions.transaction import transactional
from warehouse.goods.models import Goods
from warehouse.goods.services import GoodsLocationService
from warehouse.inventory.services import InventoryService
from warehouse.location.models import Location
from .models import RemovalRecord


class RemovalService:
    """
    A service class that encapsulates the operations
    related to RemovalRecord.
    """

    @staticmethod
    def list_removal_records(filters: dict):
        """
        根据过滤条件，返回 RemovalRecord 的查询对象，并按 id 降序排序。
        """
        query = RemovalRecord.query.order_by(RemovalRecord.id.desc())

        # 基础过滤条件
        if filters.get('goods_id'):
            query = query.filter(RemovalRecord.goods_id == filters['goods_id'])
        if filters.get('location_id'):
            query = query.filter(RemovalRecord.location_id == filters['location_id'])
        if filters.get('operator_id'):
            query = query.filter(RemovalRecord.operator_id == filters['operator_id'])
        if filters.get('start_time'):
            query = query.filter(RemovalRecord.removal_time >= filters['start_time'])
        if filters.get('end_time'):
            query = query.filter(RemovalRecord.removal_time <= filters['end_time'])

        # 针对 Goods 相关的过滤，先统一 join 一次
        if any(key in filters for key in ['goods_code', 'keyword']):
            query = query.join(Goods, RemovalRecord.goods_id == Goods.id)

        # 关联商品表处理商品编码过滤
        if filters.get('goods_code'):
            query = query.filter(Goods.code.ilike(f"%{filters['goods_code']}%"))

        # 统一关联库位表处理相关过滤
        if any(key in filters for key in ['location_code', 'warehouse_id','warehouse_ids','keyword']):
            query = query.join(Location, RemovalRecord.location_id == Location.id)

        # 库位编码过滤
        if filters.get('location_code'):
            query = query.filter(Location.code.ilike(f"%{filters['location_code']}%"))

        # 关键字联合搜索
        if filters.get('keyword'):
            keyword = filters['keyword']
            query = query.filter(
                or_(
                    Goods.code.ilike(f"%{keyword}%"),
                    Goods.name.ilike(f"%{keyword}%"),
                    Goods.manufacturer.ilike(f"%{keyword}%"),
                    Goods.category.ilike(f"%{keyword}%"),
                    Goods.tags.ilike(f"%{keyword}%"),
                    Goods.brand.ilike(f"%{keyword}%"),
                    Location.code.ilike(f"%{keyword}%"),
                    RemovalRecord.reason.ilike(f"%{keyword}%")  # 包含下架原因搜索
                )
            )

        # 仓库过滤条件
        if filters.get('warehouse_id'):
            query = query.filter(Location.warehouse_id == filters['warehouse_id'])
        if filters.get('warehouse_ids'):
            query = query.filter(Location.warehouse_id.in_(filters['warehouse_ids']))

        return query

    @staticmethod
    @transactional
    def create_removal_record(data: dict, created_by_id: int) -> RemovalRecord:
        """
        创建一条新的下架记录

        :param data: dict，包含 goods_id, location_id, quantity, operator_id, reason, remark 等字段
        :return: 新创建的 RemovalRecord 实例
        """
        goods_id=data['goods_id']
        location_id=data['location_id']
        quantity=data['quantity']

        # 先去获取商品库位GoodsLocation信息，如果找不到到会抛出404错误，捕获这个错误，并更新库存信息，同时还需要判断库存是否足够
        goods_location_record = GoodsLocationService.get_goods_location_record(goods_id, location_id)
        if goods_location_record is None:
            raise NotFoundException("GoodsLocation not found", 13004)
        
        new_record = RemovalRecord(
            goods_id=goods_id,
            location_id=location_id,
            quantity=quantity,
            reason=data['reason'],
            remark=data.get('remark', ''),
            operator_id=created_by_id
        )
        db.session.add(new_record)
        db.session.flush()
        
        goods_location_record.quantity -= new_record.quantity
        if goods_location_record.quantity < 0:
            raise BadRequestException("Stock is not enough", 15001)
        elif goods_location_record.quantity == 0:           
            db.session.delete(goods_location_record)
        else:
            db.session.add(goods_location_record)
        db.session.flush()

        #更新库存信息
        InventoryService.removal_completed(new_record.goods_id, new_record.location.warehouse_id, new_record.quantity)

        # db.session.commit()
        return new_record

    @staticmethod
    def get_removal_record(record_id: int) -> RemovalRecord:
        """
        根据 record_id 获取单个 RemovalRecord，不存在时抛出 404
        """
        return get_object_or_404(RemovalRecord, record_id)


    @staticmethod
    @transactional
    def bulk_create_removal_records(data_list: list, created_by_id: int) -> list:
        """
        批量创建下架记录

        :param data_list: list，每个元素为 dict，包含 goods_id, location_id, quantity, reason, remark 等字段
        :param created_by_id: 创建者的用户ID
        :return: 创建成功的 RemovalRecord 实例列表
        """
        new_records = []
        for data in data_list:
            new_record = RemovalService.create_removal_record(data, created_by_id)
            new_records.append(new_record)
            db.session.flush()

        # transactional 装饰器会在函数退出后统一提交
        return new_records