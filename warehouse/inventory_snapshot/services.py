from sqlalchemy import func
from extensions.db import *
from extensions.transaction import transactional
from warehouse.goods.models import Goods
from warehouse.goods.services import GoodsLocationService
from warehouse.warehouse.models import Warehouse
from .models import InventorySnapshot

class InventorySnapshotService:
    """库存快照服务类"""

    @staticmethod
    @transactional
    def create_snapshot(warehouse_id):
        """创建库存快照"""
        # 获取所有商品的当前库存信息
        filters = {
            'warehouse_id': warehouse_id
        }
        all_goods_locations = GoodsLocationService.list_goods_locations(filters)
        
        # 创建快照记录
        for item in all_goods_locations:
            snapshot = InventorySnapshot(
                goods_id=item.goods_id,
                warehouse_id=warehouse_id,
                location_id=item.location_id,
                quantity=item.quantity,
                snapshot_time= func.now()
            )
            db.session.add(snapshot)

        # db.session.commit()  # 提交事务

    @staticmethod
    def get_snapshots(filters: dict):
        """获取库存快照列表"""        
        query = InventorySnapshot.query
        # 根据过滤条件筛选
        if filters.get('goods_id'):
            query = query.filter(InventorySnapshot.goods_id == filters['goods_id'])
        if filters.get('location_id'):
            query = query.filter(InventorySnapshot.location_id == filters['location_id'])
        if filters.get('warehouse_id'):
            query = query.filter(InventorySnapshot.warehouse_id == filters['warehouse_id'])
        if filters.get('start_time'):
            query = query.filter(InventorySnapshot.snapshot_time >= filters['start_time'])
        if filters.get('end_time'):
            query = query.filter(InventorySnapshot.snapshot_time <= filters['end_time'])

        return query
        
    
    