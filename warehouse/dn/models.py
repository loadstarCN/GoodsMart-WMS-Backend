from extensions.db import *

class DN(db.Model):
    """发货单主表
    
    Attributes:
        warehouse_id: 关联仓库ID (外键约束不可删除)
        status: 单据状态 (pending/in_progress/picked/packed/delivered/completed/closed)
        dn_type: 单据类型 (shipping/return_to_supplier/damage_to_supplier/transfer)
    """
    __tablename__ = 'dn'
    
    __table_args__ = (
        db.Index('idx_dn_warehouse_status', 'warehouse_id', 'status'),  # 仓库维度查询
        db.Index('idx_dn_expected_date', 'expected_shipping_date'),    # 预计发货日期索引
        db.CheckConstraint("status IN ('pending','in_progress','picked','packed','delivered','completed','closed')", 
                          name='chk_valid_dn_status'),
        db.CheckConstraint("dn_type IN ('shipping','return_to_supplier','damage_to_supplier','transfer')", 
                          name='chk_valid_dn_dn_type'),
          
        
    )

    DN_TYPES = ('shipping', 'return_to_supplier', 'damage_to_supplier', 'transfer')
    DN_STATUSES = ('pending','in_progress', 'picked', 'packed', 'delivered', 'completed','closed')
    DN_TRANSPORTATION_MODES = ('express', 'pickup', 'courier', 'air', 'sea', 'land', 'rail', 'drone')

    id = db.Column(db.Integer, primary_key=True)
    warehouse_id = db.Column(
        db.Integer,
        db.ForeignKey('warehouses.id', ondelete='RESTRICT'),
        nullable=False,
        info={'description': '仓库ID'}
    )
    recipient_id = db.Column(
        db.Integer,
        db.ForeignKey('recipients.id', ondelete='RESTRICT'),
        nullable=False,
        info={'description': '收货人ID'}
    )
    shipping_address = db.Column(
        db.String(255),
        nullable=False,
        info={'description': '完整发货地址'}
    )
    expected_shipping_date = db.Column(
        db.Date,
        nullable=False,
        info={'description': '预计发货日期'}
    )
    dn_type = db.Column(
        db.Enum(*DN_TYPES, name='dn_type_enum'),
        nullable=False,
        default='shipping',
        info={'description': '发货单类型'}
    )
    order_number = db.Column(
        db.String(50),
        nullable=True,
        index=True,  # 客户订单号高频查询
        info={'description': '客户订单号'}
    )

    transportation_mode = db.Column(
        db.Enum(*DN_TRANSPORTATION_MODES, name='dn_transportation_mode_enum'),
        nullable=True,
        info={'description': '运输方式枚举'}
    )

    packaging_info = db.Column(
        db.String(255),
        nullable=True,
        info={'description': '包装信息'}
    )
    special_handling = db.Column(
        db.String(255),
        nullable=True,
        info={'description': '特殊处理信息'}
    )
    carrier_id = db.Column(
        db.Integer,
        db.ForeignKey('carriers.id', ondelete='RESTRICT'),
        nullable=True,
        info={'description': '承运商ID'}
    )
    status = db.Column(
        db.Enum(*DN_STATUSES, name='dn_status_enum'),
        nullable=False,
        default='pending',
        info={'description': 'DN单据状态'}
    )
    remark = db.Column(
        db.String(255),
        nullable=True,
        info={'description': '备注信息'}
    )
    is_active = db.Column(
        db.Boolean,
        default=True,
        index=True,  # 激活状态高频过滤
        info={'description': '是否有效'}
    )
    created_by = db.Column(
        db.Integer,
        db.ForeignKey('users.id', ondelete='RESTRICT'),
        nullable=False,
        info={'description': '创建人ID'}
    )
    api_key_id = db.Column(
        db.Integer,
        db.ForeignKey('api_keys.id', ondelete='SET NULL'),
        nullable=True,
        index=True,
        info={'description': '创建来源 API Key ID（用于定向 Webhook 推送）'}
    )
    created_at = db.Column(
        db.DateTime,
        default=db.func.now(),
        info={'description': '创建时间'}
    )
    updated_at = db.Column(
        db.DateTime,
        default=db.func.now(),
        onupdate=db.func.now(),
        index=True,  # 时间字段索引
        info={'description': '最后更新时间'}
    )
    # 状态时间字段统一命名规范
    started_at = db.Column(db.DateTime, nullable=True, info={'description': '开始处理时间'})
    picked_at = db.Column(db.DateTime, nullable=True, info={'description': '拣货完成时间'})
    packed_at = db.Column(db.DateTime, nullable=True, info={'description': '打包完成时间'})
    delivered_at = db.Column(db.DateTime, nullable=True, info={'description': '发货时间'})
    completed_at = db.Column(db.DateTime, nullable=True, info={'description': '流程完成时间'})
    closed_at = db.Column(db.DateTime, nullable=True, info={'description': '单据关闭时间'})

    # 关系加载策略优化
    warehouse = db.relationship(
        'Warehouse',
        backref=db.backref('dns', lazy='dynamic'),
        lazy='joined',
        info={'description': '仓库对象'}
    )
    recipient = db.relationship(
        'Recipient',
        backref=db.backref('dns', lazy='dynamic'),
        lazy='joined',
        info={'description': '收货人对象'}
    )
    carrier = db.relationship(
        'Carrier',
        backref=db.backref('dns', lazy='dynamic'),
        lazy='joined',
        info={'description': '承运商对象'}
    )
    creator = db.relationship(
        'User',
        backref=db.backref('created_dns', lazy='dynamic'),
        lazy='joined',
        info={'description': '创建人对象'}
    )
    details = db.relationship(
        'DNDetail',
        backref='dn',
        lazy='select',  # 按需加载明细
        cascade='all, delete-orphan',
        info={'description': '发货明细集合'}
    )


