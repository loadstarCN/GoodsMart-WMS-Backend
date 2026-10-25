from sqlalchemy import func, case
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import lazyload
from extensions.db import *
from extensions.error import BadRequestException, NotFoundException
from extensions.transaction import transactional
from warehouse.goods.models import Goods
from warehouse.warehouse.models import Warehouse
from .models import Inventory

class InventoryService:

    @staticmethod
    def _get_for_update(goods_id: int, warehouse_id: int) -> Inventory:
        """SELECT ... FOR UPDATE — 写操作专用，防止并发更新导致库存数据竞争"""
        inventory = (
            Inventory.query
            .options(lazyload('*'))
            .filter_by(goods_id=goods_id, warehouse_id=warehouse_id)
            .with_for_update()
            .first()
        )
        if not inventory:
            raise NotFoundException(
                f"Inventory not found for goods {goods_id} in warehouse {warehouse_id}", 43001
            )
        return inventory

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
        query = InventoryService._scoped(query.join(Warehouse), filters)

        if filters.get('goods_id'):
            query = query.filter(Inventory.goods_id == filters['goods_id'])
        # 只看还有待上架的（sorted_stock > 0；其中 sorted_damage_stock 只能上到 damaged 库位）
        if filters.get('pending_putaway'):
            query = query.filter(Inventory.sorted_stock > 0)

        if filters.get('low_stock_threshold'):
            query = query.filter(Inventory.low_stock_threshold <= filters['low_stock_threshold'])
        if filters.get('high_stock_threshold'):
            query = query.filter(Inventory.high_stock_threshold >= filters['high_stock_threshold'])
        # 低于补货阈值（可售 < low）/ 高于积压阈值（可售 > high）；阈值为 -1（不启用）的不算
        sale = Inventory.onhand_stock - Inventory.locked_stock - Inventory.dn_stock
        if filters.get('below_low_threshold'):
            query = query.filter(Inventory.low_stock_threshold != -1, sale < Inventory.low_stock_threshold)
        if filters.get('above_high_threshold'):
            query = query.filter(Inventory.high_stock_threshold != -1, sale > Inventory.high_stock_threshold)

        if 'goods_codes' in filters or 'keyword' in filters:
            query = query.join(Goods)
        if filters.get('goods_codes'):
            query = query.filter(Goods.code.in_(filters['goods_codes']))

        # Search by keyword in goods name, code, manufacturer, category, tags, brand
        return InventoryService._keyword(query, filters.get('keyword'))

    @staticmethod
    def _keyword(query, keyword):
        """商品关键字（编码 / 名称 / 厂商 / 分类 / 标签 / 品牌包含，不分大小写）；query 已 join Goods"""
        if not keyword:
            return query
        like = f"%{keyword.lower()}%"
        return query.filter(
            func.lower(Goods.code).like(like) |
            func.lower(Goods.name).like(like) |
            func.lower(Goods.manufacturer).like(like) |
            func.lower(Goods.category).like(like) |
            func.lower(Goods.tags).like(like) |
            func.lower(Goods.brand).like(like)
        )

    @staticmethod
    def _scoped(query, filters: dict):
        """库存行的范围（列表与待上架汇总共用；query 已 join Warehouse）：启用中的仓库；
        warehouse_id / warehouse_ids（add_warehouse_filter 给的当前仓库 / 可访问仓库）；company_id"""
        query = query.filter(Warehouse.is_active == True)
        if filters.get('warehouse_id'):
            query = query.filter(Inventory.warehouse_id == filters['warehouse_id'])
        if filters.get('warehouse_ids'):
            query = query.filter(Inventory.warehouse_id.in_(filters['warehouse_ids']))
        if filters.get('company_id'):
            query = query.filter(Warehouse.company_id == filters['company_id'])
        return query

    @staticmethod
    def pending_putaway_summary(filters: dict, overdue_days: int | None = None) -> dict:
        """待上架汇总（GET /inventory/pending-putaway/summary，首页 / 侧栏的数字）：范围同库存列表，
        只算 sorted_stock > 0 的行（keyword 同列表）→ {inventory_count, goods_count, total_quantity, damaged_quantity, good_quantity,
        overdue_days, overdue_quantity, overdue_inventory_count, oldest_pending_since}（超期按先进先上，见 pending.py）"""
        from warehouse.common import clock
        from .pending import PENDING_PUTAWAY_OVERDUE_DAYS, overdue_summary
        if overdue_days is None:
            overdue_days = PENDING_PUTAWAY_OVERDUE_DAYS
        query = db.session.query(Inventory.goods_id, Inventory.warehouse_id, Inventory.sorted_stock,
                                 Inventory.sorted_damage_stock).select_from(Inventory).join(
            Warehouse, Warehouse.id == Inventory.warehouse_id)
        # keyword：与库存列表一致（商品编码 / 名称 / 厂商 / 分类 / 标签 / 品牌）
        if filters.get('keyword'):
            query = InventoryService._keyword(query.join(Goods, Goods.id == Inventory.goods_id), filters['keyword'])
        rows = InventoryService._scoped(query, filters).filter(Inventory.sorted_stock > 0).all()
        total = sum(int(row.sorted_stock or 0) for row in rows)
        damaged = sum(min(int(row.sorted_damage_stock or 0), int(row.sorted_stock or 0)) for row in rows)
        overdue = overdue_summary([(row.goods_id, row.warehouse_id, int(row.sorted_stock)) for row in rows],
                                  overdue_days, clock.now())
        return {
            'warehouse_id': filters.get('warehouse_id'),
            'company_id': filters.get('company_id'),
            'inventory_count': len(rows),
            'goods_count': len({row.goods_id for row in rows}),
            'total_quantity': total,
            'damaged_quantity': damaged,
            'good_quantity': total - damaged,
            'overdue_days': overdue_days,
            **overdue,
        }

    @staticmethod
    @transactional
    def create_inventory(data) -> Inventory:
        """
        新建一条库存记录（各库存量为 0）：实物库存只能经 StockService 记账（warehouse/stock/service.py），
        这里只给 ensure_inventory 建全 0 行用
        :param data: dict，必须包含 goods_id 和 warehouse_id；可带 remark
        :return: 新的 Inventory 实例
        """
        if 'goods_id' not in data or 'warehouse_id' not in data:
            raise BadRequestException("goods_id and warehouse_id are required to create an inventory record.", 16026)

        new_inventory = Inventory(
            goods_id=data['goods_id'],
            warehouse_id=data['warehouse_id'],
            remark=data.get('remark', ""),
        )
        db.session.add(new_inventory)
        return new_inventory

    @staticmethod
    @transactional
    def ensure_inventory(goods_id: int, warehouse_id: int) -> Inventory:
        """取库存行，没有就新建（各库存量为 0）。

        新建放在 savepoint 里（B-107）：同一新商品第一次被并发建 ASN / 正向调整时，后到的一方撞主键
        (goods_id, warehouse_id) → 只回滚 savepoint 并重新读取对方已提交的行，不再 500。
        PostgreSQL 上后到的 INSERT 会等先到的事务结束：对方提交 → 主键冲突走这里；对方回滚 → 本次插入成功。
        """
        inventory = db.session.get(Inventory, (goods_id, warehouse_id))
        if inventory is not None:
            return inventory
        try:
            with db.session.begin_nested():
                inventory = InventoryService.create_inventory({'goods_id': goods_id, 'warehouse_id': warehouse_id})
                db.session.flush()
        except IntegrityError:
            inventory = db.session.get(Inventory, (goods_id, warehouse_id))
            if inventory is None:
                raise
        return inventory

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
        inventory = InventoryService._get_for_update(goods_id, warehouse_id)
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
        inventory = InventoryService._get_for_update(goods_id, warehouse_id)
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
        inventory = InventoryService._get_for_update(goods_id, warehouse_id)
        if inventory.asn_stock < quantity:
            raise BadRequestException("Not enough ASN stock.", 15005)
        inventory.asn_stock -= quantity
        inventory.received_stock += quantity
        # db.session.commit()

    @staticmethod
    @transactional
    def asn_completed(goods_id: int, warehouse_id: int, quantity: int, actual_quantity: int,
                      damage_quantity: int = 0, *, op=None, doc_line_id: int | None = None):
        """
        分拣完成：签收库存减少，分拣库存增加
        :param goods_id: 关联的商品 ID
        :param warehouse_id: 仓库 ID
        :param quantity: 签收数量
        :param actual_quantity: 实际分拣数量（良品 + 破损）
        :param damage_quantity: 其中的破损数量：记进待上架·破损（只能上到 damaged 库位）
        :param op: 调用方的 StockOp（sort_in，带单据）；不传则单独记一笔
        这里的实际分拣数量可能小于签收数量，表示部分商品缺货，也有可能大于签收数量，表示多拣货
        received_stock 不是实物（签收时按计划量占位），照旧在这里直接改、不记流水；实收按 sort_in 记账：
        （仓外）→ 待上架·良品 / 待上架·破损
        """
        from warehouse.stock.service import Leg, StockOp
        inventory = InventoryService._get_for_update(goods_id, warehouse_id)
        # 签收库存不足说明单据没有走过 asn_received（或库存已被别的流程扣走），
        # 静默清零会掩盖数据不一致，直接报错让调用方处理。
        if inventory.received_stock < quantity:
            raise BadRequestException("Not enough received stock.", 15006)
        inventory.received_stock -= quantity
        actual_quantity = actual_quantity or 0
        damaged = max(0, min(damage_quantity or 0, actual_quantity))
        own = op is None
        if own:
            op = StockOp(warehouse_id, 'sort_in')
        op.move(goods_id, actual_quantity - damaged, dst=Leg.pending(), doc_line_id=doc_line_id)
        op.move(goods_id, damaged, dst=Leg.pending(damaged=True), doc_line_id=doc_line_id)
        if own:
            op.commit()

    @staticmethod
    @transactional
    def asn_cancelled(goods_id: int, warehouse_id: int, quantity: int):
        """
        取消已签收的 ASN：回滚 asn_received 的签收库存。
        asn_stock 由调用方在单据关闭后用 update_and_calculate_asn_stock 重算。
        :param goods_id: 关联的商品 ID
        :param warehouse_id: 仓库 ID
        :param quantity: 原签收数量
        """
        inventory = InventoryService._get_for_update(goods_id, warehouse_id)
        if inventory.received_stock < quantity:
            raise BadRequestException("Not enough received stock to cancel.", 15006)
        inventory.received_stock -= quantity
        # db.session.commit()

    # 这些原因的下架按破损品回到待上架（只能再上到 damaged 库位）；从 damaged 库位下架的也一样
    DAMAGED_REMOVAL_REASONS = frozenset({'damage', 'damaged'})

    @staticmethod
    @transactional
    def dn_picked(goods_id: int, warehouse_id: int, quantity: int, picked_quantity: int):
        """
        拣货完成：释放 DN 预占（dn_stock）。实物由拣货完成的 pick 记账（库位 → 已拣，warehouse/picking/services.py）
        :param goods_id: 关联的商品 ID
        :param warehouse_id: 仓库 ID
        :param quantity: 该 DN 该商品的计划量（整笔释放）
        :param picked_quantity: 实际拣货数量
        注意：picked_quantity 可能小于 quantity，表示部分商品缺货；缺的部分不再继续预占（否则会锁死商品 dn 数量）
        """
        inventory = InventoryService._get_for_update(goods_id, warehouse_id)
        if inventory.dn_stock < picked_quantity:
            raise BadRequestException("Not enough DN stock.", 15008)
        # Release this DN's full reservation when picking closes. Only the
        # physically picked amount moved to picked_stock (pick); any short quantity
        # becomes available to other DNs again.
        if inventory.dn_stock < quantity:
            raise BadRequestException("Not enough DN stock.", 15008)
        inventory.dn_stock -= quantity

    @staticmethod
    @transactional
    def dn_packed(goods_id: int, warehouse_id: int, quantity: int, picked_quantity: int | None = None, *,
                  op=None, doc_line_id: int | None = None):
        """
        包装完成：已拣 → 已打包（pack）；少打包的差额 已拣 → 待上架良品（pack_short_return）
        :param goods_id: 关联的商品 ID
        :param warehouse_id: 仓库 ID
        :param quantity: 实际包装数量
        :param picked_quantity: 该 DN 该商品的已拣数量（缺省等于 quantity）
        :param op: 调用方的 StockOp（带单据）；不传则单独记一笔
        注意：quantity 可能小于 picked_quantity，表示少打包。与拣货缺货时 dn_picked 释放整笔预占对应，
        打包完成时按已拣量整笔扣已拣，未打包的差额退回待上架（按良品），之后走上架放回库位；
        否则差额会永久留在已拣里。差额只在两个分量之间搬动，total_stock 不变。
        """
        from warehouse.stock.service import Leg, StockOp
        if picked_quantity is None:
            picked_quantity = quantity
        if quantity > picked_quantity:
            raise BadRequestException(
                f"Packed quantity exceeds picked quantity for goods {goods_id}: "
                f"packed {quantity} > picked {picked_quantity}.",
                16043
            )
        own = op is None
        if own:
            op = StockOp(warehouse_id, 'pack')
        op.move(goods_id, quantity, src=Leg.picked(), dst=Leg.packed(), movement_type='pack',
                doc_line_id=doc_line_id)
        op.move(goods_id, picked_quantity - quantity, src=Leg.picked(), dst=Leg.pending(),
                movement_type='pack_short_return', doc_line_id=doc_line_id)
        if own:
            op.commit()

    @staticmethod
    @transactional
    def dn_cancelled_after_picking(goods_id: int, warehouse_id: int, picked_quantity: int = 0,
                                   packed_quantity: int = 0, *, op=None, doc_line_id: int | None = None):
        """
        取消已拣货 / 已打包（未发货）的 DN：货退回待上架（按良品），之后走上架放回库位（dn_cancel_return）
        :param picked_quantity: 还在已拣里的数量（DN 为 picked：该商品的已拣量）
        :param packed_quantity: 还在已打包里的数量（DN 为 packed：该商品的已打包量；
                                少打包的差额在打包完成时已退回待上架）
        只在几个分量之间搬动，total_stock 不变；dn_stock 由调用方在 DN 关闭后重聚合
        """
        from warehouse.stock.service import Leg, StockOp
        own = op is None
        if own:
            op = StockOp(warehouse_id, 'dn_cancel_return')
        op.move(goods_id, picked_quantity or 0, src=Leg.picked(), dst=Leg.pending(), doc_line_id=doc_line_id)
        op.move(goods_id, packed_quantity or 0, src=Leg.packed(), dst=Leg.pending(), doc_line_id=doc_line_id)
        if own:
            op.commit()

    @staticmethod
    @transactional
    def dn_delivered(goods_id: int, warehouse_id: int, quantity: int, *, op=None, doc_line_id: int | None = None):
        """
        发货完成：已打包 → 在途（ship）。在途不计入 total_stock
        """
        from warehouse.stock.service import Leg, StockOp
        own = op is None
        if own:
            op = StockOp(warehouse_id, 'ship')
        op.move(goods_id, quantity or 0, src=Leg.packed(), dst=Leg.in_transit(), doc_line_id=doc_line_id)
        if own:
            op.commit()

    @staticmethod
    @transactional
    def dn_completed(goods_id: int, warehouse_id: int, quantity: int, *, op=None, doc_line_id: int | None = None):
        """
        签收确认：在途 →（仓外）（sign）
        """
        from warehouse.stock.service import Leg, StockOp
        own = op is None
        if own:
            op = StockOp(warehouse_id, 'sign')
        op.move(goods_id, quantity or 0, src=Leg.in_transit(), doc_line_id=doc_line_id)
        if own:
            op.commit()

    @staticmethod
    @transactional
    def set_low_stock_threshold(goods_id: int, warehouse_id: int, threshold: int):
        """
        设置低库存阈值
        :param goods_id: 关联的商品 ID
        :param warehouse_id: 仓库 ID
        :param threshold: 新的低库存阈值
        """
        inventory = InventoryService._get_for_update(goods_id, warehouse_id)
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
        inventory = InventoryService._get_for_update(goods_id, warehouse_id)
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
    def update_and_calculate_asn_stock(goods_id: int, warehouse_id: int):
        """
        更新 ASN 库存并计算库存状态
        :param goods_id: 关联的商品 ID
        :param warehouse_id: 仓库 ID
        """
        inventory = InventoryService._get_for_update(goods_id, warehouse_id)

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
        inventory = InventoryService._get_for_update(goods_id, warehouse_id)

        from warehouse.dn.models import DN,DNDetail

        # 计算 DN 预扣库存（dn_stock）
        # dn_stock 代表「已被 DN 占用、但尚未实际拣出」的预扣量，必须涵盖所有仍持有
        # 预扣的 DN，而不能只统计 pending：
        #   - pending / in_progress：尚未拣货，按计划量(quantity)全额预扣
        #   - picked / packed / delivered / completed：拣货已结束，实际拣出的数量已转入
        #     picked_stock，短拣差额必须释放，不再继续预扣
        #   - closed 及其他：预扣已释放，计 0
        # 若此处只数 pending，DN 一旦进入 in_progress 就会在重聚合时被清掉预扣，
        # 导致后续 picking_dn → dn_picked 时 dn_stock < picked_quantity 误报 15008。
        reserved_expr = case(
            (DN.status.in_(['pending', 'in_progress']), DNDetail.quantity),
            else_=0
        )
        total_quantity = db.session.query(func.coalesce(func.sum(reserved_expr), 0)).\
            join(DN, DNDetail.dn_id == DN.id).\
            filter(
                DNDetail.goods_id == goods_id,
                DN.warehouse_id == warehouse_id,
                DN.is_active == True,
            ).scalar()

        inventory.dn_stock = total_quantity

        db.session.add(inventory)
        db.session.flush()
        # db.session.commit()
