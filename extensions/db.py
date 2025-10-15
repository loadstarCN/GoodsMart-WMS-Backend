from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from extensions.error import NotFoundException

db = SQLAlchemy()          # 数据库扩展
migrate = Migrate()        # 数据库迁移扩展

def get_object_or_404(model, object_id, error_message=None):
    """
    安全获取对象或抛出 NotFound 异常
    """
    obj = db.session.get(model, object_id)
    if not obj:
        if not error_message:
            error_message = f"{model.__name__} with id {object_id} not found"
        print(error_message)
        # 确保抛出的是 werkzeug 的 NotFound 异常
        raise NotFoundException(error_message,13001)
    return obj

def get_object_or_none(model, object_id):
    """
    安全获取对象，找不到时返回 None
    """
    return db.session.get(model, object_id)