class DNDetail(db.Model):
    """发货明细表
    
    Attributes:
        quantity: 计划数量 (必须≥0)
        picked_quantity: 已拣数量 (必须≤quantity)
    """
    __tablename__ = 'dn_details'
    
    __table_args__ = (
        db.Index('idx_dn_detail_goods_dn', 'goods_id', 'dn_id'),  # 商品维度查询
        db.CheckConstraint('quantity >= 0', name='chk_dn_detail_quantity'),
        db.CheckConstraint('picked_quantity <= quantity', name='chk_dn_detail_picked_qty'),
        db.CheckConstraint('packed_quantity <= picked_quantity', name='chk_dn_detail_packed_qty')
    )

    id = db.Column(db.Integer, primary_key=True)
    dn_id = db.Column(
        db.Integer,
        db.ForeignKey('dn.id', ondelete='CASCADE'),
        nullable=False,
        info={'description': '主单ID'}
    )
    goods_id = db.Column(
        db.Integer,
        db.ForeignKey('goods.id', ondelete='RESTRICT'),
        nullable=False,
        info={'description': '商品ID'}
    )
    quantity = db.Column(
        db.Integer,
        nullable=False,
        default=0,
        info={'description': '计划出库数量'}
    )
    picked_quantity = db.Column(
        db.Integer,
        default=0,
        nullable=False,
        info={'description': '已拣选数量'}
    )
    packed_quantity = db.Column(
        db.Integer,
        default=0,
        nullable=False,
        info={'description': '已打包数量'}
    )
    delivered_quantity = db.Column(
        db.Integer,
        default=0,
        nullable=False,
        info={'description': '已发货数量'}
    )
    remark = db.Column(
        db.String(255),
        nullable=True,
        info={'description': '明细备注'}
    )
    created_by = db.Column(
        db.Integer,
        db.ForeignKey('users.id', ondelete='RESTRICT'),
        nullable=False,
        info={'description': '创建人ID'}
    )
    create_time = db.Column(
        db.DateTime,
        default=db.func.now(),
        info={'description': '创建时间'}
    )
    update_time = db.Column(
        db.DateTime,
        default=db.func.now(),
        onupdate=db.func.now(),
        info={'description': '最后更新时间'}
    )

    # 关系优化
    goods = db.relationship(
        'Goods',
        backref=db.backref('dn_details', lazy='dynamic'),
        lazy='joined',
        info={'description': '商品对象'}
    )
    creator = db.relationship(
        'User',
        backref=db.backref('created_dn_details', lazy='dynamic'),
        lazy='joined',
        info={'description': '创建人对象'}
    )


