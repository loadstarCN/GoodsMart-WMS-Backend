from extensions.db import *
from extensions.error import BadRequestException, ForbiddenException, NotFoundException
from extensions.transaction import transactional
from system.common.permissions import assert_grantable_permissions, get_actor_context
from system.user.models import User
from .models import APIKey
from .utils import api_key_prefix, generate_api_key, hash_api_key


def validate_webhook_url(url):
    # 延迟导入：system.webhook 包在初始化时会反向引用本模块的 views，顶层导入会形成循环
    from system.webhook.utils import validate_webhook_url as _validate
    return _validate(url)


def clean_webhook_subscriptions(subscriptions):
    """webhook_subscriptions：事件类型字符串列表，只允许白名单里的广播事件；None = 空列表"""
    from system.webhook.services import SUBSCRIBABLE_EVENT_TYPES  # 延迟导入，原因同上

    if subscriptions is None:
        return []
    if not isinstance(subscriptions, list) or not all(isinstance(s, str) for s in subscriptions):
        raise BadRequestException("webhook_subscriptions must be a list of event types", 14018)
    cleaned = list(dict.fromkeys(s.strip() for s in subscriptions if s and s.strip()))
    unknown = [s for s in cleaned if s not in SUBSCRIBABLE_EVENT_TYPES]
    if unknown:
        raise BadRequestException(
            f"Unsupported webhook_subscriptions: {', '.join(unknown)}; "
            f"allowed: {', '.join(SUBSCRIBABLE_EVENT_TYPES)}",
            14018,
        )
    return cleaned


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
        if filters.get('keyword'):
            keyword = f"%{filters['keyword']}%"
            query = query.filter(
                APIKey.system_name.ilike(keyword) | APIKey.key_prefix.ilike(keyword)
            )
        if filters.get('company_id') is not None:
            query = query.filter(APIKey.company_id == filters['company_id'])

        return query

    @staticmethod
    def get_api_key(api_key_id: int) -> APIKey:
        """
        根据 ID 获取单个 APIKey，不存在时抛出 404
        """
        api_key = get_object_or_404(APIKey, api_key_id)
        return api_key

    # ------------------------------------------------------------------
    # 输入校验：权限 / 绑定用户 / webhook
    # ------------------------------------------------------------------
    @staticmethod
    def _clean_permissions(permissions, actor):
        if permissions is None:
            return []
        if not isinstance(permissions, list) or not all(isinstance(p, str) for p in permissions):
            raise BadRequestException("permissions must be a list of permission names", 14010)
        cleaned = sorted({p.strip() for p in permissions if p and p.strip()})
        assert_grantable_permissions(cleaned, actor)
        return cleaned

    @staticmethod
    def _validate_user_binding(user_id, company_id, actor):
        """非超管只能把 API Key 绑定到本公司员工，且该员工不能持有平台权限"""
        if user_id is None:
            return None
        user = db.session.get(User, user_id)
        if not user:
            raise NotFoundException(f"User with id {user_id} not found", 13002)
        is_super, _, _ = actor
        if is_super:
            return user_id
        if user.type != 'staff' or user.company_id != company_id:
            raise ForbiddenException("API Key can only be bound to a staff of your own company", 12006)
        if user.has_permission('all_access'):
            raise ForbiddenException("API Key cannot be bound to a platform administrator", 12006)
        return user_id

    @staticmethod
    @transactional
    def create_api_key(data: dict, actor=None) -> APIKey:
        """
        创建新 APIKey。返回的实例带有临时属性 plain_key（明文，只在此时可见）
        """
        actor = actor or get_actor_context()
        raw_key = generate_api_key()

        new_api_key = APIKey(
            key=hash_api_key(raw_key),
            key_prefix=api_key_prefix(raw_key),
            system_name=data['system_name'],
            permissions=APIKeyService._clean_permissions(data.get('permissions'), actor),
        )
        new_api_key.company_id = data.get('company_id')
        new_api_key.user_id = APIKeyService._validate_user_binding(
            data.get('user_id'), new_api_key.company_id, actor
        )
        new_api_key.is_active = bool(data.get('is_active', True))
        new_api_key.webhook_url = validate_webhook_url(data.get('webhook_url'))
        new_api_key.webhook_secret = data.get('webhook_secret') or None
        new_api_key.webhook_subscriptions = clean_webhook_subscriptions(data.get('webhook_subscriptions'))
        db.session.add(new_api_key)
        db.session.flush()
        new_api_key.plain_key = raw_key
        return new_api_key

    @staticmethod
    @transactional
    def update_api_key(api_key_id: int, data: dict, actor=None) -> APIKey:
        """
        更新 APIKey 信息（company_id 不可修改；webhook_secret 传空即保持不变）
        """
        actor = actor or get_actor_context()
        api_key = APIKeyService.get_api_key(api_key_id)

        api_key.system_name = data.get('system_name', api_key.system_name)
        if 'permissions' in data:
            api_key.permissions = APIKeyService._clean_permissions(data.get('permissions'), actor)
        if 'user_id' in data:
            api_key.user_id = APIKeyService._validate_user_binding(
                data.get('user_id'), api_key.company_id, actor
            )
        if 'is_active' in data:
            api_key.is_active = bool(data['is_active'])
        if 'webhook_url' in data:
            api_key.webhook_url = validate_webhook_url(data.get('webhook_url'))
        if data.get('webhook_secret'):
            api_key.webhook_secret = data['webhook_secret']
        if 'webhook_subscriptions' in data:
            api_key.webhook_subscriptions = clean_webhook_subscriptions(data.get('webhook_subscriptions'))

        return api_key

    @staticmethod
    @transactional
    def delete_api_key(api_key_id: int):
        """
        删除 APIKey
        """
        api_key = APIKeyService.get_api_key(api_key_id)
        db.session.delete(api_key)

    @staticmethod
    @transactional
    def deactivate_company_keys(company_id: int) -> int:
        """停用某公司名下全部 API Key（删除公司时调用）"""
        keys = APIKey.query.filter_by(company_id=company_id, is_active=True).all()
        for key in keys:
            key.is_active = False
        return len(keys)
