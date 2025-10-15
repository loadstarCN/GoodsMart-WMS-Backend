from flask import  g
from flask_restx import Resource,abort
from extensions.error import ForbiddenException, NotFoundException
from system.common import permission_required,paginate
from warehouse.common import warehouse_required,add_warehouse_filter,check_goods_access, check_location_access
from .schemas import (
    api_ns,
    removal_record_model,
    removal_record_input_model,        
    removal_pagination_parser,         
    removal_pagination_model
)
from warehouse.goods.services import GoodsLocationService, GoodsService
from .services import RemovalService  

@api_ns.doc(security="jsonWebToken")
@api_ns.route('/')
class RemovalRecordList(Resource):

    @permission_required(["all_access","company_all_access","removal_read"])
    @warehouse_required()
    @api_ns.expect(removal_pagination_parser)
    @api_ns.marshal_with(removal_pagination_model)
    def get(self):
        """获取所有下架记录（分页）"""
        args = removal_pagination_parser.parse_args()
        page = args.get('page')
        per_page = args.get('per_page')

        # 构造过滤条件
        filters = {
            'goods_id': args.get('goods_id'),
            'location_id': args.get('location_id'),
            'operator_id': args.get('operator_id'),
            'removal_time': args.get('removal_time'),
            'location_code': args.get('location_code'),  
            'goods_code': args.get('goods_code'),        
            'keyword': args.get('keyword'),              
            'warehouse_id': args.get('warehouse_id')     
        }
        filters = add_warehouse_filter(filters)  

        query = RemovalService.list_removal_records(filters)
        return paginate(query, page, per_page)

    @permission_required(["all_access","company_all_access","removal_edit"])
    @warehouse_required()
    @api_ns.expect(removal_record_input_model)
    @api_ns.marshal_with(removal_record_model)
    def post(self):
        """创建新的下架记录"""
        data = api_ns.payload
        created_by = g.current_user.id

        # 需要判断商品和库位是否存在，以及是否是在允许的仓库中
        goods_id = data.get('goods_id')
        location_id = data.get('location_id')        
        if not GoodsLocationService.is_goods_in_location(goods_id, location_id):
            raise NotFoundException("Goods not found in the specified location", 13003)
       
        # 验证员工用户对指定商品的访问权限
        if not check_goods_access(goods_id):
            raise ForbiddenException("You do not have access to this Goods", 12001)

        # 验证员工用户对指定库位的访问权限
        if not check_location_access(location_id):
            raise ForbiddenException("You do not have access to this Location", 12001)

        new_record = RemovalService.create_removal_record(data,created_by)
        return new_record, 201

@api_ns.doc(security="jsonWebToken")
@api_ns.route('/<int:record_id>')
class RemovalRecordDetail(Resource):

    @permission_required(["all_access","company_all_access","removal_read"])
    @api_ns.marshal_with(removal_record_model)
    def get(self, record_id):
        """获取下架记录详情"""
        record = RemovalService.get_removal_record(record_id)
        return record

@api_ns.doc(security="jsonWebToken")
@api_ns.route('/bulk')
class RemovalRecordBulk(Resource):

    @permission_required(["all_access","company_all_access","removal_edit"])
    @warehouse_required()
    @api_ns.expect([removal_record_input_model])
    @api_ns.marshal_with(removal_record_model, as_list=True)
    def post(self):
        """
        批量创建下架记录
        预期接收一个列表，每个元素是 removal_record_input_model 定义的 JSON 对象
        """
        data_list = api_ns.payload  # payload 应该是一个列表
        created_by = g.current_user.id

        # 对每条记录进行基础验证，例如判断商品是否在指定库位中存在及权限验证
        for data in data_list:
            goods_id = data.get('goods_id')
            location_id = data.get('location_id')
            if not GoodsLocationService.is_goods_in_location(goods_id, location_id):
                raise NotFoundException("Goods not found in the specified location", 13003)
            if not check_goods_access(goods_id):
                raise ForbiddenException("You do not have access to this Goods", 12001)
            if not check_location_access(location_id):
                raise ForbiddenException("You do not have access to this Location", 12001)

        new_records = RemovalService.bulk_create_removal_records(data_list, created_by)
        return new_records, 201