class DNCustoms(db.Model):
    """DN 报关快照（海外件）

    由对接方（批发站）随 DN 带来：发票号、贸易条件、收件人税号、运费、收件人、
    每个商品的 HS / 英文品名 / 成交单价等。结构合法即整体存下（内容不全照收），
    是否能出单证由 problems 判断。一张 DN 至多一条；存在即视为海外件（is_export）。
    """
    __tablename__ = 'dn_customs'

    __table_args__ = (
        db.UniqueConstraint('dn_id', name='uq_dn_customs_dn_id'),
    )

    id = db.Column(db.Integer, primary_key=True)
    dn_id = db.Column(
        db.Integer,
        db.ForeignKey('dn.id', ondelete='CASCADE'),
        nullable=False,
        info={'description': 'DN ID'}
    )
    invoice_number = db.Column(db.String(50), nullable=True, info={'description': '发票号（空则用 DN 订单号）'})
    currency = db.Column(db.String(10), nullable=True, info={'description': '币种（ISO 4217）'})
    incoterm = db.Column(db.String(20), nullable=True, info={'description': '贸易条件（Incoterms 2020）'})
    export_reason = db.Column(db.String(50), nullable=True, info={'description': '出口原因（SALE 等）'})
    recipient_country = db.Column(db.String(10), nullable=True, info={'description': '目的国（ISO 3166-1 alpha-2）'})
    recipient_tax_id = db.Column(db.String(100), nullable=True, info={'description': '收件人税号'})
    recipient_tax_id_type = db.Column(db.String(30), nullable=True, info={'description': '收件人税号类型（EORI / VAT / ...）'})
    freight_charge = db.Column(db.Integer, nullable=True, info={'description': '运费（发票上单列并计入总额）'})
    consignee = db.Column(db.JSON, nullable=True, info={'description': '收件人（name/company/address/...）'})
    lines = db.Column(db.JSON, nullable=False, default=list, info={'description': '报关行（按 goods_code）'})
    created_by = db.Column(
        db.Integer,
        db.ForeignKey('users.id', ondelete='SET NULL'),
        nullable=True,
        info={'description': '写入人ID'}
    )
    created_at = db.Column(db.DateTime, default=db.func.now(), info={'description': '创建时间'})
    updated_at = db.Column(
        db.DateTime, default=db.func.now(), onupdate=db.func.now(),
        info={'description': '最后替换时间'}
    )

    dn = db.relationship(
        'DN',
        backref=db.backref('customs', uselist=False, lazy='select', cascade='all, delete-orphan'),
        info={'description': 'DN'}
    )

    def to_dict(self) -> dict:
        return {
            'invoice_number': self.invoice_number,
            'currency': self.currency,
            'incoterm': self.incoterm,
            'export_reason': self.export_reason,
            'recipient_country': self.recipient_country,
            'recipient_tax_id': self.recipient_tax_id,
            'recipient_tax_id_type': self.recipient_tax_id_type,
            'freight_charge': self.freight_charge,
            'consignee': self.consignee,
            'lines': self.lines or [],
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }


class DNPackage(db.Model):
    """DN 装箱记录（箱号、毛重、外箱尺寸）；PDA / Web 打包时整体替换。"""
    __tablename__ = 'dn_packages'

    __table_args__ = (
        db.UniqueConstraint('dn_id', 'package_no', name='uq_dn_package_no'),
        db.CheckConstraint('package_no >= 1', name='chk_dn_package_no'),
    )

    id = db.Column(db.Integer, primary_key=True)
    dn_id = db.Column(
        db.Integer,
        db.ForeignKey('dn.id', ondelete='CASCADE'),
        nullable=False,
        index=True,
        info={'description': 'DN ID'}
    )
    package_no = db.Column(db.Integer, nullable=False, info={'description': '箱号（从 1 连续）'})
    gross_weight_kg = db.Column(db.Numeric(6, 3), nullable=False, info={'description': '毛重（千克，三位小数）'})
    length_mm = db.Column(db.Integer, nullable=False, info={'description': '长（毫米）'})
    width_mm = db.Column(db.Integer, nullable=False, info={'description': '宽（毫米）'})
    height_mm = db.Column(db.Integer, nullable=False, info={'description': '高（毫米）'})
    remark = db.Column(db.String(255), nullable=True, info={'description': '备注'})
    created_by = db.Column(
        db.Integer,
        db.ForeignKey('users.id', ondelete='SET NULL'),
        nullable=True,
        info={'description': '录入人ID'}
    )
    created_at = db.Column(db.DateTime, default=db.func.now(), info={'description': '录入时间'})

    dn = db.relationship(
        'DN',
        backref=db.backref(
            'packages', lazy='select', cascade='all, delete-orphan',
            order_by='DNPackage.package_no',
        ),
        info={'description': 'DN'}
    )

    def to_dict(self) -> dict:
        return {
            'package_no': self.package_no,
            'gross_weight_kg': float(self.gross_weight_kg) if self.gross_weight_kg is not None else None,
            'length_mm': self.length_mm,
            'width_mm': self.width_mm,
            'height_mm': self.height_mm,
            'remark': self.remark,
        }


