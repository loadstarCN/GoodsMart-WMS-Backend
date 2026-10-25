from extensions import db
from extensions.db import UTCDateTime

class Goods(db.Model):
    """商品主表（支持多公司多仓库管理）"""
    __tablename__ = 'goods'

    __table_args__ = (
        db.UniqueConstraint('code', 'company_id', name='uix_code_company'),
        db.Index('idx_goods_code', 'code'),          # 商品编码快速检索
        db.Index('idx_goods_company', 'company_id'),  # 公司查询加速
        db.Index('idx_goods_name', 'name'),          # 商品名称模糊查询
        db.Index('idx_goods_tags', 'tags'),          # 标签维度分析
        db.Index('idx_goods_category', 'category'),  # 分类查询加速
        db.Index('idx_goods_brand', 'brand'),        # 品牌维度分析
        db.Index('idx_goods_manufacturer', 'manufacturer'),  # 厂商模糊查询
        db.Index('idx_price_range', 'price'),       # 价格区间查询
        db.CheckConstraint("price >= 0", name='chk_positive_price'),
        db.CheckConstraint("discount_price <= price", name='chk_discount_price'),        
    )

    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(
        db.Integer,
        db.ForeignKey('companies.id', ondelete='RESTRICT'),
        nullable=False,
        index=True,
        info={'description': '关联公司ID（禁止删除有商品的公司）'}
    )
    code = db.Column(
        db.String(50),
        nullable=False,
        info={'description': '商品编码（公司内唯一）'}
    )
    name = db.Column(
        db.String(100),
        nullable=False,
        index=True,
        info={'description': '商品名称（支持模糊查询）'}
    )
    description = db.Column(
        db.Text,  # 扩展描述长度
        nullable=True,
        info={'description': '商品详细描述'}
    )
    unit = db.Column(
        db.String(20),
        nullable=False,
        default='pcs',
        info={'description': '计量单位（默认：件）'}
    )
    # 尺寸优化为毫米单位
    weight = db.Column(
        db.Numeric(8, 3),  # 最大99999.999千克
        nullable=True,
        info={'description': '重量（千克，三位小数）'}
    )
    length = db.Column(
        db.Integer,
        nullable=True,
        info={'description': '长度（毫米）'}
    )
    width = db.Column(
        db.Integer,
        nullable=True,
        info={'description': '宽度（毫米）'}
    )
    height = db.Column(
        db.Integer,
        nullable=True,
        info={'description': '高度（毫米）'}
    )
    # 原产国：入库称重时看包装「MADE IN」录入；不设默认值，未录入即为空
    origin_country = db.Column(
        db.String(2),
        nullable=True,
        index=True,
        info={'description': '原产国（ISO 3166-1 alpha-2，大写；空=未录入）'}
    )
    manufacturer = db.Column(
        db.String(255),
        nullable=True,
        info={'description': '生产厂商全称'}
    )
    brand = db.Column(
        db.String(255),
        nullable=True,
        index=True,
        info={'description': '品牌名称（支持品牌维度分析）'}
    )
    image_url = db.Column(
        db.String(512),  # 扩展URL长度
        nullable=True,
        info={'description': '高清图片URL'}
    )
    thumbnail_url = db.Column(
        db.String(512),
        nullable=True,
        info={'description': '缩略图URL（尺寸建议：300x300）'}
    )
    category = db.Column(
        db.String(100),
        nullable=True,
        info={'description': '商品分类（三级分类示例：家电/厨房电器/电饭煲）'}
    )
    tags = db.Column(
        db.String(255), 
        nullable=True,
        info={'description': '商品标签（示例：["促销","新品"]）'}
    )
    price = db.Column(
        db.Numeric(12, 2),  # 精确金额
        nullable=True,
        info={'description': '标准售价（含两位小数）'}
    )
    discount_price = db.Column(
        db.Numeric(12, 2),
        nullable=True,
        info={'description': '促销价格（需≤标准售价）'}
    )
    currency = db.Column(
        db.String(10),
        nullable=False,
        default='JPY',
        info={'description': '货币代码（ISO 4217标准）'}
    )
    expiration_date = db.Column(
        db.Date,
        nullable=True,
        info={'description': '保质期截止日（仅限食品/药品）'}
    )
    production_date = db.Column(
        db.Date,
        nullable=True,
        info={'description': '生产日期（ISO 8601格式）'}
    )
    extra_data = db.Column(
        db.JSON,  # 使用JSONB类型（PostgreSQL）
        nullable=True,
        info={'description': '扩展数据（示例：{"color":"red","material":"metal"}）'}
    )
    is_active = db.Column(
        db.Boolean,
        default=True,
        index=True,
        info={'description': '是否上架'}
    )
    created_by = db.Column(
        db.Integer,
        db.ForeignKey('users.id', ondelete='SET NULL'),
        nullable=True,  # 与 ondelete='SET NULL' 一致：创建人被删除后置空
        info={'description': '创建人ID'}
    )
    created_at = db.Column(
        UTCDateTime,
        default=db.func.now(),
        info={'description': '创建时间'}
    )
    updated_at = db.Column(
        UTCDateTime,
        default=db.func.now(),
        onupdate=db.func.now(),
        index=True,
        info={'description': '最后更新时间'}
    )

    # 关系加载策略
    company = db.relationship(
        'Company',
        backref=db.backref('goods', lazy='dynamic'),
        lazy='joined',
        info={'description': '关联公司对象'}
    )
    storage_records = db.relationship(
        'GoodsLocation',
        backref='goods',
        lazy='dynamic',
        cascade='all, delete-orphan',
        info={'description': '库位存储记录'}
    )
    creator = db.relationship(
        'User',
        backref=db.backref('goods_created', lazy='dynamic'),
        lazy='joined',
        info={'description': '创建人对象'}
    )
    inventories = db.relationship(
        'Inventory',
        back_populates='goods',
        lazy='select',
        info={'description': '库存变动记录'}
    )

