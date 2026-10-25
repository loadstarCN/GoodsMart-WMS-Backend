"""库存底座（一期）的表。设计与口径见 doc/库存数值系统说明.md 第九、十节。

- GoodsLot：生产批次主数据（批号）。一期只建表，库存行与流水的 lot_id 一律为空，二期启用
- InventoryLotStock：仓内、但不在库位上的库存（待上架良品 / 破损、已拣、已打包、已发未签），仓库 × 商品 × 批次
- StockLedger：库存流水，每笔实物变动按「桶」记一行（复式：来源桶一行、去向桶一行），只增不改
- StockDailyBalance：日终结存（仓库当地日期 24:00），按桶存非零行
- InventoryMetricsDaily：库存分析的夜间汇总（库龄、近 N 天出库、周转、呆滞）

桶 = 仓库 × 商品 × 批次 × 库位（只有库位类别有）× 库存类别（stock_type）。
Inventory（商品 × 仓库）上的实物字段从此是这些行表的缓存，只能由 StockService（warehouse/stock/service.py）写。
"""
from extensions import db
from extensions.db import UTCDateTime

# 库存类别（流水 / 日终结存的 stock_type）
STOCK_ONHAND = 'onhand'                  # 标准库位
STOCK_DAMAGED = 'damaged'                # 破损库位
STOCK_RETURN = 'return'                  # 退货库位
STOCK_PENDING = 'pending'                # 待上架·良品（sorted_stock − sorted_damage_stock）
STOCK_PENDING_DAMAGED = 'pending_damaged'  # 待上架·破损（sorted_damage_stock）
STOCK_PICKED = 'picked'                  # 已拣未打包
STOCK_PACKED = 'packed'                  # 已打包未发货
STOCK_IN_TRANSIT = 'in_transit'          # 已发货未签收（不计入仓库 total_stock）
STOCK_TYPES = (STOCK_ONHAND, STOCK_DAMAGED, STOCK_RETURN, STOCK_PENDING, STOCK_PENDING_DAMAGED,
               STOCK_PICKED, STOCK_PACKED, STOCK_IN_TRANSIT)
# 库位类型 → 库存类别
LOCATION_STOCK_TYPES = {'standard': STOCK_ONHAND, 'damaged': STOCK_DAMAGED, 'return': STOCK_RETURN}
LOCATION_TYPES_OF_STOCK = {value: key for key, value in LOCATION_STOCK_TYPES.items()}
# 计入仓库 total_stock 的类别（仓内实物，不含在途）
IN_WAREHOUSE_STOCK_TYPES = (STOCK_ONHAND, STOCK_DAMAGED, STOCK_RETURN, STOCK_PENDING, STOCK_PENDING_DAMAGED,
                            STOCK_PICKED, STOCK_PACKED)

# 变动类型（movement_type）
MOVEMENT_TYPES = (
    'opening',            # 期初
    'reconcile',          # 系统校正（部署窗口内的差额、人工核实后的校正）
    'sort_in',            # 分拣完成（ASN 完成）：（外）→ 待上架
    'putaway',            # 上架：待上架 → 库位
    'removal',            # 下架（非拣货）：库位 → 待上架
    'transfer',           # 移库：库位 → 库位
    'pick',               # 拣货完成：库位 → 已拣
    'pack',               # 打包完成：已拣 → 已打包
    'pack_short_return',  # 少打包的差额：已拣 → 待上架
    'dn_cancel_return',   # 已拣 / 已打包的 DN 取消：已拣或已打包 → 待上架
    'ship',               # 发货：已打包 → 在途
    'sign',               # 签收（含自动视为签收）：在途 →（外）
    'adjust_gain',        # 调整单完成·盘盈：（外）→ 库位
    'adjust_loss',        # 调整单完成·盘亏：库位 →（外）
    'lot_relabel',        # 批次补录 / 改标（二期）
    'correction',         # 历史数据校正（已确认的旧 bug 造成的计数差额，reason 写 bug 编号，见 stock/legacy_fixes.py）
)
# 变动来源
LEDGER_SOURCES = ('web', 'pda', 'api', 'system')
# 日终结存的来源：由流水算出 / 历史回放 / 由旧快照转换（只有库位库存）
DAILY_SOURCES = ('ledger', 'derived', 'legacy_snapshot')


def _bigint_pk():
    """自增 bigint 主键（SQLite 只有 INTEGER PRIMARY KEY 才自增）"""
    return db.Column(db.BigInteger().with_variant(db.Integer, 'sqlite'), primary_key=True)