class DNDocument(db.Model):
    """DN 出口单证（商业发票 / 装箱单）PDF。

    生成即定稿：PDF 字节存库，不再改写；数据变化时旧版作废（status=void）、新版 version+1。
    同一版本的发票与装箱单成对签发，共享 version 与 data_sha256（单证数据指纹）。
    """
    __tablename__ = 'dn_documents'

    __table_args__ = (
        db.UniqueConstraint('dn_id', 'doc_type', 'version', name='uq_dn_document_version'),
        db.Index('idx_dn_document_status', 'dn_id', 'status'),
        db.CheckConstraint("doc_type IN ('commercial_invoice','packing_list')", name='chk_dn_document_type'),
        db.CheckConstraint("status IN ('issued','void')", name='chk_dn_document_status'),
    )

    DOC_TYPES = ('commercial_invoice', 'packing_list')
    STATUSES = ('issued', 'void')

    id = db.Column(db.Integer, primary_key=True)
    dn_id = db.Column(
        db.Integer,
        db.ForeignKey('dn.id', ondelete='CASCADE'),
        nullable=False,
        info={'description': 'DN ID'}
    )
    doc_type = db.Column(db.String(30), nullable=False, info={'description': 'commercial_invoice / packing_list'})
    version = db.Column(db.Integer, nullable=False, info={'description': '版本（同 DN 递增，发票与装箱单共用）'})
    document_number = db.Column(db.String(80), nullable=False, info={'description': '单证号（发票号）'})
    invoice_date = db.Column(db.Date, nullable=False, info={'description': '单证日期（DOCUMENT_TIMEZONE）'})
    status = db.Column(db.String(10), nullable=False, default='issued', info={'description': 'issued / void'})
    sha256 = db.Column(db.String(64), nullable=False, info={'description': 'PDF 文件 SHA-256'})
    data_sha256 = db.Column(db.String(64), nullable=False, info={'description': '单证数据指纹（判断是否需要升版本）'})
    size_bytes = db.Column(db.Integer, nullable=False, info={'description': 'PDF 字节数'})
    file_name = db.Column(db.String(150), nullable=False, info={'description': '文件名'})
    content = db.deferred(db.Column(db.LargeBinary, nullable=False, info={'description': 'PDF 内容'}))
    issued_at = db.Column(db.DateTime, default=db.func.now(), info={'description': '签发时间'})
    issued_by = db.Column(
        db.Integer,
        db.ForeignKey('users.id', ondelete='SET NULL'),
        nullable=True,
        info={'description': '签发人ID'}
    )
    voided_at = db.Column(db.DateTime, nullable=True, info={'description': '作废时间'})
    void_reason = db.Column(db.String(50), nullable=True, info={'description': '作废原因'})

    dn = db.relationship(
        'DN',
        backref=db.backref(
            'customs_documents', lazy='select', cascade='all, delete-orphan',
            order_by='DNDocument.id.desc()',
        ),
        info={'description': 'DN'}
    )

    def to_meta(self) -> dict:
        return {
            'id': self.id,
            'dn_id': self.dn_id,
            'doc_type': self.doc_type,
            'version': self.version,
            'document_number': self.document_number,
            'invoice_date': self.invoice_date.isoformat() if self.invoice_date else None,
            'issued_at': self.issued_at.isoformat() if self.issued_at else None,
            'issued_by': self.issued_by,
            'status': self.status,
            'sha256': self.sha256,
            'size_bytes': self.size_bytes,
            'file_name': self.file_name,
            'voided_at': self.voided_at.isoformat() if self.voided_at else None,
            'void_reason': self.void_reason,
        }