class GoodsLocation(db.Model):
    """商品库位存储表"""
    __tablename__ = 'goods_locations'

    # 唯一键 (goods_id, location_id, 批次)：无批次（lot_id 为空）按 0 算，同一商品同一库位仍只有一行无批次库存。
    # 用函数唯一索引而不是 NULLS NOT DISTINCT（SQLite 不支持）。数量只能由 StockService 改（warehouse/stock/guard.py）
    __table_args__ = (
        db.Index('uix_goods_location_lot', 'goods_id', 'location_id', db.text('coalesce(lot_id, 0)'), unique=True),
        db.Index('idx_goods_location_goods', 'goods_id'),
        db.Index('idx_goods_location_location', 'location_id'),
        db.Index('idx_goods_location_lot', 'lot_id'),
        db.CheckConstraint("quantity >= 0", name='chk_non_negative_quantity'),
    )

    id = db.Column(db.Integer, primary_key=True)
    goods_id = db.Column(
        db.Integer,
        db.ForeignKey('goods.id', ondelete='RESTRICT'),
        nullable=False,
        index=True,
        info={'description': '关联商品ID（禁止删除有库存的商品）'}
    )
    location_id = db.Column(
        db.Integer,
        db.ForeignKey('locations.id', ondelete='RESTRICT'),
        nullable=False,
        index=True,
        info={'description': '关联库位ID（禁止删除有库存的库位）'}
    )
    lot_id = db.Column(
        db.Integer,
        db.ForeignKey('goods_lots.id', ondelete='RESTRICT', name='fk_goods_locations_lot_id'),
        nullable=True,
        info={'description': '批次（为空 = 无批次；一期一律为空，二期启用）'}
    )
    quantity = db.Column(
        db.Integer,
        nullable=False,
        default=0,
        info={'description': '当前库存数量'}
    )
    created_at = db.Column(
        UTCDateTime,
        default=db.func.now(),
        info={'description': '入库时间'}
    )
    updated_at = db.Column(
        UTCDateTime,
        default=db.func.now(),
        onupdate=db.func.now(),
        info={'description': '最后更新时间'}
    )

    # 关系加载策略
    location = db.relationship(
        'Location',
        backref=db.backref('stored_goods', lazy='dynamic'),
        lazy='joined',
        info={'description': '关联库位对象'}
    )


