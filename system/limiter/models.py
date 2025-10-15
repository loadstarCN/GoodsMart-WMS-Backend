from extensions import db
from datetime import datetime

class IPBlacklist(db.Model):
    """IP黑名单表"""
    __tablename__ = 'blacklist'

    __table_args__ = (
        # 跨平台索引优化
        db.Index('idx_blacklist_ip', 'ip_address'),
        db.Index('idx_blacklist_time', 'timestamp'),
    )
    id = db.Column(db.Integer, primary_key=True)
    ip_address = db.Column(
        db.String(45),  # 兼容IPv6最大长度
        unique=True,
        nullable=False,
        info={'description': 'IPv4/IPv6地址'}
    )
    reason = db.Column(db.String(255))
    timestamp = db.Column(
        db.DateTime, 
        default=db.func.now(),
        index=True,
        info={'description': '创建时间'}
    )

    def to_dict(self):
        """安全脱敏输出"""
        return {
            "ip": self._mask_ip(self.ip_address),
            "type": self.ip_type,
            "reason": self.reason
        }
    
    def _mask_ip(self, ip):
        """IP地址脱敏处理"""
        if ':' in ip:  # IPv6
            return f"{ip[:8]}******{ip[-5:]}"
        return f"{ip.rsplit('.',1)[0]}.*"  # IPv4

class IPWhitelist(db.Model):
    """IP白名单表"""
    __tablename__ = 'whitelist'

    __table_args__ = (
        db.Index('idx_whitelist_ip', 'ip_address'),
        db.Index('idx_whitelist_time', 'timestamp'),
    )

    id = db.Column(db.Integer, primary_key=True)
    ip_address = db.Column(
        db.String(45),  # 兼容IPv6最大长度
        unique=True,
        nullable=False,
        info={'description': 'IPv4/IPv6地址'}
    )
    reason = db.Column(db.String(255))
    timestamp = db.Column(
        db.DateTime, 
        default=db.func.now(),
        index=True,
        info={'description': '创建时间'}
    )

    def to_dict(self):
        return {
            "ip": self._mask_ip(self.ip_address),
            "type": self.ip_type,
            "reason": self.reason
        }
    
    def _mask_ip(self, ip):
        """IP地址脱敏处理"""
        if ':' in ip:  # IPv6
            return f"{ip[:8]}******{ip[-5:]}"
        return f"{ip.rsplit('.',1)[0]}.*"  # IPv4