class GoodsLot(db.Model):
    """生产批次（批号）主数据。唯一键 (goods_id, lot_no)；商品属于公司，等价于「公司 × 商品 × 批号」。
    一期只建表（库存与流水的 lot_id 全为空），二期由 LotService 维护。"""
    __tablename__ = 'goods_lots'

    __table_args__ = (
        db.UniqueConstraint('goods_id', 'lot_no', name='uq_goods_lots_goods_lot_no'),
        db.Index('idx_goods_lots_goods_expiry', 'goods_id', 'expiry_date'),
        db.Index('idx_goods_lots_expiry', 'expiry_date'),
        db.CheckConstraint('expiry_date IS NULL OR production_date IS NULL OR expiry_date >= production_date',
                           name='chk_goods_lots_dates'),
    )

    id = db.Column(db.Integer, primary_key=True)
    goods_id = db.Column(db.Integer, db.ForeignKey('goods.id', ondelete='RESTRICT'), nullable=False,
                         info={'description': '商品（公司由商品确定）'})
    lot_no = db.Column(db.String(40), nullable=False,
                       info={'description': '批号（规范化后：去首尾空白、全角转半角、转大写）'})
    production_date = db.Column(db.Date, nullable=True, info={'description': '生产日期'})
    expiry_date = db.Column(db.Date, nullable=True, info={'description': '有效期至（当天仍有效）'})
    status = db.Column(db.String(10), nullable=False, default='active', server_default='active',
                       info={'description': 'active / hold（冻结、召回，预留）'})
    source = db.Column(db.String(20), nullable=True,
                       info={'description': 'sorting / adjustment / relabel / cyclecount / api / manual'})
    first_inbound_at = db.Column(UTCDateTime, nullable=True, info={'description': '第一次入库记账的时刻（库龄用）'})
    remark = db.Column(db.String(255), nullable=True)
    created_by = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='SET NULL'), nullable=True)
    created_at = db.Column(UTCDateTime, default=db.func.now())
    updated_at = db.Column(UTCDateTime, default=db.func.now(), onupdate=db.func.now())


class InventoryLotStock(db.Model):
    """仓内、但不在库位上的库存（仓库 × 商品 × 批次；lot_id 为空 = 无批次）。各列全为 0 时删行。
    Inventory 上的同名字段是本表对应列的合计（缓存）。只能由 StockService 写（stock/guard.py 守卫）。"""
    __tablename__ = 'inventory_lot_stock'

    __table_args__ = (
        db.Index('uix_inventory_lot_stock', 'warehouse_id', 'goods_id', db.text('coalesce(lot_id, 0)'), unique=True),
        db.Index('idx_inventory_lot_stock_goods', 'goods_id'),
        db.Index('idx_inventory_lot_stock_lot', 'lot_id'),
        db.CheckConstraint('sorted_stock >= 0 AND sorted_damage_stock >= 0 AND picked_stock >= 0 '
                           'AND packed_stock >= 0 AND delivered_stock >= 0', name='chk_inventory_lot_stock_non_negative'),
        db.CheckConstraint('sorted_damage_stock <= sorted_stock', name='chk_inventory_lot_stock_damaged'),
    )

    id = db.Column(db.Integer, primary_key=True)
    warehouse_id = db.Column(db.Integer, db.ForeignKey('warehouses.id', ondelete='RESTRICT'), nullable=False)
    goods_id = db.Column(db.Integer, db.ForeignKey('goods.id', ondelete='RESTRICT'), nullable=False)
    lot_id = db.Column(db.Integer, db.ForeignKey('goods_lots.id', ondelete='RESTRICT'), nullable=True,
                       info={'description': '批次（为空 = 无批次；一期一律为空）'})
    sorted_stock = db.Column(db.Integer, nullable=False, default=0, server_default='0',
                             info={'description': '待上架合计（含其中的破损）'})
    sorted_damage_stock = db.Column(db.Integer, nullable=False, default=0, server_default='0',
                                    info={'description': '待上架里的破损（0 ≤ 破损 ≤ 待上架合计）'})
    picked_stock = db.Column(db.Integer, nullable=False, default=0, server_default='0', info={'description': '已拣'})
    packed_stock = db.Column(db.Integer, nullable=False, default=0, server_default='0', info={'description': '已打包'})
    delivered_stock = db.Column(db.Integer, nullable=False, default=0, server_default='0',
                                info={'description': '已发货未签收（在途）'})
    updated_at = db.Column(UTCDateTime, default=db.func.now(), onupdate=db.func.now())

    STOCK_COLUMNS = ('sorted_stock', 'sorted_damage_stock', 'picked_stock', 'packed_stock', 'delivered_stock')

    def is_empty(self) -> bool:
        return all(not (getattr(self, column) or 0) for column in self.STOCK_COLUMNS)


