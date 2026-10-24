"""承运商在线对接（自动建运单）的设置：读取 / 保存 / 测试连接，以及按承运商取适配器。

- 一个承运商至多一条对接（carrier_integrations）；插件见 warehouse/carrier/integrations。
- 密钥（设置项 kind = secret）加密保存，接口只返回「已设置 / 未设置」；保存时没传或传空 = 保持原值。
- 有未结束的自动运单（pending / active / unknown / cancelling）时不能删掉对接或换插件（取消运单要用这套设置）→ 409 16125。
- 设置不合法 → 400 16124（field = adapter / settings.<key> / secrets.<key> / enabled）。
"""
import json
import logging

from cryptography.fernet import InvalidToken

from extensions.db import db
from extensions.error import BadRequestException, ConflictException
from extensions.transaction import transactional
from system.settings.services import decrypt_secret, encrypt_secret

from .integrations import adapter_class, display_name, registry
from .integrations.base import STEP_TEST, CarrierError, clock
from .methods import adopt_integration, release_integration
from .models import Carrier, CarrierIntegration

logger = logging.getLogger(__name__)

TEST_BUDGET_SECONDS = 20


def _blocker(code, message, **extra):
    item = {'code': code, 'message': message}
    item.update({k: v for k, v in extra.items() if v is not None})
    return item


