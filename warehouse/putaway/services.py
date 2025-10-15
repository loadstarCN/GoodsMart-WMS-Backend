from sqlalchemy import or_
from extensions.db import *
from extensions.transaction import transactional
from warehouse.goods.models import Goods, GoodsLocation
from warehouse.goods.services import GoodsLocationService
from warehouse.inventory.services import InventoryService
from warehouse.location.models import Location
from .models import PutawayRecord


class PutawayService:
    """
    A service class that encapsulates the operations
    related to PutawayRecord.
    """

    @staticmethod
    def list_putaway_records(filters: dict):
        """
        根据过滤条件，返回 PutawayRecord 的查询对象，并按 id 降序排序。
        
        :param filters: dict，包含可能的过滤字段
        :return: 已排序并过滤后的 SQLAlchemy Query 对象
        """
        query = PutawayRecord.query.order_by(PutawayRecord.id.desc())

        # 如果需要根据过滤条件进行筛选，可按需添加
        if filters.get('goods_id'):
            query = query.filter(PutawayRecord.goods_id == filters['goods_id'])
        if filters.get('location_id'):
            query = query.filter(PutawayRecord.location_id == filters['location_id'])
        if filters.get('operator_id'):
            query = query.filter(PutawayRecord.operator_id == filters['operator_id'])
        if filters.get('start_time'):
            query = query.filter(PutawayRecord.putaway_time >= filters['start_time'])
        if filters.get('end_time'):
            query = query.filter(PutawayRecord.putaway_time <= filters['end_time'])

        # 针对 Goods 相关的过滤，先统一 join 一次
        if any(key in filters for key in ['goods_code', 'keyword']):
            query = query.join(Goods, PutawayRecord.goods_id == Goods.id)   

        if filters.get('goods_code'):                            
            query = query.filter(Goods.code.ilike(f"%{filters['goods_code']}%"))

        # 针对 Location 相关的过滤，先统一 join 一次
        if any(key in filters for key in ['location_code', 'warehouse_id','warehouse_ids','keyword']):
            query = query.join(Location, PutawayRecord.location_id == Location.id)        

        if filters.get('location_code'):
            query = query.filter(Location.code.ilike(f"%{filters['location_code']}%"))

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
                    Location.code.ilike(f"%{keyword}%")
                )
            )

        if filters.get('warehouse_id'):
            query = query.filter(Location.warehouse_id == filters['warehouse_id'])
        if filters.get('warehouse_ids'):
            query = query.filter(Location.warehouse_id.in_(filters['warehouse_ids']))       

        return query

    @staticmethod
    @transactional
    def create_putaway_record(data: dict, created_by_id: int) -> PutawayRecord:
        """
        创建一条新的上架记录
        
        :param data: dict，包含 goods_id, location_id, quantity, operator_id, remark 等字段
        :return: 新创建的 PutawayRecord 实例
        """
        new_record = PutawayRecord(
            goods_id=data['goods_id'],
            location_id=data['location_id'],
            quantity=data['quantity'],
            remark=data.get('remark', ''),
            operator_id=created_by_id
        )
        db.session.add(new_record)
        db.session.flush()

        goods_location_record = GoodsLocationService.get_goods_location_record(new_record.goods_id, new_record.location_id)
        if goods_location_record is None:
            # 创建新的库存信息
            data = {
                'goods_id': new_record.goods_id,
                'location_id': new_record.location_id,
                'quantity': new_record.quantity
            }
            goods_location_record = GoodsLocationService.create_goods_location(data)
        else:
            # 更新库存信息
            goods_location_record.quantity += new_record.quantity
        
        db.session.add(goods_location_record)
        db.session.flush()

        #更新库存信息
        InventoryService.putaway_completed(new_record.goods_id, new_record.location.warehouse_id,new_record.quantity)

        # db.session.commit()
        return new_record

    @staticmethod
    def get_putaway_record(record_id: int) -> PutawayRecord:
        """
        根据 record_id 获取单个 PutawayRecord，不存在时抛出 404
        """
        return get_object_or_404(PutawayRecord, record_id)

    @staticmethod
    @transactional
    def bulk_create_putaway_records(data_list: list, created_by_id: int) -> list:
        """
        批量创建上架记录
        :param data_list: list，每个元素为 dict，包含 goods_id, location_id, quantity, remark 等字段
        :param created_by_id: 创建者的用户ID
        :return: 创建成功的 PutawayRecord 实例列表
        """
        new_records = []
        for data in data_list:
            new_record = PutawayService.create_putaway_record(data, created_by_id)
            new_records.append(new_record)
            db.session.flush()
        
        # 装饰器 transactional 会在函数退出后自动提交事务
        return new_records
