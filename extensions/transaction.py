# extensions/transaction.py
from functools import wraps
from flask import g  # 全局对象，用于记录请求范围内的事务嵌套深度
from .db import db   # 导入初始化后的数据库对象

def transactional(func):
    """事务管理装饰器：确保嵌套调用只在最外层提交或回滚事务。"""
    @wraps(func)
    def wrapper(*args, **kwargs):
        # 初始化或更新嵌套事务深度计数
        if not hasattr(g, "transaction_depth"):
            g.transaction_depth = 0
        g.transaction_depth += 1
        try:
            result = func(*args, **kwargs)     # 执行目标函数
            if g.transaction_depth == 1:
                # 只有在最外层调用（depth回到1）时提交事务
                db.session.commit()
            return result
        except Exception as e:
            # 发生异常时，只有最外层调用负责回滚
            if g.transaction_depth == 1:
                db.session.rollback()
            # 将异常继续抛出，以便上层逻辑知道发生了错误
            raise
        finally:
            # 函数执行完毕，减少嵌套深度
            g.transaction_depth -= 1
    return wrapper
