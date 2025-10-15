import uuid
from extensions.db import *
from extensions.transaction import transactional
from .models import APIKey

class APIKeyService:

    @staticmethod
    def _get_instance(api_key_or_id: int | APIKey) -> APIKey:
        """
        根据传入参数返回 APIKey 实例。
        如果参数为 int，则调用 get_api_key 获取 APIKey 实例；
        否则直接返回传入的 APIKey 实例。
        """
        if isinstance(api_key_or_id, int):
            return APIKeyService.get_api_key(api_key_or_id)
        return api_key_or_id

    @staticmethod
    def list_api_keys(filters: dict):
        """
        根据过滤条件返回 APIKey 查询对象
        """
        query = APIKey.query.order_by(APIKey.id.desc())

        if filters.get('is_active') is not None:
            query = query.filter(APIKey.is_active == filters['is_active'])
        if filters.get('system_name'):
            query = query.filter(APIKey.system_name.ilike(f"%{filters['system_name']}%"))

        return query

    @staticmethod
    def get_api_key(api_key_id: int) -> APIKey:
        """
        根据 ID 获取单个 APIKey，不存在时抛出 404
        """
        api_key = get_object_or_404(APIKey, api_key_id)
        return api_key

    @staticmethod
    @transactional
    def create_api_key(data: dict) -> APIKey:
        """
        创建新 APIKey
        """
        new_api_key = APIKey(
            key=str(uuid.uuid4()),
            system_name=data['system_name'],
            user_id=data.get('user_id'),
            permissions=data.get('permissions', [])
        )
        db.session.add(new_api_key)
        # db.session.commit()
        return new_api_key

    @staticmethod
    @transactional
    def update_api_key(api_key_id: int, data: dict) -> APIKey:
        """
        更新 APIKey 信息
        """
        api_key = APIKeyService.get_api_key(api_key_id)

        api_key.system_name = data.get('system_name', api_key.system_name)
        api_key.permissions = data.get('permissions', api_key.permissions)
        api_key.user_id = data.get('user_id', api_key.user_id)

        # db.session.commit()
        return api_key

    @staticmethod
    @transactional
    def delete_api_key(api_key_id: int):
        """
        删除 APIKey
        """
        api_key = APIKeyService.get_api_key(api_key_id)
        db.session.delete(api_key)
        # db.session.commit()
