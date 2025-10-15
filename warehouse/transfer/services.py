from flask_restx import abort
from extensions.db import *
from extensions.error import BadRequestException, NotFoundException
from extensions.transaction import transactional
from warehouse.goods.models import Goods
from warehouse.goods.services import GoodsLocationService
from warehouse.inventory.services import InventoryService
from warehouse.location.models import Location
from sqlalchemy import or_
from sqlalchemy.orm import aliased
from .models import TransferRecord

class TransferService:
    """
    A service class that encapsulates the operations
    related to TransferRecord.
    """

    @staticmethod
    def list_transfer_records(filters: dict):
        """
        根据过滤条件，返回 TransferRecord 的查询对象，并按 id 降序排序。
        
        :param filters: dict，包含可能的过滤字段
        :return: 已排序并过滤后的 SQLAlchemy Query 对象
        """
        query = TransferRecord.query.order_by(TransferRecord.id.desc())

        # 基础筛选
        if filters.get('goods_id'):
            query = query.filter(TransferRecord.goods_id == filters['goods_id'])
        if filters.get('from_location_id'):
            query = query.filter(TransferRecord.from_location_id == filters['from_location_id'])
        if filters.get('to_location_id'):
            query = query.filter(TransferRecord.to_location_id == filters['to_location_id'])

        if filters.get('location_id'):
            query = query.filter(or_(TransferRecord.from_location_id == filters['location_id'],
                                     TransferRecord.to_location_id == filters['location_id']))
        if filters.get('operator_id'):
            query = query.filter(TransferRecord.operator_id == filters['operator_id'])
        if filters.get('start_time'):
            query = query.filter(TransferRecord.transfer_time >= filters['start_time'])
        if filters.get('end_time'):
            query = query.filter(TransferRecord.transfer_time <= filters['end_time'])

        # 针对 Goods 相关的过滤，统一 join Goods
        if any(key in filters for key in ['goods_code', 'keyword']):
            query = query.join(Goods, TransferRecord.goods_id == Goods.id)
        if filters.get('goods_code'):
            query = query.filter(Goods.code.ilike(f"%{filters['goods_code']}%"))

        # 针对 Location 相关的过滤（包括 from_location 和 to_location），统一使用别名 join
        if any(key in filters for key in ['from_location_code', 'to_location_code', 'warehouse_id', 'warehouse_ids', 'keyword']):
            from_loc = aliased(Location)
            to_loc = aliased(Location)
            query = query.join(from_loc, TransferRecord.from_location_id == from_loc.id)
            query = query.join(to_loc, TransferRecord.to_location_id == to_loc.id)

            if filters.get('from_location_code'):
                query = query.filter(from_loc.code.ilike(f"%{filters['from_location_code']}%"))
            if filters.get('to_location_code'):
                query = query.filter(to_loc.code.ilike(f"%{filters['to_location_code']}%"))

            if filters.get('warehouse_id'):
                query = query.filter(or_(from_loc.warehouse_id == filters['warehouse_id'],
                                         to_loc.warehouse_id == filters['warehouse_id']))
            if filters.get('warehouse_ids'):
                query = query.filter(or_(from_loc.warehouse_id.in_(filters['warehouse_ids']),
                                         to_loc.warehouse_id.in_(filters['warehouse_ids'])))

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
                        from_loc.code.ilike(f"%{keyword}%"),
                        to_loc.code.ilike(f"%{keyword}%")
                    )
                )

        return query

    @staticmethod
    @transactional
    def create_transfer_record(data: dict, created_by_id: int) -> TransferRecord:
        """
        创建一条新的移库记录
        
        :param data: dict，包含 goods_id, from_location_id, to_location_id, quantity, operator_id, remark 等字段
        :return: 新创建的 TransferRecord 实例
        """
        goods_id=data['goods_id']
        from_location_id=data['from_location_id']
        to_location_id=data['to_location_id']
        quantity=data['quantity']
        
        # 先去获取商品库位GoodsLocation信息，如果找不到会抛出404错误，捕获这个错误，并创建新的库存信息
        from_goods_location_record = GoodsLocationService.get_goods_location_record(goods_id, from_location_id)
        to_goods_location_record = GoodsLocationService.get_goods_location_record(goods_id, to_location_id)
        if from_goods_location_record is None:
            raise NotFoundException("Goods not found in the specified from_location", 14003)
        if from_goods_location_record.quantity < data['quantity']:
            raise BadRequestException("Insufficient inventory in the specified location", 15001)
        
        new_record = TransferRecord(
            goods_id=goods_id,
            from_location_id=from_location_id,
            to_location_id=to_location_id,
            quantity=quantity,
            operator_id=created_by_id,
            remark=data.get('remark', '')
        )
        db.session.add(new_record)
        db.session.flush()
        
        from_goods_location_record.quantity -= quantity
        if from_goods_location_record.quantity == 0:
            db.session.delete(from_goods_location_record)
        else:
            db.session.add(from_goods_location_record)
        

        if to_goods_location_record is None:
            # 创建新的库存信息
            data = {
                'goods_id': new_record.goods_id,
                'location_id': new_record.to_location_id,
                'quantity': new_record.quantity
            }
            to_goods_location_record = GoodsLocationService.create_goods_location(data)
        else:
            to_goods_location_record.quantity += new_record.quantity

        db.session.add(to_goods_location_record)        
        db.session.flush()   

        #更新库存信息
        InventoryService.update_and_calculate_stock(new_record.goods_id, new_record.to_location.warehouse_id)
        # db.session.commit()
        return new_record

    @staticmethod
    def get_transfer_record(record_id: int) -> TransferRecord:
        """
        根据 record_id 获取单个 TransferRecord，不存在时抛出 404
        
        :param record_id: TransferRecord 的 ID
        :return: TransferRecord 实例
        :raises NotFound: 如果 record_id 不存在
        """
        return get_object_or_404(TransferRecord, record_id)

    @staticmethod
    @transactional
    def bulk_create_transfer_records(data_list: list, created_by_id: int) -> list:
        """
        批量创建移库记录
        
        :param data_list: list，每个元素为 dict，包含 goods_id, from_location_id, to_location_id, quantity, remark 等字段
        :param created_by_id: 创建者的用户ID
        :return: 创建成功的 TransferRecord 实例列表
        """
        new_records = []
        for data in data_list:
            new_record = TransferService.create_transfer_record(data, created_by_id)
            new_records.append(new_record)
            db.session.flush()

        
        return new_records
