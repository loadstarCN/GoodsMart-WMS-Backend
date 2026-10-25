from flask_restx import Namespace, fields, reqparse
from extensions import authorizations
from .services import SEARCH_TYPES, SCOPES, DEFAULT_LIMIT, MAX_LIMIT, MAX_QUERY_LENGTH

# 初始化 Namespace
api_ns = Namespace(
    'search',
    description='Global search (top bar search box / search result page)',
    authorizations=authorizations
)

# -----------------------------
# 请求参数
# -----------------------------
search_parser = reqparse.RequestParser()
search_parser.add_argument('q', type=str, location='args',
                           help=f'Keyword (trimmed, at most {MAX_QUERY_LENGTH} characters; longer input is cut). '
                                'Shorter than 2 characters: only exact document numbers / goods / location codes')
search_parser.add_argument('types', type=str, location='args', action='append',
                           help=f"Types to search, comma separated or repeated ({', '.join(SEARCH_TYPES)}); "
                                'default all types the caller may read')
search_parser.add_argument('scope', type=str, location='args',
                           help=f"{' / '.join(SCOPES)}: warehouse (default) = current warehouse "
                                '(X-WAREHOUSE-ID / warehouse_id; none = all accessible, same as the lists); '
                                'all = every warehouse the caller can access (the current warehouse is ignored)')
search_parser.add_argument('limit', type=int, location='args',
                           help=f'Items per type (default {DEFAULT_LIMIT}, at most {MAX_LIMIT})')
search_parser.add_argument('page', type=int, location='args',
                           help='Page number (only when exactly one type is searched)')
search_parser.add_argument('per_page', type=int, location='args',
                           help=f'Items per page (only when exactly one type is searched; default = limit, '
                                f'at most {MAX_LIMIT})')
search_parser.add_argument('warehouse_id', type=int, location='args',
                           help='Current warehouse (same as the X-WAREHOUSE-ID header)')
search_parser.add_argument('company_id', type=int, location='args',
                           help='Platform administrators only: limit goods / recipients to one company '
                                '(same as the goods / recipient lists)')

# -----------------------------
# 返回结构
# -----------------------------
highlight_model = api_ns.model('GlobalSearchHighlight', {
    'field': fields.String(description='Display text the range is in: code / title / subtitle / columns.<key>'),
    'start': fields.Integer(description='Start offset in UTF-16 code units (JavaScript string index)'),
    'end': fields.Integer(description='End offset (exclusive)'),
})

url_hint_model = api_ns.model('GlobalSearchUrlHint', {
    'page': fields.String(description='Front-end page type (= type): /<page>/detail/<id>'),
    'id': fields.Integer(description='Record ID'),
})

search_item_model = api_ns.model('GlobalSearchItem', {
    'type': fields.String(description='Type', enum=list(SEARCH_TYPES)),
    'id': fields.Integer(description='Record ID'),
    'code': fields.String(description='Document number (DN-163, PICK-5, …) / goods or location code; '
                                      'recipients have none'),
    'title': fields.String(description='Title text'),
    'subtitle': fields.String(description="Subtitle text (parts joined by ' · '); "
                                          'includes the matched goods when a detail line matched'),
    'status': fields.String(description='Raw status value (documents / tasks); null for master data'),
    'warehouse_id': fields.Integer(description='Warehouse ID (null for goods / recipients)'),
    'url_hint': fields.Nested(url_hint_model, description='Where the front end should go'),
    'matched_field': fields.String(
        description='Best matching field: number / dn_number / asn_number / cyclecount_number / order_number / '
                    'tracking_number / recipient_name / supplier_name / task_name / goods_code / goods_name / '
                    'location_code / name / organization'),
    'exact': fields.Boolean(description='Full document number (with prefix) or full tracking number matched; '
                                        'such items come first'),
    'highlights': fields.List(fields.Nested(highlight_model),
                              description='Matched ranges in the display texts'),
    'columns': fields.Raw(description='List columns of the type (only when exactly one type is searched)'),
})

search_group_model = api_ns.model('GlobalSearchGroup', {
    'type': fields.String(description='Type', enum=list(SEARCH_TYPES)),
    'total': fields.Integer(description='Number of matches'),
    'page': fields.Integer(description='Page number (1 unless exactly one type is searched)'),
    'per_page': fields.Integer(description='Items per page'),
    'pages': fields.Integer(description='Number of pages'),
    'items': fields.List(fields.Nested(search_item_model)),
})

search_result_model = api_ns.model('GlobalSearchResult', {
    'q': fields.String(description='Keyword actually used (trimmed / cut)'),
    'scope': fields.String(description='warehouse / all', enum=list(SCOPES)),
    'types': fields.List(fields.String, description='Types searched (requested ∩ readable)'),
    'counts': fields.Raw(description='{type: number of matches} for every searched type (0 included)'),
    'exact_matches': fields.List(fields.Nested(search_item_model),
                                 description='Items with exact = true across all groups (show them first)'),
    'groups': fields.List(fields.Nested(search_group_model),
                          description='One group per searched type; groups with an exact match come first'),
})
