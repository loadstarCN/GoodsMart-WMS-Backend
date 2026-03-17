"""Webhook 事件推送日志 — 视图"""
from flask_restx import Resource, abort
from extensions import db
from extensions.error import ForbiddenException
from system.common import permission_required, paginate
from system.third_party.models import APIKey
from system.third_party.views import _get_user_company_id, _is_super_admin
from .models import WebhookEvent
from .services import _send_event
from .schemas import (
    api_ns,
    webhook_event_model,
    pagination_parser,
    pagination_model,
)


def _company_api_key_ids():
    """获取当前用户公司下的所有 API Key ID 列表（用于数据隔离）"""
    company_id = _get_user_company_id()
    if not company_id:
        return []
    return [k.id for k in APIKey.query.filter_by(company_id=company_id).all()]


def _enforce_event_company_scope(event):
    """确保非超管用户只能访问自己公司的 Webhook 事件"""
    if _is_super_admin():
        return
    api_key_ids = _company_api_key_ids()
    if event.api_key_id not in api_key_ids:
        raise ForbiddenException("Permission denied: cannot access webhook events of other companies", 12002)


@api_ns.doc(security="jsonWebToken")
@api_ns.route('/')
class WebhookEventList(Resource):
    """Webhook 事件列表"""

    @permission_required(["all_access", "company_all_access", "webhook_read"])
    @api_ns.expect(pagination_parser)
    @api_ns.marshal_with(pagination_model)
    def get(self):
        """获取 Webhook 推送日志（分页）"""
        args = pagination_parser.parse_args()
        page = args.get('page')
        per_page = args.get('per_page')

        query = WebhookEvent.query.order_by(WebhookEvent.created_at.desc())

        # 非超管用户强制只查自己公司的数据
        if not _is_super_admin():
            api_key_ids = _company_api_key_ids()
            query = query.filter(WebhookEvent.api_key_id.in_(api_key_ids))

        # 筛选条件
        if args.get('event_type'):
            query = query.filter(WebhookEvent.event_type == args['event_type'])
        if args.get('status'):
            query = query.filter(WebhookEvent.status == args['status'])
        if args.get('api_key_id'):
            query = query.filter(WebhookEvent.api_key_id == args['api_key_id'])
        if args.get('keyword'):
            keyword = f"%{args['keyword']}%"
            query = query.filter(
                db.or_(
                    WebhookEvent.event_type.ilike(keyword),
                    WebhookEvent.last_error.ilike(keyword),
                )
            )

        return paginate(query, page, per_page)


@api_ns.doc(security="jsonWebToken")
@api_ns.route('/<int:event_id>')
class WebhookEventDetail(Resource):
    """Webhook 事件详情"""

    @permission_required(["all_access", "company_all_access", "webhook_read"])
    @api_ns.marshal_with(webhook_event_model)
    def get(self, event_id):
        """获取单个 Webhook 事件详情"""
        event = WebhookEvent.query.get_or_404(event_id)
        _enforce_event_company_scope(event)
        return event


@api_ns.doc(security="jsonWebToken")
@api_ns.route('/<int:event_id>/retry')
class WebhookEventRetry(Resource):
    """手动重试 Webhook 事件"""

    @permission_required(["all_access", "company_all_access", "webhook_edit"])
    @api_ns.marshal_with(webhook_event_model)
    def post(self, event_id):
        """手动重试推送单个 Webhook 事件"""
        event = WebhookEvent.query.get_or_404(event_id)
        _enforce_event_company_scope(event)

        if event.status == 'sent':
            abort(400, '该事件已推送成功，无需重试')

        # 手动重试不受次数限制，重置状态为 pending
        event.status = 'pending'
        event.next_retry_at = None
        event.attempts = 0

        # 立即尝试推送
        _send_event(event)
        db.session.commit()

        return event