class CarrierIntegrationService:

    # ------------------------------------------------------------------
    # 读取
    # ------------------------------------------------------------------

    @staticmethod
    def _decrypt(integration: CarrierIntegration):
        """→ (secrets dict, 解得开)"""
        if not integration.secrets:
            return {}, True
        try:
            value = json.loads(decrypt_secret(integration.secrets))
        except (InvalidToken, ValueError, TypeError):
            logger.error("carrier %s: saved integration keys cannot be decrypted (SETTINGS_ENCRYPTION_KEY changed?)",
                         integration.carrier_id)
            return {}, False
        return (value if isinstance(value, dict) else {}), True

    @staticmethod
    def snapshot(integration):
        """对接设置的纯数据副本（不引用 ORM 对象，调承运商时不用碰数据库）：
        {carrier_id, adapter, enabled, settings, secrets, secrets_readable}；没有对接为 None"""
        if integration is None:
            return None
        secrets, readable = CarrierIntegrationService._decrypt(integration)
        return {
            'carrier_id': integration.carrier_id,
            'adapter': integration.adapter,
            'enabled': bool(integration.enabled),
            'settings': dict(integration.settings or {}),
            'secrets': secrets,
            'secrets_readable': readable,
        }

    @staticmethod
    def for_carrier(carrier):
        """承运商 → 对接设置快照（没有承运商 / 没有对接为 None）"""
        if carrier is None:
            return None
        return CarrierIntegrationService.snapshot(carrier.integration)

    @staticmethod
    def build(snapshot):
        """快照 → 适配器实例；插件没装为 None"""
        if snapshot is None:
            return None
        cls = adapter_class(snapshot['adapter'])
        return cls(snapshot['settings'], snapshot['secrets']) if cls else None

    @staticmethod
    def problems(snapshot, adapter, *, for_create=True) -> list:
        """对接本身的问题 → blockers。for_create=False（取消已建的运单）时不看「停用」。
        CARRIER_INTEGRATION_UNAVAILABLE 插件没装；CARRIER_INTEGRATION_DISABLED 停用；
        CARRIER_NOT_CONFIGURED 必填设置 / 密钥缺（或密钥解不开）；CARRIER_CONFIG_INVALID 取值不合法。"""
        name = display_name(snapshot['adapter'])
        if adapter is None:
            return [_blocker('CARRIER_INTEGRATION_UNAVAILABLE',
                             f"The {snapshot['adapter']} integration is not installed on this server")]
        blockers = []
        if for_create and not snapshot['enabled']:
            blockers.append(_blocker('CARRIER_INTEGRATION_DISABLED',
                                     f"The {name} integration of this carrier is turned off"))
        if not snapshot['secrets_readable']:
            blockers.append(_blocker('CARRIER_NOT_CONFIGURED',
                                     f"The saved {name} keys cannot be read (the encryption key of the server "
                                     "changed?); enter them again in the carrier settings", field='secrets'))
        else:
            missing = adapter.missing_settings()
            if missing:
                blockers.append(_blocker('CARRIER_NOT_CONFIGURED',
                                         f"{name} settings are incomplete (missing: {', '.join(missing)})",
                                         fields=missing))
        for problem in adapter.setting_problems():
            blockers.append(_blocker('CARRIER_CONFIG_INVALID', f"{name} settings: {problem}"))
        return blockers

    @staticmethod
    def available() -> list:
        """可选的对接插件（承运商编辑页的下拉框与表单）"""
        return [cls.describe() for cls in sorted(registry().values(), key=lambda c: c.name.lower())]

    @staticmethod
    def payload(carrier: Carrier):
        """GET /carrier/<id>/integration：{adapter, name, enabled, settings, secrets: {key: 已设置}, ready, problems,
        updated_at, updated_by}；没有对接为 null"""
        integration = carrier.integration
        if integration is None:
            return None
        snapshot = CarrierIntegrationService.snapshot(integration)
        adapter = CarrierIntegrationService.build(snapshot)
        cls = adapter_class(integration.adapter)
        problems = CarrierIntegrationService.problems(snapshot, adapter, for_create=False)
        return {
            'adapter': integration.adapter,
            'name': display_name(integration.adapter),
            'enabled': bool(integration.enabled),
            'settings': adapter.settings if adapter is not None else dict(integration.settings or {}),
            'secrets': {key: bool(snapshot['secrets'].get(key)) for key in (cls.secret_keys() if cls else [])},
            'ready': not problems,
            'problems': problems,
            'updated_at': integration.updated_at.isoformat() if integration.updated_at else None,
            'updated_by': integration.updated_by,
        }

    # ------------------------------------------------------------------
    # 保存 / 删除
    # ------------------------------------------------------------------

    @staticmethod
    def _open_shipments(carrier: Carrier):
        """承运商未结束的插件运单（source = api；手填 / 文件导回的运单不用这套设置取消）"""
        from warehouse.dn.carrier_services import OPEN_STATUSES
        from warehouse.dn.models import DNCarrierShipment
        return (DNCarrierShipment.query
                .filter(DNCarrierShipment.carrier_id == carrier.id, DNCarrierShipment.source == 'api',
                        DNCarrierShipment.status.in_(OPEN_STATUSES))
                .order_by(DNCarrierShipment.id)
                .all())

    @staticmethod
    def _assert_no_open_shipments(carrier: Carrier, what: str):
        open_records = CarrierIntegrationService._open_shipments(carrier)
        if open_records:
            raise ConflictException(
                f"{what}: this carrier has shipments that are not finished yet (they are cancelled with these "
                "settings). Cancel or finish them first.", 16125,
                details={'shipments': [{'id': r.id, 'dn_id': r.dn_id, 'status': r.status,
                                        'tracking_number': r.tracking_number} for r in open_records[:20]]},
            )

    @staticmethod
    def _parse(data: dict, current):
        """请求体 → (插件类, enabled, 设置, 密钥)；密钥没传 / 空串 = 用 current 里的"""
        if not isinstance(data, dict):
            raise BadRequestException("Request body must be an object", 16124)
        cls = adapter_class(data.get('adapter'))
        if cls is None:
            raise BadRequestException(f"Unknown carrier integration: {data.get('adapter')}", 16124, field='adapter')
        enabled = data.get('enabled', True)
        if not isinstance(enabled, bool):
            raise BadRequestException("enabled must be true or false", 16124, field='enabled')
        try:
            settings = cls.clean_settings(data.get('settings'))
        except ValueError as exc:
            key, message = exc.args if len(exc.args) == 2 else (None, str(exc))
            raise BadRequestException(message, 16124, field=f"settings.{key}" if key else 'settings') from exc

        same_adapter = current is not None and current['adapter'] == cls.code
        secrets = dict(current['secrets']) if same_adapter else {}
        given = data.get('secrets') or {}
        if not isinstance(given, dict):
            raise BadRequestException("secrets must be an object", 16124, field='secrets')
        for key in cls.secret_keys():
            value = given.get(key)
            if value is None or (isinstance(value, str) and not value.strip()):
                continue
            if not isinstance(value, str):
                raise BadRequestException(f"{key} must be a string", 16124, field=f"secrets.{key}")
            secrets[key] = value.strip()
        return cls, enabled, settings, secrets

    @staticmethod
    @transactional
    def save(carrier: Carrier, data: dict, user_id):
        """PUT /carrier/<id>/integration：{adapter, enabled, settings, secrets}。adapter 为空 = 删除对接。
        对接方式跟上（warehouse/carrier/methods.py）：新建 / 重新启用 / 换插件后，还是默认值的方式改成 api；
        删掉对接（或换成不支持的插件）时 api 的方式退回默认值。"""
        integration = carrier.integration
        if not (data or {}).get('adapter'):
            if integration is not None:
                CarrierIntegrationService._assert_no_open_shipments(carrier, "The integration cannot be removed")
                db.session.delete(integration)
                release_integration(carrier)
                db.session.flush()
                db.session.expire(carrier, ['integration'])
            return None

        current = CarrierIntegrationService.snapshot(integration)
        cls, enabled, settings, secrets = CarrierIntegrationService._parse(data, current)
        was_enabled = integration is not None and integration.adapter == cls.code and bool(integration.enabled)
        if integration is not None and integration.adapter != cls.code:
            CarrierIntegrationService._assert_no_open_shipments(carrier, "The integration cannot be changed")
            release_integration(carrier, cls)
        if enabled and not was_enabled:
            adopt_integration(carrier, cls)
        if integration is None:
            integration = CarrierIntegration(carrier_id=carrier.id)
            db.session.add(integration)
        integration.adapter = cls.code
        integration.enabled = enabled
        integration.settings = settings
        integration.secrets = encrypt_secret(json.dumps(secrets)) if secrets else None
        integration.updated_by = user_id
        db.session.flush()
        db.session.expire(carrier, ['integration'])
        logger.warning("carrier %s: %s integration saved by user %s (enabled: %s)", carrier.id, cls.code, user_id,
                       enabled)
        return CarrierIntegrationService.payload(carrier)

    # ------------------------------------------------------------------
    # 测试连接
    # ------------------------------------------------------------------

    @staticmethod
    def test(carrier: Carrier, data=None) -> dict:
        """POST /carrier/<id>/integration/test：用请求里的设置（没传的用已保存的）连一次承运商，不保存。
        → {ok, message, errors?, missing?}（连不上也是 200，ok = false）"""
        current = CarrierIntegrationService.snapshot(carrier.integration)
        if data and data.get('adapter'):
            cls, enabled, settings, secrets = CarrierIntegrationService._parse(data, current)
            snapshot = {'carrier_id': carrier.id, 'adapter': cls.code, 'enabled': enabled, 'settings': settings,
                        'secrets': secrets, 'secrets_readable': True}
        elif current is not None:
            snapshot = current
        else:
            raise BadRequestException("This carrier has no integration to test", 16124, field='adapter')
        adapter = CarrierIntegrationService.build(snapshot)
        problems = CarrierIntegrationService.problems(snapshot, adapter, for_create=False)
        if problems:
            missing = next((p.get('fields') for p in problems if p['code'] == 'CARRIER_NOT_CONFIGURED'), None)
            return {'ok': False, 'message': problems[0]['message'], 'missing': missing or []}
        try:
            result = adapter.test_connection(deadline=clock() + TEST_BUDGET_SECONDS)
        except CarrierError as exc:
            return {'ok': False, 'message': exc.message, 'errors': exc.errors, 'status_code': exc.status_code,
                    'permission_denied': exc.permission_denied, 'step': exc.step or STEP_TEST}
        except NotImplementedError:
            return {'ok': False, 'message': f"The {adapter.name} integration cannot test the connection"}
        return {'ok': True, 'message': f"Connected to {adapter.name}", **(result or {})}
