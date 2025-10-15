from sqlalchemy import func
from extensions.db import *
from extensions.error import BadRequestException
from extensions.transaction import transactional
from warehouse.goods.models import Goods
from warehouse.warehouse.models import Warehouse
from .models import Inventory

class InventoryService:

    @staticmethod
    def _calculate_total_stock(inventory: Inventory) -> int:
        """计算当前库存总量（包含所有状态库存的汇总值）

        Args:
            inventory (Inventory): 包含库存数据的模型实例，应包含以下字段：
                - onhand_stock: 实际在库库存
                - damage_stock: 损坏库存
                - return_stock: 退货待处理库存
                - sorted_stock: 分拣完成待上架库存
                - picked_stock: 已拣货未包装库存
                - packed_stock: 已包装待发货库存

        Returns:
            int: 所有库存状态的物理库存总量（单位：件/个）

        Note:
            当前计算公式为：
            总库存 = 实际在库库存 
                    + 损坏库存
                    + 退货库存
                    + 分拣库存
                    + 拣货库存
                    + 包装库存
            
            注意与业务规则的差异：
            [实际业务规则要求]
            可用库存 = 实际在库库存 
                    - 锁定库存 
                    - 待出库库存
            """
        return (
            inventory.onhand_stock +
            inventory.damage_stock +
            inventory.return_stock +
            inventory.sorted_stock + 
            inventory.picked_stock +
            inventory.packed_stock
        )
    

    @staticmethod
    def get_inventory(goods_id: int, warehouse_id: int) -> Inventory:
        """
        通过 goods_id 和 warehouse_id 获取 Inventory 实例，如不存在则抛出 404
        :param goods_id: 关联的商品 ID
        :param warehouse_id: 仓库 ID
        :return: Inventory 实例
        """
        # 注意：对于复合主键，需要传入元组
        return get_object_or_404(Inventory, (goods_id, warehouse_id))
    
    @staticmethod
    def list_inventories(filters: dict):
        """
        根据过滤条件，返回 Inventory 的查询对象。
        :param filters: dict，包含可能的过滤字段
        :return: 一个已排序并过滤后的 SQLAlchemy Query 对象
        """
        query = Inventory.query.order_by(Inventory.goods_id.desc())
        query = query.join(Warehouse).filter(Warehouse.is_active == True)
        
        if filters.get('goods_id'):
            query = query.filter(Inventory.goods_id == filters['goods_id'])
        if filters.get('warehouse_id'):
            query = query.filter(Inventory.warehouse_id == filters['warehouse_id'])
        if filters.get('warehouse_ids'):
            query = query.filter(Inventory.warehouse_id.in_(filters['warehouse_ids']))
        
        if filters.get('low_stock_threshold'):
            query = query.filter(Inventory.low_stock_threshold <= filters['low_stock_threshold'])
        if filters.get('high_stock_threshold'):
            query = query.filter(Inventory.high_stock_threshold >= filters['high_stock_threshold'])
            
        if 'goods_codes' in filters or 'keyword' in filters:
            query = query.join(Goods)
        if filters.get('goods_codes'):
            query = query.filter(Goods.code.in_(filters['goods_codes']))

        # Search by keyword in goods name, code, manufacturer, category, tags, brand
        if filters.get('keyword'):
            query = query.filter(
                func.lower(Goods.code).like(f"%{filters['keyword'].lower()}%") |
                func.lower(Goods.name).like(f"%{filters['keyword'].lower()}%") |
                func.lower(Goods.manufacturer).like(f"%{filters['keyword'].lower()}%") |
                func.lower(Goods.category).like(f"%{filters['keyword'].lower()}%") |
                func.lower(Goods.tags).like(f"%{filters['keyword'].lower()}%") |
                func.lower(Goods.brand).like(f"%{filters['keyword'].lower()}%")
            )

        return query

    @staticmethod
    @transactional
    def create_inventory(data) -> Inventory:
        """
        创建新的库存记录
        :param data: 包含库存字段的 dict（必须包含 goods_id 和 warehouse_id）
        :return: 新的 Inventory 实例
        """
        if 'goods_id' not in data or 'warehouse_id' not in data:
            raise BadRequestException("goods_id and warehouse_id are required to create an inventory record.", 16026)
          
        new_inventory = Inventory(
            goods_id=data['goods_id'],
            warehouse_id=data['warehouse_id'],
            total_stock=data.get('total_stock', 0),
            onhand_stock=data.get('onhand_stock', 0),
            locked_stock=data.get('locked_stock', 0),      # 初始化锁定库存
            damage_stock=data.get('damage_stock', 0),
            return_stock=data.get('return_stock', 0),
            asn_stock=data.get('asn_stock', 0),
            received_stock=data.get('received_stock', 0),
            sorted_stock=data.get('sorted_stock', 0),
            dn_stock=data.get('dn_stock', 0),
            picked_stock=data.get('picked_stock', 0),
            packed_stock=data.get('packed_stock', 0),        # 注意字段命名改为 packed_stock
            delivered_stock=data.get('delivered_stock', 0),  # 注意字段命名改为 delivered_stock
            remark=data.get('remark', ""),
        )

        db.session.add(new_inventory)
        # db.session.commit()
        return new_inventory

    @staticmethod
    @transactional
    def update_inventory(goods_id: int, warehouse_id: int, data) -> Inventory:
        """
        更新库存记录
        :param goods_id: 关联的商品 ID
        :param warehouse_id: 仓库 ID
        :param data: 包含更新字段的 dict
        :return: 更新后的 Inventory 实例
        """
        inventory = InventoryService.get_inventory(goods_id, warehouse_id)

        # 更新库存字段
        inventory.total_stock = data.get('total_stock', inventory.total_stock)
        inventory.onhand_stock = data.get('onhand_stock', inventory.onhand_stock)
        inventory.locked_stock = data.get('locked_stock', inventory.locked_stock)      
        inventory.damage_stock = data.get('damage_stock', inventory.damage_stock)
        inventory.return_stock = data.get('return_stock', inventory.return_stock)
        inventory.asn_stock = data.get('asn_stock', inventory.asn_stock)
        inventory.received_stock = data.get('received_stock', inventory.received_stock)
        inventory.sorted_stock = data.get('sorted_stock', inventory.sorted_stock)
        inventory.dn_stock = data.get('dn_stock', inventory.dn_stock)
        inventory.picked_stock = data.get('picked_stock', inventory.picked_stock)        
        inventory.packed_stock = data.get('packed_stock', inventory.packed_stock)        
        inventory.delivered_stock = data.get('delivered_stock', inventory.delivered_stock)  
        inventory.remark = data.get('remark', inventory.remark)  

        # db.session.commit()
        return inventory

    @staticmethod
    @transactional
    def delete_inventory(goods_id: int, warehouse_id: int):
        """
        删除库存记录
        :param goods_id: 关联的商品 ID
        :param warehouse_id: 仓库 ID
        :return: None
        """        
        inventory = InventoryService.get_inventory(goods_id, warehouse_id)
        db.session.delete(inventory)
        # db.session.commit()

    @staticmethod
    @transactional
    def lock_inventory(goods_id: int, warehouse_id: int, quantity: int):
        """
        锁定库存
        :param goods_id: 关联的商品 ID
        :param warehouse_id: 仓库 ID
        :param quantity: 锁定数量
        注意：这里不需要扣减 onhand_stock，使用的时候用onhand_stock - locked_stock计算可用库存
        """
        inventory = Inventory.query.filter_by(goods_id=goods_id, warehouse_id=warehouse_id).first()
        if inventory.onhand_stock < quantity:
            raise BadRequestException("Insufficient stock to lock", 15003)
        inventory.locked_stock += quantity
        # db.session.commit()
            
    @staticmethod
    @transactional
    def unlock_inventory(goods_id: int, warehouse_id: int, quantity: int):
        """
        解锁库存
        :param goods_id: 关联的商品 ID
        :param warehouse_id: 仓库 ID
        :param quantity: 解锁数量
        """
        inventory = Inventory.query.filter_by(goods_id=goods_id, warehouse_id=warehouse_id).first()
        if inventory.locked_stock < quantity:
            raise BadRequestException("Insufficient locked stock to unlock", 15004)
        inventory.locked_stock -= quantity
        # db.session.commit()

    @staticmethod
    @transactional
    def asn_received(goods_id: int, warehouse_id: int, quantity: int):
        """
        到货确认：ASN 库存减少，签收库存增加
        :param goods_id: 关联的商品 ID
        :param warehouse_id: 仓库 ID
        :param quantity: 到货数量
        """
        inventory = InventoryService.get_inventory(goods_id, warehouse_id)
        if inventory.asn_stock < quantity:
            raise BadRequestException("Not enough ASN stock.", 15005)
        inventory.asn_stock -= quantity
        inventory.received_stock += quantity
        # db.session.commit()

    @staticmethod
    @transactional
    def asn_completed(goods_id: int, warehouse_id: int, quantity: int, actual_quantity: int):
        """
        分拣完成：签收库存减少，分拣库存增加
        :param goods_id: 关联的商品 ID
        :param warehouse_id: 仓库 ID
        :param quantity: 签收数量
        :param actual_quantity: 实际分拣数量
        这里的实际分拣数量可能小于签收数量，表示部分商品缺货，也有可能大于签收数量，表示多拣货
        """
        inventory = InventoryService.get_inventory(goods_id, warehouse_id)
        # if inventory.received_stock < quantity:
        #     raise BadRequestException("Not enough received stock.", 15006)
        inventory.received_stock -= quantity
        if inventory.received_stock < 0:
            inventory.received_stock = 0
        # 分拣库存增加
        inventory.sorted_stock += actual_quantity
        inventory.total_stock = InventoryService._calculate_total_stock(inventory)
        # db.session.commit()

    @staticmethod
    @transactional
    def putaway_completed(goods_id: int, warehouse_id: int, quantity: int):
        """
        上架完成：分拣库存减少，现有库存增加
        :param goods_id: 关联的商品 ID
        :param warehouse_id: 仓库 ID
        :param quantity: 上架数量
        """
        inventory = InventoryService.get_inventory(goods_id, warehouse_id)
        if inventory.sorted_stock < quantity:
            raise BadRequestException("Not enough sort stock.",15007)
        inventory.sorted_stock -= quantity
        db.session.flush()
        InventoryService.update_and_calculate_stock(goods_id, warehouse_id)
        # db.session.commit()

    @staticmethod
    @transactional
    def removal_completed(goods_id: int, warehouse_id: int, quantity: int):
        """
        下架完成：现有库存减少
        :param goods_id: 关联的商品 ID
        :param warehouse_id: 仓库 ID
        :param quantity: 下架数量
        """
        inventory = InventoryService.get_inventory(goods_id, warehouse_id)
        inventory.sorted_stock += quantity
        db.session.flush()
        InventoryService.update_and_calculate_stock(goods_id, warehouse_id)
        # db.session.commit()

    
    
    @staticmethod
    @transactional
    def dn_picked(goods_id: int, warehouse_id: int, quantity: int,picked_quantity: int):
        """
        拣货完成：DN 库存减少，拣货库存增加
        :param goods_id: 关联的商品 ID
        :param warehouse_id: 仓库 ID
        :param quantity: 拣货数量
        :param picked_quantity: 实际拣货数量
        注意：picked_quantity 可能小于 quantity，表示部分商品缺货
        如果缺货的情况下需要重置 dn_stock 的值，否则会锁死商品dn数量。
        """
        inventory = InventoryService.get_inventory(goods_id, warehouse_id)
        if inventory.dn_stock < picked_quantity:
            raise BadRequestException("Not enough DN stock.", 15008)
        inventory.dn_stock -= quantity
        inventory.picked_stock += picked_quantity
        inventory.total_stock = InventoryService._calculate_total_stock(inventory)
        # db.session.commit()

    @staticmethod
    @transactional
    def dn_packed(goods_id: int, warehouse_id: int, quantity: int):
        """
        包装完成：拣货库存减少，配送库存增加
        :param goods_id: 关联的商品 ID
        :param warehouse_id: 仓库 ID
        :param quantity: 包装数量
        """
        inventory = InventoryService.get_inventory(goods_id, warehouse_id)
        if inventory.picked_stock < quantity:
            raise BadRequestException("Not enough pick stock.", 15009)
        inventory.picked_stock -= quantity
        inventory.packed_stock += quantity
        inventory.total_stock = InventoryService._calculate_total_stock(inventory)
        # db.session.commit()

    @staticmethod
    @transactional
    def dn_delivered(goods_id: int, warehouse_id: int, quantity: int):
        """
        发货完成：配送库存减少
        :param goods_id: 关联的商品 ID
        :param warehouse_id: 仓库 ID
        :param quantity: 发货数量
        """
        inventory = InventoryService.get_inventory(goods_id, warehouse_id)
        if inventory.packed_stock < quantity:
            raise BadRequestException("Not enough packed stock.", 15010)
        inventory.packed_stock -= quantity
        inventory.delivered_stock += quantity
        inventory.total_stock = InventoryService._calculate_total_stock(inventory)
        # db.session.commit()

    @staticmethod
    @transactional
    def dn_completed(goods_id: int, warehouse_id: int, quantity: int):
        """
        签收确认：减少已发货库存
        :param goods_id: 关联的商品 ID
        :param warehouse_id: 仓库 ID
        :param quantity: 签收数量
        """
        inventory = InventoryService.get_inventory(goods_id, warehouse_id)
        if inventory.delivered_stock < quantity:
            raise BadRequestException("Not enough delivered stock.", 15011)
        inventory.delivered_stock -= quantity
        inventory.total_stock = InventoryService._calculate_total_stock(inventory)
        # db.session.commit()

    @staticmethod
    @transactional
    def dn_closed(goods_id: int, warehouse_id: int, quantity: int):
        """
        关闭 DN：将所有相关库存状态重置
        :param goods_id: 关联的商品 ID
        :param warehouse_id: 仓库 ID
        :param quantity: 关闭数量
        """
        inventory = InventoryService.get_inventory(goods_id, warehouse_id)
        if inventory.dn_stock < quantity:
            raise BadRequestException("Not enough DN stock to close.", 15012)
        inventory.dn_stock -= quantity
        inventory.total_stock = InventoryService._calculate_total_stock(inventory)
        # db.session.commit()

    @staticmethod
    @transactional
    def set_low_stock_threshold(goods_id: int, warehouse_id: int, threshold: int):
        """
        设置低库存阈值
        :param goods_id: 关联的商品 ID
        :param warehouse_id: 仓库 ID
        :param threshold: 新的低库存阈值
        """
        inventory = InventoryService.get_inventory(goods_id, warehouse_id)
        if threshold < -1:
            raise BadRequestException("Low stock threshold must be non-negative or -1 to disable.", 15013)
        inventory.low_stock_threshold = threshold
        # db.session.commit()

    @staticmethod
    @transactional
    def set_high_stock_threshold(goods_id: int, warehouse_id: int, threshold: int):
        """
        设置高库存阈值
        :param goods_id: 关联的商品 ID
        :param warehouse_id: 仓库 ID
        :param threshold: 新的高库存阈值
        """
        inventory = InventoryService.get_inventory(goods_id, warehouse_id)
        if threshold < 0 and threshold != -1:
            raise BadRequestException("High stock threshold must be non-negative or -1 to disable.", 15014)
        if threshold != -1 and threshold <= inventory.low_stock_threshold:
            raise BadRequestException("High stock threshold must be greater than low stock threshold.", 15015)
        inventory.high_stock_threshold = threshold
        # db.session.commit()

    @staticmethod
    def check_stock_thresholds(goods_id: int, warehouse_id: int):
        """
        检查当前库存是否超出阈值范围（基于可用库存）
        :param goods_id: 关联的商品 ID
        :param warehouse_id: 仓库 ID
        :return: dict，包含库存状态信息
        """
        inventory = InventoryService.get_inventory(goods_id, warehouse_id)
        
        return {
            "is_below_low_threshold": (
                inventory.low_stock_threshold != -1
                and inventory.available_stock_for_sale < inventory.low_stock_threshold
            ),
            "is_above_high_threshold": (
                inventory.high_stock_threshold != -1
                and inventory.available_stock_for_sale > inventory.high_stock_threshold
            ),
        }
    
    @staticmethod
    @transactional
    def update_and_calculate_stock(goods_id: int, warehouse_id: int):

        from warehouse.goods.services import GoodsLocationService
        
        """
        更新库存并计算库存状态
        :param goods_id: 关联的商品 ID
        :param warehouse_id: 仓库 ID
        """
        inventory = InventoryService.get_inventory(goods_id, warehouse_id)
        # 查找GoodsLocation表中的库存记录（这里假设支持传入 warehouse_id）
        gl_record = GoodsLocationService.get_quantity_by_location_type(goods_id, warehouse_id)
        
        if 'standard' in gl_record:
            inventory.onhand_stock = gl_record['standard']
        if 'damaged' in gl_record:
            inventory.damage_stock = gl_record['damaged']
        if 'return' in gl_record:
            inventory.return_stock = gl_record['return']
        
        # 使用私有方法计算总库存
        inventory.total_stock = InventoryService._calculate_total_stock(inventory)
        db.session.add(inventory)
        db.session.flush()
        # db.session.commit()

    @staticmethod
    @transactional
    def update_and_calculate_asn_stock(goods_id: int, warehouse_id: int):
        """
        更新 ASN 库存并计算库存状态
        :param goods_id: 关联的商品 ID
        :param warehouse_id: 仓库 ID
        """
        inventory = InventoryService.get_inventory(goods_id, warehouse_id)

        from warehouse.asn.models import ASN,ASNDetail

        # 计算 ASN 库存
        # 构建查询：求和 ASNDetail.quantity
        total_quantity = db.session.query(func.coalesce(func.sum(ASNDetail.quantity), 0)).\
            join(ASN, ASNDetail.asn_id == ASN.id).\
            filter(
                ASNDetail.goods_id == goods_id,
                ASN.warehouse_id == warehouse_id,
                ASN.is_active == True,
                ASN.status == 'pending'
            ).scalar()
        
        inventory.asn_stock = total_quantity
        db.session.flush()
        # db.session.commit()

    
    @staticmethod
    @transactional
    def update_and_calculate_dn_stock(goods_id: int, warehouse_id: int):
        """
        更新 DN 库存并计算库存状态
        :param goods_id: 关联的商品 ID
        :param warehouse_id: 仓库 ID
        """
        inventory = InventoryService.get_inventory(goods_id, warehouse_id)

        from warehouse.dn.models import DN,DNDetail

        # 计算 DN 库存
        # 构建查询：求和 DN.quantity
        total_quantity = db.session.query(func.coalesce(func.sum(DNDetail.quantity), 0)).\
            join(DN, DNDetail.dn_id == DN.id).\
            filter(
                DNDetail.goods_id == goods_id,
                DN.warehouse_id == warehouse_id,
                DN.is_active == True,
                DN.status == 'pending'
            ).scalar()
        
        inventory.dn_stock = total_quantity

        db.session.add(inventory)
        db.session.flush()
        # db.session.commit()

            

    
    
