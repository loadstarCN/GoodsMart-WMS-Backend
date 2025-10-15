from flask import g
from flask_restx import Resource,abort
from extensions.error import ForbiddenException
from system.common import permission_required
from warehouse.common import warehouse_required,add_warehouse_filter,check_goods_access, check_location_access

from .schemas import (
    api_ns, 
    putaway_record_model, 
    putaway_record_input_model, 
    putaway_pagination_parser,
    putaway_pagination_model
)
from system.common import paginate
from .services import PutawayService
from warehouse.goods.services import GoodsService
from warehouse.location.models import Location

@api_ns.doc(security="jsonWebToken")
@api_ns.route('/')
class PutawayRecordList(Resource):

    @permission_required(["all_access","company_all_access","putaway_read"])
    @warehouse_required()
    @api_ns.expect(putaway_pagination_parser)
    @api_ns.marshal_with(putaway_pagination_model)
    def get(self):
        """获取所有上架记录"""
        args = putaway_pagination_parser.parse_args()
        page = args.get('page')
        per_page = args.get('per_page')

        # 构造过滤条件
        filters = {
            'goods_id': args.get('goods_id'),
            'location_id': args.get('location_id'),
            'operator_id': args.get('operator_id'),
            'start_time': args.get('start_time'),
            'end_time': args.get('end_time'),
            'location_code': args.get('location_code'),
            'goods_code': args.get('goods_code'),
            'keyword': args.get('keyword'),
        }
        filters = add_warehouse_filter(filters)

        query = PutawayService.list_putaway_records(filters)
        return paginate(query, page, per_page)

    @permission_required(["all_access","company_all_access","putaway_edit"])
    @warehouse_required()
    @api_ns.expect(putaway_record_input_model)
    @api_ns.marshal_with(putaway_record_model)
    def post(self):
        """创建新上架记录"""
        data = api_ns.payload
        created_by = g.current_user.id

        # 需要判断商品和库位是否存在，以及是否是在允许的仓库中
        goods_id = data.get('goods_id')
        location_id = data.get('location_id')

        # 验证员工用户对指定商品的访问权限
        if not check_goods_access(goods_id):
            raise ForbiddenException("You do not have access to this Goods", 12001)
        
        # 验证员工用户对指定库位的访问权限
        if not check_location_access(location_id):
            raise ForbiddenException("You do not have access to this Location", 12001)
    
        new_record = PutawayService.create_putaway_record(data,created_by)
        return new_record, 201


@api_ns.doc(security="jsonWebToken")
@api_ns.route('/<int:record_id>')
class PutawayRecordDetail(Resource):

    @permission_required(["all_access","company_all_access","putaway_read"])
    @api_ns.marshal_with(putaway_record_model)
    def get(self, record_id):
        """获取上架记录详情"""
        record = PutawayService.get_putaway_record(record_id)
        return record


@api_ns.doc(security="jsonWebToken")
@api_ns.route('/bulk')
class PutawayRecordBulk(Resource):
    
    @permission_required(["all_access","company_all_access","putaway_edit"])
    @warehouse_required()
    @api_ns.expect([putaway_record_input_model])
    @api_ns.marshal_with(putaway_record_model, as_list=True)
    def post(self):
        """
        批量创建上架记录
        预期接收一个列表，每个元素是 putaway_record_input_model 定义的 JSON 对象
        """
        data_list = api_ns.payload  # 预期 payload 是一个列表
        created_by = g.current_user.id

        # 验证每个记录的商品和库位访问权限
        for data in data_list:
            goods_id = data.get('goods_id')
            location_id = data.get('location_id')
            if not check_goods_access(goods_id):
                raise ForbiddenException("You do not have access to this Goods", 12001)
            if not check_location_access(location_id):
                raise ForbiddenException("You do not have access to this Location", 12001)

        new_records = PutawayService.bulk_create_putaway_records(data_list, created_by)
        return new_records, 201