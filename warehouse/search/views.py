from functools import wraps

from flask import g
from flask_jwt_extended import current_user, jwt_required
from flask_restx import Resource

from extensions.error import UnauthorizedException
from system.common.permissions import get_actor_company_id
from .schemas import api_ns, search_parser, search_result_model
from .services import GlobalSearchService


def _authenticated(func):
    """只要求已认证（JWT 或 API Key，认证方式同 permission_required）。
    各类型的查看权限在服务里逐类判断：一类都没有时返回空结果，不 403"""
    @wraps(func)
    @jwt_required(optional=True)
    def wrapper(*args, **kwargs):
        system = getattr(g, 'current_system', None)
        if not current_user and not (system and system.get('api_key')):
            raise UnauthorizedException("Unauthorized", 11003)
        return func(*args, **kwargs)
    return wrapper


@api_ns.doc(security="jsonWebToken")
@api_ns.route('', '/')
class GlobalSearch(Resource):

    @_authenticated
    @api_ns.expect(search_parser)
    @api_ns.marshal_with(search_result_model)
    def get(self):
        """
        Global search over documents, tasks and master data, grouped by type.
        - `types`: dn / asn / sorting / picking / packing / delivery / cyclecount / adjustment / goods / location /
          recipient (comma separated; default all). Only types the caller may read (`<module>_read`, or all_access /
          company_all_access) are searched; none → empty result (not 403). Unknown type → 400 40000 (field types)
        - `scope`: warehouse (default; X-WAREHOUSE-ID / warehouse_id like the lists) / all (every accessible warehouse)
        - `limit`: items per type (default 5, max 50). With exactly one type: `page` / `per_page` and `columns`
        - Document numbers as shown (DN-163, PICK-5, CC-12 …) match exactly (`exact`: true, listed first, also in
          `exact_matches`); so do full tracking numbers (spaces / hyphens ignored; delivery tasks by their own number
          or the DN's current one) and goods codes that are the same (case-insensitive). Plain digits try numbers and
          tracking numbers. Shorter than 2 characters: exact numbers / codes only
        - `matched_goods` {goods_id, code, name, quantity}: documents / tasks matched by a detail goods
        - `highlights`: matched ranges in code / title / subtitle / columns.<key> (UTF-16 offsets, end exclusive)
        """
        args = search_parser.parse_args()
        requested = GlobalSearchService.parse_types(args.get('types'))
        scope = GlobalSearchService.parse_scope(args.get('scope'))
        types = GlobalSearchService.readable_types(requested)
        if not types:
            # 一类都不能看：空结果（不查仓库范围，也就不会因为仓库范围 403）
            return GlobalSearchService.search(args.get('q'), [], {}, None, scope=scope), 200

        warehouse_scope = GlobalSearchService.resolve_scope(scope)
        # 商品 / 收货方是公司级主数据：员工 / 公司级 API Key 强制本公司，平台管理员可按参数筛（同两个列表）
        company_id = get_actor_company_id() or args.get('company_id')
        result = GlobalSearchService.search(
            args.get('q'), types, warehouse_scope, company_id, scope=scope,
            limit=args.get('limit'), page=args.get('page'), per_page=args.get('per_page'),
            # 分页 / 列表列只看请求里是否只指定了一类（按权限筛掉后剩一类不算）
            single=len(requested) == 1,
        )
        return result, 200
