# utils/pagination.py
from flask import has_request_context, request
from flask_restx import fields, reqparse,inputs

# 单页上限与全量返回上限，防止 ?per_page=100000 / ?all=true 把单进程拖垮
MAX_PER_PAGE = 500
MAX_ALL_ROWS = 5000

# 定义请求参数解析器
pagination_parser = reqparse.RequestParser()
pagination_parser.add_argument(
    'page',
    type=int,
    default=None,
    store_missing=False,  # 未传参时不包含该字段
    help='Page number (默认不传则不生效)'
)
pagination_parser.add_argument(
    'per_page',
    type=int,
    default=None,
    store_missing=False,  # 未传参时不包含该字段
    help=f'每页条目数 (最大 {MAX_PER_PAGE})'
)
pagination_parser.add_argument(
    'all',
    type=inputs.boolean,
    default=None,  # 显式设为 None
    help=f'是否返回全部记录 (true/false/null，最多 {MAX_ALL_ROWS} 条)'
)

def create_pagination_model(api, nested_model):
    return api.model('Page', {
        'page': fields.Integer(description='Current page number'),
        'per_page': fields.Integer(description='Items per page'),
        'total': fields.Integer(description='Total number of items'),
        'pages': fields.Integer(description='Total number of pages'),
        'items': fields.List(fields.Nested(nested_model, skip_none=True), description='Items on the current page'),
        'prev': fields.Integer(description='Previous page number'),
        'next': fields.Integer(description='Next page number'),
        'has_prev': fields.Boolean(description='Is there a previous page'),
        'has_next': fields.Boolean(description='Is there a next page'),
    })


def _wants_all_from_request() -> bool:
    """未显式传 get_all 时，从 ?all= 读取（让所有列表端点天然支持 all=true）"""
    if not has_request_context():
        return False
    raw = request.args.get('all')
    return raw is not None and str(raw).strip().lower() in ('1', 'true', 'yes', 'on')


def paginate(query, page, per_page, get_all=None, schema=None):
    """
    Paginate the results of a SQLAlchemy query (支持全量返回模式)

    :param query: SQLAlchemy query object
    :param page: 当前页码（全量模式下无效）
    :param per_page: 每页数量（全量模式下无效，最大 MAX_PER_PAGE）
    :param schema: 序列化器（可选）
    :param get_all: 是否返回全量数据（覆盖分页参数；None 时取请求参数 all）
    :return: 统一的分页结构字典
    """
    if get_all is None:
        get_all = _wants_all_from_request()

    if get_all:
        # 全量模式：返回所有记录并构造分页结构（有上限）
        total = query.count()  # 使用 COUNT 优化性能
        items = query.limit(MAX_ALL_ROWS).all()
        pages = 1 if total > 0 else 0

        # 应用序列化器
        serialized_items = schema.dump(items) if schema else items

        return {
            'page': 1,
            'per_page': len(items),
            'total': total,
            'pages': pages,
            'items': serialized_items,
            'prev': None,
            'next': None,
            'has_prev': False,
            'has_next': False,
        }
    else:
        # 标准分页模式
        pagination = query.paginate(page=page, per_page=per_page, max_per_page=MAX_PER_PAGE, error_out=False)
        # 应用序列化器
        serialized_items = schema.dump(pagination.items) if schema else pagination.items

        return {
            'page': pagination.page,
            'per_page': pagination.per_page,
            'total': pagination.total,
            'pages': pagination.pages,
            'items': serialized_items,
            'prev': pagination.prev_num if pagination.has_prev else None,
            'next': pagination.next_num if pagination.has_next else None,
            'has_prev': pagination.has_prev,
            'has_next': pagination.has_next,
        }
