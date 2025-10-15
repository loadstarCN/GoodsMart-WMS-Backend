from extensions.db import *
from sqlalchemy.ext.hybrid import hybrid_property

class Location(db.Model):
    """仓库库位信息表
    
    Attributes:
        code: 库位代码 (同一仓库下唯一)
        location_type: 库位类型 (standard/damaged/return)
        capacity: 存储容量 (单位立方米)
        is_active: 启用状态 (默认True)
    """
    __tablename__ = 'locations'

    __table_args__ = (
        # 核心业务索引
        db.Index('idx_location_warehouse_active', 'warehouse_id', 'is_active'),  # 仓库维度状态查询
        db.Index('idx_location_code', 'code'),  # 库位代码查询加速
        
        # 数据完整性约束
        db.UniqueConstraint('warehouse_id', 'code', name='uq_warehouse_code'),  # 同一仓库下代码唯一
        db.CheckConstraint('width > 0 AND depth > 0 AND height > 0', name='chk_dimension_positive'),
    )

    LOCATION_TYPES = ('standard', 'damaged', 'return')

    id = db.Column(db.Integer, primary_key=True)
    warehouse_id = db.Column(
        db.Integer, 
        db.ForeignKey('warehouses.id', ondelete='RESTRICT'),  # 阻止仓库删除
        nullable=False,
        info={'description': '所属仓库ID'}
    )
    code = db.Column(
        db.String(50), 
        nullable=False,
        info={'description': '库位代码（同一仓库下唯一）'}
    )
    description = db.Column(
        db.String(255), 
        nullable=True,
        info={'description': '库位描述'}
    )
    location_type = db.Column(
        db.Enum(*LOCATION_TYPES, name='location_type_enum'),
        nullable=False,
        default='standard',
        info={'description': '库位类型（standard/damaged/return）'}
    )
    width = db.Column(
        db.Float, 
        nullable=True,
        info={'description': '库位宽度（单位：厘米）'}
    )
    depth = db.Column(
        db.Float, 
        nullable=True,
        info={'description': '库位深度（单位：厘米）'}
    )
    height = db.Column(
        db.Float, 
        nullable=True,
        info={'description': '库位高度（单位：厘米）'}
    )
    capacity = db.Column(
        db.Float, 
        nullable=True,
        info={'description': '最大存储容量（立方米）'}
    )
    is_active = db.Column(
        db.Boolean, 
        default=True,
        info={'description': '启用状态（默认激活）'}
    )
    created_by = db.Column(
        db.Integer, 
        db.ForeignKey('users.id', ondelete='RESTRICT'),  # 创建人不可删除
        nullable=False,
        info={'description': '创建人ID'}
    )
    created_at = db.Column(
        db.DateTime, 
        default=db.func.now(),
        info={'description': '创建时间（自动记录）'}
    )
    updated_at = db.Column(
        db.DateTime, 
        default=db.func.now(),
        onupdate=db.func.now(),
        info={'description': '最后更新时间（自动更新）'}
    )

    # 关系加载优化
    warehouse = db.relationship(
        'Warehouse', 
        backref=db.backref('locations', lazy='dynamic'),  # 动态反向查询
        lazy='joined',  # 立即加载仓库信息
        info={'description': '所属仓库对象'}
    )
    
    creator = db.relationship(
        'User',
        backref=db.backref('created_locations', lazy='dynamic'),  # 动态反向查询
        lazy='joined',  # 立即加载创建者
        info={'description': '库位创建者对象'}
    )

    @hybrid_property
    def volume_capacity(self):
        return self.width * self.depth * self.height