class StockLedger(db.Model):
    """库存流水：每笔实物变动按桶记一行（来源桶 −N、去向桶 +N），只增不改，纠错一律写 reconcile 行。
    balance_after = 本行之后同一桶的结存（按 (posted_at, id) 衔接）；goods_total_after = 本笔变动之后该商品在该仓库的
    total_stock（仓内实物，不含在途）。"""
    __tablename__ = 'stock_ledger'

    __table_args__ = (
        db.Index('idx_stock_ledger_warehouse_posted', 'warehouse_id', 'posted_at'),
        db.Index('idx_stock_ledger_goods_posted', 'warehouse_id', 'goods_id', 'posted_at'),
        db.Index('idx_stock_ledger_lot_posted', 'lot_id', 'posted_at'),
        db.Index('idx_stock_ledger_location_posted', 'location_id', 'posted_at'),
        db.Index('idx_stock_ledger_doc', 'doc_type', 'doc_id'),
        db.Index('idx_stock_ledger_trace', 'trace_id'),
        db.Index('idx_stock_ledger_company_posted', 'company_id', 'posted_at'),
        db.CheckConstraint('quantity <> 0', name='chk_stock_ledger_quantity'),
    )

    id = _bigint_pk()
    posted_at = db.Column(UTCDateTime, nullable=False, default=db.func.now(),
                          info={'description': '记账时刻（日终库存按它算，已过去的日子不会再变）'})
    occurred_at = db.Column(UTCDateTime, nullable=False,
                            info={'description': '业务发生时刻（如发货取承运商揽收时刻，可能早于记账时刻）'})
    company_id = db.Column(db.Integer, nullable=False, info={'description': '冗余，范围过滤用'})
    warehouse_id = db.Column(db.Integer, db.ForeignKey('warehouses.id', ondelete='RESTRICT'), nullable=False)
    goods_id = db.Column(db.Integer, db.ForeignKey('goods.id', ondelete='RESTRICT'), nullable=False)
    lot_id = db.Column(db.Integer, db.ForeignKey('goods_lots.id', ondelete='RESTRICT'), nullable=True)
    location_id = db.Column(db.Integer, db.ForeignKey('locations.id', ondelete='RESTRICT'), nullable=True,
                            info={'description': '库位（只有库位类别 onhand / damaged / return 才有）'})
    stock_type = db.Column(db.String(16), nullable=False, info={'description': '库存类别（STOCK_TYPES）'})
    quantity = db.Column(db.Integer, nullable=False, info={'description': '带符号的变动数'})
    balance_after = db.Column(db.Integer, nullable=False, info={'description': '本行之后同一桶的结存'})
    goods_total_after = db.Column(db.Integer, nullable=False,
                                  info={'description': '本笔变动之后该商品在该仓库的 total_stock'})
    counter_stock_type = db.Column(db.String(16), nullable=True,
                                   info={'description': '同一笔变动另一侧的库存类别（为空 = 仓外：入库 / 出库 / 校正）'})
    movement_type = db.Column(db.String(24), nullable=False, info={'description': '变动类型（MOVEMENT_TYPES）'})
    doc_type = db.Column(db.String(20), nullable=True, info={'description': '来源单据类型'})
    doc_id = db.Column(db.Integer, nullable=True)
    doc_no = db.Column(db.String(100), nullable=True)
    doc_line_id = db.Column(db.Integer, nullable=True)
    reason = db.Column(db.String(30), nullable=True, info={'description': '下架原因、调整原因码等'})
    operator_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='SET NULL'), nullable=True)
    api_key_id = db.Column(db.Integer, db.ForeignKey('api_keys.id', ondelete='SET NULL'), nullable=True)
    source = db.Column(db.String(10), nullable=True, info={'description': 'web / pda / api / system'})
    trace_id = db.Column(db.String(64), nullable=False, info={'description': '同一事务的所有行共用'})
    remark = db.Column(db.String(255), nullable=True)

    goods = db.relationship('Goods', lazy='joined')
    location = db.relationship('Location', lazy='joined')
    operator = db.relationship('User', lazy='joined', foreign_keys=[operator_id])


