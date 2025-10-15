from flask import g
from flask_restx import Resource, abort
from extensions.error import BadRequestException, ForbiddenException, NotFoundException
from system.common import permission_required
from warehouse.common import warehouse_required,add_warehouse_filter,check_goods_access, check_location_access

from .schemas import (
    api_ns, 
    transfer_record_model, 
    transfer_record_input_model, 
    transfer_pagination_parser,
    transfer_pagination_model
)
from system.common import paginate
from .services import TransferService
from warehouse.goods.services import GoodsLocationService, GoodsService
from warehouse.location.models import Location

@api_ns.doc(security="jsonWebToken")
@api_ns.route('/')
class TransferRecordList(Resource):
    
    @permission_required(["all_access","company_all_access","transfer_read"])
    @api_ns.expect(transfer_pagination_parser)
    @api_ns.marshal_with(transfer_pagination_model)
    def get(self):
        """获取所有移库记录"""
        args = transfer_pagination_parser.parse_args()
        page = args.get('page')
        per_page = args.get('per_page')

        # 构造过滤条件
        filters = {
            'goods_id': args.get('goods_id'),
            'from_location_id': args.get('from_location_id'),
            'to_location_id': args.get('to_location_id'),
            'operator_id': args.get('operator_id'),
            'location_id': args.get('location_id'),
            'start_time': args.get('start_time'),
            'end_time': args.get('end_time'),
            'from_location_code': args.get('from_location_code'),
            'to_location_code': args.get('to_location_code'),
            'goods_code': args.get('goods_code'),
            'keyword': args.get('keyword'),
        }

        filters = add_warehouse_filter(filters)

        query = TransferService.list_transfer_records(filters)
        return paginate(query, page, per_page)

    @permission_required(["all_access","company_all_access","transfer_edit"])
    @warehouse_required()
    @api_ns.expect(transfer_record_input_model)
    @api_ns.marshal_with(transfer_record_model)
    def post(self):
        """创建新移库记录"""
        data = api_ns.payload
        created_by = g.current_user.id

        # 需要判断商品和库位是否存在，以及是否是在允许的仓库中
        goods_id = data.get('goods_id')
        from_location_id = data.get('from_location_id')
        to_location_id = data.get('to_location_id')

        # 判断form_location_id是否和to_location_id相同
        if from_location_id == to_location_id:
            raise BadRequestException("The from_location_id and to_location_id cannot be the same", 14006)

        if not GoodsLocationService.is_goods_in_location(goods_id, from_location_id):
            raise NotFoundException("Goods not found in the specified from_location", 14003)
       
        # 验证员工用户对指定商品的访问权限
        if not check_goods_access(goods_id):
            raise ForbiddenException("You do not have access to this Goods", 12001)
        
        # 验证员工用户对指定库位的访问权限
        if not check_location_access(from_location_id):
            raise ForbiddenException("You do not have access to this Location", 12001)
        
        if not check_location_access(to_location_id):
            raise ForbiddenException("You do not have access to this Location", 12001)

        new_record = TransferService.create_transfer_record(data,created_by)
        return new_record, 201


@api_ns.doc(security="jsonWebToken")
@api_ns.route('/<int:record_id>')
class TransferRecordDetail(Resource):

    @permission_required(["all_access","company_all_access","transfer_read"])
    @api_ns.marshal_with(transfer_record_model)
    def get(self, record_id):
        """获取移库记录详情"""
        record = TransferService.get_transfer_record(record_id)
        return record


@api_ns.doc(security="jsonWebToken")
@api_ns.route('/bulk')
class TransferRecordBulk(Resource):

    @permission_required(["all_access", "company_all_access", "transfer_edit"])
    @warehouse_required()
    @api_ns.expect([transfer_record_input_model])
    @api_ns.marshal_with(transfer_record_model, as_list=True)
    def post(self):
        """
        批量创建移库记录
        预期接收一个列表，每个元素为 transfer_record_input_model 定义的 JSON 对象
        """
        data_list = api_ns.payload  # payload 预期为列表
        created_by = g.current_user.id

        # 对每条记录进行基础验证，例如验证商品和库位的存在及访问权限
        for data in data_list:
            goods_id = data.get('goods_id')
            from_location_id = data.get('from_location_id')
            to_location_id = data.get('to_location_id')
            # 判断from_location_id是否和to_location_id相同
            if from_location_id == to_location_id:
                raise BadRequestException("The from_location_id and to_location_id cannot be the same", 14006)

            if not GoodsLocationService.is_goods_in_location(goods_id, from_location_id):
                raise NotFoundException("Goods not found in the specified from_location", 14003)
            
            # 验证对指定商品和库位的访问权限
            if not check_goods_access(goods_id):
                raise ForbiddenException("You do not have access to this Goods", 12001)
            if not check_location_access(from_location_id):
                raise ForbiddenException("You do not have access to this From Location", 12001)
            if not check_location_access(to_location_id):
                raise ForbiddenException("You do not have access to this To Location", 12001)
        
        new_records = TransferService.bulk_create_transfer_records(data_list, created_by)
        return new_records, 201