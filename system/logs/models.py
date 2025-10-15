from extensions import db  # 引用 extensions.py 中的 db
from datetime import datetime


class ActivityLog(db.Model):
    """活动日志表（性能优化版）"""
    __tablename__ = 'activity_logs'
    
    __table_args__ = (
        # 复合索引优化
        db.Index('idx_log_endpoint_time', 'endpoint', 'created_at'),
        db.Index('idx_log_status_time', 'status_code', 'created_at'),
        db.Index('idx_ip_prefix', 'ip_address'), 
        # 数据完整性约束
        db.CheckConstraint("status_code BETWEEN 100 AND 599", name='chk_status_code'),
        db.CheckConstraint("method IN ('GET','POST','PUT','DELETE')", name='chk_method_type')
    )
    
    METHOD_TYPES = ('GET', 'POST', 'PUT', 'DELETE')

    id = db.Column(db.Integer, primary_key=True)
    actor = db.Column(db.String(255), index=True, info={'description': '操作主体'})
    endpoint = db.Column(db.String(255), nullable=False, info={'description': '接口路径'})

    method = db.Column(
        db.Enum(*METHOD_TYPES, name='method_enum'),
        nullable=False,
        default='POST'
    )
    request_content_type = db.Column(db.String(255), nullable=True)  # 请求的 Content-Type
    response_content_type = db.Column(db.String(255), nullable=True)  # 响应的 Content-Type
    request_data = db.Column(db.Text, nullable=True)  # 请求内容（原始字符串）
    response_data = db.Column(db.Text, nullable=True)  # 响应内容（原始字符串）
    status_code = db.Column(db.Integer, nullable=False, info={'description': '状态码'})
    ip_address = db.Column(db.String(45), info={'description': 'IPv6兼容地址'})  # 支持IPv6
    processing_time = db.Column(db.Integer, info={'description': '处理时间（毫秒）'})  # 整型优化
    created_at = db.Column(
        db.DateTime,
        default=db.func.now(),
        nullable=False,
        index=True,
        info={'description': '创建时间'}
    )
    

    def to_dict(self):
        """安全脱敏的字典转换"""
        data = {
            "id": self.id,
            "actor": self.actor,
            "endpoint": self.endpoint,
            "method": self.method,
            "status_code": self.status_code,
            "processing_time": self.processing_time,
            "created_at": self.created_at.isoformat(),
            "trace_id": self.trace_id
        }
        # IP地址脱敏（网页3）
        if self.ip_address:
            data['ip_address'] = f"{self.ip_address[:-6]}******"
        return data