class StockDailyBalance(db.Model):
    """日终结存：仓库当地日期 balance_date 的 24:00，按桶只存非零行"""
    __tablename__ = 'stock_daily_balances'

    __table_args__ = (
        db.Index('uix_stock_daily_balance', 'balance_date', 'warehouse_id', 'goods_id', db.text('coalesce(lot_id, 0)'),
                 db.text('coalesce(location_id, 0)'), 'stock_type', unique=True),
        db.Index('idx_stock_daily_balance_warehouse_date', 'warehouse_id', 'balance_date'),
        db.Index('idx_stock_daily_balance_goods_date', 'goods_id', 'balance_date'),
        db.CheckConstraint('quantity > 0', name='chk_stock_daily_balance_quantity'),
    )

    id = _bigint_pk()
    balance_date = db.Column(db.Date, nullable=False, info={'description': '仓库当地日期（当日 24:00 的结存）'})
    warehouse_id = db.Column(db.Integer, db.ForeignKey('warehouses.id', ondelete='RESTRICT'), nullable=False)
    goods_id = db.Column(db.Integer, db.ForeignKey('goods.id', ondelete='RESTRICT'), nullable=False)
    lot_id = db.Column(db.Integer, db.ForeignKey('goods_lots.id', ondelete='RESTRICT'), nullable=True)
    location_id = db.Column(db.Integer, db.ForeignKey('locations.id', ondelete='RESTRICT'), nullable=True)
    stock_type = db.Column(db.String(16), nullable=False)
    quantity = db.Column(db.Integer, nullable=False)
    source = db.Column(db.String(16), nullable=False, default='ledger',
                       info={'description': 'ledger（由流水算出）/ derived（历史回放）/ legacy_snapshot（旧快照，只有库位库存）'})


class InventoryMetricsDaily(db.Model):
    """库存分析的夜间汇总（截至 metric_date 当地 24:00）：库龄、近 N 天出库、周转、呆滞。保留约 400 天"""
    __tablename__ = 'inventory_metrics_daily'

    __table_args__ = (
        db.Index('idx_inventory_metrics_warehouse_date', 'warehouse_id', 'metric_date'),
    )

    metric_date = db.Column(db.Date, primary_key=True)
    warehouse_id = db.Column(db.Integer, db.ForeignKey('warehouses.id', ondelete='RESTRICT'), primary_key=True)
    goods_id = db.Column(db.Integer, db.ForeignKey('goods.id', ondelete='RESTRICT'), primary_key=True)
    total_stock = db.Column(db.Integer, nullable=False, default=0, info={'description': '日终仓内实物合计'})
    available_stock_for_sale = db.Column(db.Integer, nullable=True, info={'description': '汇总时的可售库存'})
    out_qty_7d = db.Column(db.Integer, nullable=False, default=0)
    out_qty_30d = db.Column(db.Integer, nullable=False, default=0)
    out_qty_90d = db.Column(db.Integer, nullable=False, default=0)
    sales_qty_30d = db.Column(db.Integer, nullable=False, default=0,
                              info={'description': '近 30 天出库里 dn_type = shipping 的部分'})
    last_in_at = db.Column(UTCDateTime, nullable=True)
    last_out_at = db.Column(UTCDateTime, nullable=True)
    avg_stock_30d = db.Column(db.Float, nullable=True, info={'description': '近 30 天日均库存（日终 total）'})
    turnover_days_30d = db.Column(db.Float, nullable=True, info={'description': '近 30 天日均库存 ÷ 日均出库；没有出库为空'})
    days_of_supply = db.Column(db.Float, nullable=True, info={'description': '可售 ÷ 近 30 天日均出库；没有出库为空'})
    # 库龄档位（件数）：0–30 / 31–90 / 91–180 / 180+ 天（档位定义见 metrics.AGE_BUCKETS）
    age_0_30 = db.Column(db.Integer, nullable=False, default=0)
    age_31_90 = db.Column(db.Integer, nullable=False, default=0)
    age_91_180 = db.Column(db.Integer, nullable=False, default=0)
    age_over_180 = db.Column(db.Integer, nullable=False, default=0)
    age_unknown = db.Column(db.Integer, nullable=False, default=0, info={'description': '找不到入库记录的件数'})
    weighted_age_days = db.Column(db.Float, nullable=True)
    oldest_in_at = db.Column(UTCDateTime, nullable=True)
    out_qty_90d_rank = db.Column(db.Integer, nullable=True, info={'description': '近 90 天出库量在本仓库的名次（1 起）'})
    abc_class = db.Column(db.String(1), nullable=True,
                          info={'description': 'ABC 分档（近 90 天出库量累计占比 ≤ 70% A、≤ 90% B、其余 C；没有出库为空）'})
    idle_days = db.Column(db.Integer, nullable=True, info={'description': '距最后一次出库（没出过库按最早入库）的天数'})
    status = db.Column(db.String(8), nullable=False, default='normal',
                       info={'description': 'normal / dead（呆滞：近 90 天无出库且库龄 > 90 天）/ empty（没有库存）'})
    created_at = db.Column(UTCDateTime, default=db.func.now())

    goods = db.relationship('Goods', lazy='joined')