def _user_ref(user_id):
    """操作人：{id, user_name}（用户已删除时为 None）"""
    if user_id is None:
        return None
    from system.user.models import User
    user = db.session.get(User, user_id)
    return {'id': user.id, 'user_name': user.user_name} if user else None


def _iso(moment):
    from extensions.db import as_utc
    return as_utc(moment).isoformat() if moment is not None else None


class GoodsImport(db.Model):
    """商品导入记录（POST /goods/bulk_upload 真正执行的每一次；dry_run 校验不记）。

    原文件与错误行文件（原格式 + 末列「错误」）存库，保留 30 天（files_expire_at），到期由定时任务清掉文件内容，记录本身保留。
    这次新增 / 更新的商品记在 goods_import_items（商品列表按 import_id 筛选）。
    """
    __tablename__ = 'goods_imports'

    MODES = ('skip', 'append', 'override', 'active')
    STATUSES = ('completed', 'partial', 'failed')

    __table_args__ = (
        db.Index('idx_goods_imports_company_created', 'company_id', 'created_at'),
        db.Index('idx_goods_imports_files_expire', 'files_expire_at'),
        db.CheckConstraint("status IN ('completed','partial','failed')", name='chk_goods_imports_status'),
    )

    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(db.Integer, db.ForeignKey('companies.id', ondelete='CASCADE'), nullable=False,
                           info={'description': '公司ID'})
    file_name = db.Column(db.String(255), nullable=True, info={'description': '上传的文件名'})
    file_format = db.Column(db.String(10), nullable=True, info={'description': 'csv / xlsx'})
    encoding = db.Column(db.String(20), nullable=True,
                         info={'description': 'CSV 的实际编码 utf-8 / utf-8-sig / cp932（Excel 为空）'})
    encoding_detected = db.Column(db.Boolean, nullable=True, info={'description': '编码是否自动识别'})
    sheet_name = db.Column(db.String(100), nullable=True, info={'description': 'Excel 读的工作表'})
    mode = db.Column(db.String(10), nullable=False,
                     info={'description': '编码已存在时：skip / append / override / active'})
    status = db.Column(db.String(10), nullable=False,
                       info={'description': 'completed（无错误）/ partial（有错误行未导入）/ failed（什么都没导入）'})
    row_count = db.Column(db.Integer, nullable=False, default=0, info={'description': '数据行数'})
    created_count = db.Column(db.Integer, nullable=False, default=0)
    updated_count = db.Column(db.Integer, nullable=False, default=0)
    unchanged_count = db.Column(db.Integer, nullable=False, default=0)
    skipped_count = db.Column(db.Integer, nullable=False, default=0)
    error_count = db.Column(db.Integer, nullable=False, default=0)
    columns = db.Column(db.JSON, nullable=True, info={'description': '认出的列（列键，文件里的顺序）'})
    ignored_columns = db.Column(db.JSON, nullable=True, info={'description': '没认出、导入时忽略的表头'})
    failure = db.Column(db.JSON, nullable=True,
                        info={'description': '整份文件不能用 / 有错误时的第一个错误 {code, message, details}'})
    errors = db.deferred(db.Column(db.JSON, nullable=True,
                                   info={'description': '错误行（前 200 行）[{row, code, errors: [...]}]'}))
    original_file = db.deferred(db.Column(db.LargeBinary, nullable=True, info={'description': '原文件'}))
    original_size = db.Column(db.Integer, nullable=True)
    error_file = db.deferred(db.Column(db.LargeBinary, nullable=True,
                                       info={'description': '错误行文件（原格式 + 末列「错误」）'}))
    error_file_name = db.Column(db.String(255), nullable=True)
    files_expire_at = db.Column(UTCDateTime, nullable=True, info={'description': '原文件 / 错误行文件的删除时刻'})
    files_purged_at = db.Column(UTCDateTime, nullable=True, info={'description': '文件已删除的时刻'})
    created_by = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='SET NULL'), nullable=True)
    created_at = db.Column(UTCDateTime, default=db.func.now())

    def to_dict(self) -> dict:
        files_available = self.files_purged_at is None
        return {
            'id': self.id,
            'company_id': self.company_id,
            'file_name': self.file_name,
            'file_format': self.file_format,
            'encoding': self.encoding,
            'encoding_detected': self.encoding_detected,
            'sheet_name': self.sheet_name,
            'mode': self.mode,
            'status': self.status,
            'row_count': self.row_count,
            'created': self.created_count,
            'updated': self.updated_count,
            'unchanged': self.unchanged_count,
            'skipped': self.skipped_count,
            'errors': self.error_count,
            'columns': self.columns or [],
            'ignored_columns': self.ignored_columns or [],
            'failure': self.failure,
            'has_original_file': files_available and self.original_size is not None,
            'has_error_file': files_available and self.error_file_name is not None,
            'error_file_name': self.error_file_name,
            'files_expire_at': _iso(self.files_expire_at),
            'files_purged_at': _iso(self.files_purged_at),
            'created_by': self.created_by,
            'created_by_user': _user_ref(self.created_by),
            'created_at': _iso(self.created_at),
        }


