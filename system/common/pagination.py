# utils/pagination.py
from flask_restx import fields, reqparse,inputs

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
    help='每页条目数 (默认不传则不生效)'
)
pagination_parser.add_argument(
    'all',
    type=inputs.boolean,
    default=None,  # 显式设为 None
    help='是否返回全部记录 (true/false/null)'
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


def paginate(query, page, per_page, get_all=False, schema=None):
    """
    Paginate the results of a SQLAlchemy query (支持全量返回模式)
    
    :param query: SQLAlchemy query object
    :param page: 当前页码（全量模式下无效）
    :param per_page: 每页数量（全量模式下无效）
    :param schema: 序列化器（可选）
    :param get_all: 是否返回全量数据（覆盖分页参数）
    :return: 统一的分页结构字典
    """
    if get_all:
        # 全量模式：返回所有记录并构造分页结构
        total = query.count()  # 使用 COUNT 优化性能
        items = query.all()
        pages = 1 if total > 0 else 0
        
        # 应用序列化器
        serialized_items = schema.dump(items) if schema else items
        
        return {
            'page': 1,
            'per_page': total,
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
        pagination = query.paginate(page=page, per_page=per_page, error_out=False)
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