class GoodsImportItem(db.Model):
    """一次导入新增 / 更新了哪些商品（商品列表 ?import_id= 筛选）。商品删除时一并删除"""
    __tablename__ = 'goods_import_items'

    __table_args__ = (
        db.Index('idx_goods_import_items_goods', 'goods_id'),
        db.CheckConstraint("action IN ('create','update')", name='chk_goods_import_items_action'),
    )

    import_id = db.Column(db.Integer, db.ForeignKey('goods_imports.id', ondelete='CASCADE'), primary_key=True)
    goods_id = db.Column(db.Integer, db.ForeignKey('goods.id', ondelete='CASCADE'), primary_key=True)
    action = db.Column(db.String(10), nullable=False, info={'description': 'create / update'})


class GoodsExport(db.Model):
    """商品导出任务（POST /goods/exports）。≤ 1,000 件在请求里同步生成，更多的在后台生成（进度 processed / total）。

    文件存库（content），生成后保留 7 天（expires_at），到期由定时任务删内容、状态改 expired；记录保留，可按原条件重新导出。
    filters / columns / lang / file_format / encoding 是导出条件；scope_warehouse_ids 是库存列与「有 / 无库存」筛选
    用的仓库范围（建任务时按调用方解析好，后台生成时没有请求上下文）。
    """
    __tablename__ = 'goods_exports'

    STATUSES = ('pending', 'running', 'completed', 'failed', 'expired')

    __table_args__ = (
        db.Index('idx_goods_exports_company_created', 'company_id', 'created_at'),
        db.Index('idx_goods_exports_status', 'status', 'created_at'),
        db.CheckConstraint("status IN ('pending','running','completed','failed','expired')",
                           name='chk_goods_exports_status'),
    )

    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(db.Integer, db.ForeignKey('companies.id', ondelete='CASCADE'), nullable=True,
                           info={'description': '公司ID（平台管理员不限公司时为空）'})
    warehouse_id = db.Column(db.Integer, db.ForeignKey('warehouses.id', ondelete='SET NULL'), nullable=True,
                             info={'description': '库存范围的仓库（为空 = 调用方可访问的全部仓库）'})
    scope_warehouse_ids = db.Column(db.JSON, nullable=True,
                                    info={'description': '库存范围的仓库 id 列表（为空 = 不限）'})
    uses_stock = db.Column(db.Boolean, nullable=False, default=False,
                           info={'description': '有库存列或「有 / 无库存」筛选（限仓库的员工只看自己仓库的）'})
    filters = db.Column(db.JSON, nullable=True, info={'description': '筛选条件（同商品列表的参数）'})
    columns = db.Column(db.JSON, nullable=False, info={'description': '列键（顺序 = 文件里的列序）'})
    preset = db.Column(db.String(20), nullable=False, default='custom',
                       info={'description': 'import_template / all / custom'})
    lang = db.Column(db.String(10), nullable=False, info={'description': '表头语言 zh-CN / en-US / ja-JP'})
    file_format = db.Column(db.String(10), nullable=False, info={'description': 'csv / xlsx'})
    encoding = db.Column(db.String(20), nullable=True, info={'description': 'CSV：utf-8-sig / cp932；Excel 为空'})
    status = db.Column(db.String(10), nullable=False, default='pending')
    background = db.Column(db.Boolean, nullable=False, default=False, info={'description': '是否后台生成'})
    total = db.Column(db.Integer, nullable=False, default=0, info={'description': '建任务时符合条件的件数'})
    processed = db.Column(db.Integer, nullable=False, default=0, info={'description': '已写入的件数（进度）'})
    row_count = db.Column(db.Integer, nullable=True, info={'description': '文件里的商品行数'})
    unencodable_count = db.Column(db.Integer, nullable=True,
                                  info={'description': 'Shift_JIS 写不下、有字符变成「?」的商品件数'})
    file_name = db.Column(db.String(150), nullable=True)
    file_size = db.Column(db.Integer, nullable=True)
    content = db.deferred(db.Column(db.LargeBinary, nullable=True, info={'description': '文件内容'}))
    error = db.Column(db.String(500), nullable=True, info={'description': '生成失败的原因'})
    attempts = db.Column(db.Integer, nullable=False, default=0, info={'description': '已生成的次数（含重试）'})
    source_export_id = db.Column(db.Integer, db.ForeignKey('goods_exports.id', ondelete='SET NULL'), nullable=True,
                                 info={'description': '按哪条记录的条件重新导出的'})
    created_by = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='SET NULL'), nullable=True)
    created_at = db.Column(UTCDateTime, default=db.func.now())
    started_at = db.Column(UTCDateTime, nullable=True)
    heartbeat_at = db.Column(UTCDateTime, nullable=True, info={'description': '后台生成最近一次报进度的时刻'})
    finished_at = db.Column(UTCDateTime, nullable=True)
    expires_at = db.Column(UTCDateTime, nullable=True, info={'description': '文件删除的时刻'})

    def to_dict(self) -> dict:
        total = self.total or 0
        progress = 100 if self.status == 'completed' else (
            min(99, int((self.processed or 0) * 100 / total)) if total else 0)
        return {
            'id': self.id,
            'company_id': self.company_id,
            'warehouse_id': self.warehouse_id,
            'status': self.status,
            'background': self.background,
            'filters': self.filters or {},
            'columns': self.columns or [],
            'column_count': len(self.columns or []),
            'preset': self.preset,
            'lang': self.lang,
            'file_format': self.file_format,
            'encoding': self.encoding,
            'total': total,
            'processed': self.processed or 0,
            'progress': progress,
            'row_count': self.row_count,
            'unencodable_count': self.unencodable_count,
            'file_name': self.file_name,
            'file_size': self.file_size,
            'downloadable': self.status == 'completed',
            'error': self.error,
            'attempts': self.attempts,
            'source_export_id': self.source_export_id,
            'created_by': self.created_by,
            'created_by_user': _user_ref(self.created_by),
            'created_at': _iso(self.created_at),
            'started_at': _iso(self.started_at),
            'finished_at': _iso(self.finished_at),
            'expires_at': _iso(self.expires_at),
        }