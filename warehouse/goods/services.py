from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from flask import g, has_app_context
from extensions import db
from sqlalchemy import or_,func
from collections import defaultdict
from extensions.db import get_object_or_404
from extensions.error import BadRequestException
from extensions.transaction import transactional
from system.webhook.services import emit_company_event
from warehouse.common.countries import normalize_country, is_valid_country
from warehouse.location.models import Location
from .models import Goods, GoodsLocation

# 同步给订阅方的商品规格：模型字段 → payload 键（单位写进键名：重量 kg、尺寸 mm）
SPEC_FIELDS = (
    ('weight', 'goods_weight_kg'),
    ('length', 'goods_length_mm'),
    ('width', 'goods_width_mm'),
    ('height', 'goods_height_mm'),
    ('origin_country', 'goods_origin_country'),
)
# 规格变更来源（goods.spec_updated 的 source）
SPEC_SOURCES = ('station', 'manual', 'import', 'api')
SPEC_UPDATED_EVENT = 'goods.spec_updated'


def clean_origin_country(value, field='origin_country'):
    """原产国入参：None / 空串 = 清空（返回 None）；其余去空白、大写，必须是有效的
    ISO 3166-1 alpha-2 代码，否则 400（10014）。不做任何默认值。"""
    if value is None:
        return None
    if not isinstance(value, str):
        raise BadRequestException(f"{field} must be an ISO 3166-1 alpha-2 country code", 10014, field)
    code = normalize_country(value)
    if not code:
        return None
    if not is_valid_country(code):
        raise BadRequestException(f"Invalid {field}: '{value}' is not an ISO 3166-1 alpha-2 country code",
                                  10014, field)
    return code


def resolve_spec_source(requested=None):
    """规格变更来源：请求体 spec_source（station / manual / import / api）优先；
    没传或不认识的值 → API Key 调用 = api，登录用户（及其他）= manual"""
    if isinstance(requested, str) and requested.strip().lower() in SPEC_SOURCES:
        return requested.strip().lower()
    system = g.get('current_system') if has_app_context() else None
    if system and system.get('api_key') is not None:
        return 'api'
    return 'manual'


def _spec_number(value, exp):
    """按数据库列的精度规范化（重量 3 位小数、尺寸整数），用于前后比较与 payload 输出。
    转不了的值原样返回（交给数据库报错）。"""
    if value is None:
        return None
    try:
        return Decimal(str(value)).quantize(exp, rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError, TypeError):
        return value


def goods_spec_snapshot(goods) -> dict:
    """商品规格快照（payload 形态）：重量 float kg、尺寸 int mm、原产国大写代码；None = 未录入"""
    if goods is None:
        return {}
    weight = _spec_number(goods.weight, Decimal('0.001'))
    snapshot = {'goods_weight_kg': float(weight) if isinstance(weight, Decimal) else weight}
    for attr in ('length', 'width', 'height'):
        value = _spec_number(getattr(goods, attr), Decimal('1'))
        snapshot[f'goods_{attr}_mm'] = int(value) if isinstance(value, Decimal) else value
    snapshot['goods_origin_country'] = goods.origin_country or None
    return snapshot


def _emit_spec_updated(goods, before: dict, source: str):
    """前后快照有差异 → 在当前事务里记 goods.spec_updated（按公司广播，同一商品待发送的只留最新）"""
    after = goods_spec_snapshot(goods)
    changed = [key for _, key in SPEC_FIELDS if before.get(key) != after.get(key)]
    if not changed:
        return
    db.session.flush()  # 新建的商品要先拿到 id
    payload = {
        'goods_code': goods.code,
        'goods_id': goods.id,
        **after,
        'changed_fields': changed,
        'source': source,
        'changed_at': datetime.now().astimezone().isoformat(timespec='seconds'),
    }
    emit_company_event(SPEC_UPDATED_EVENT, payload, goods.company_id, dedupe_key=f'goods:{goods.id}')


class GoodsService:
    """
    A service class that encapsulates various operations
    related to Goods and GoodsLocation.
    """

    @staticmethod
    def _get_instance(goods_or_id: int | Goods) -> Goods:
        """
        根据传入参数返回 Goods 实例。
        如果参数为 int，则调用 get_task 获取 Goods 实例；
        否则直接返回传入的 Goods 实例。
        """
        if isinstance(goods_or_id, int):
            return GoodsService.get_task(goods_or_id)
        return goods_or_id

    @staticmethod
    def list_goods(filters: dict):
        """
        根据过滤条件，返回 Goods 的查询对象。

        :param filters: dict 类型，包含可能的过滤字段
        :return: 一个 SQLAlchemy Query 对象或已经过滤后的结果
        """
        query = Goods.query.order_by(Goods.id.desc())

        if filters.get('code'):
            query = query.filter(Goods.code.ilike(f"%{filters['code']}%"))
        if filters.get('name'):
            query = query.filter(Goods.name.ilike(f"%{filters['name']}%"))
        
        # 如果 filters 中没有 is_active 或其值为 None，则只返回 is_active=True
        if 'is_active' not in filters or filters['is_active'] is None:            
            query = query.filter(Goods.is_active == True)
        else:
            # 否则按用户传入的值进行过滤
            query = query.filter(Goods.is_active == filters['is_active'])
        

        if filters.get('manufacturer'):
            query = query.filter(Goods.manufacturer.ilike(f"%{filters['manufacturer']}%"))
        if filters.get('category'):
            query = query.filter(Goods.category.ilike(f"%{filters['category']}%"))
        if filters.get('tags'):
            tags = [tag.strip() for tag in filters['tags'].split(',')]
            tag_filters = [Goods.tags.ilike(f"%{tag}%") for tag in tags]
            query = query.filter(or_(*tag_filters))
        if filters.get('price_min') is not None:
            query = query.filter(Goods.price >= filters['price_min'])
        if filters.get('price_max') is not None:
            query = query.filter(Goods.price <= filters['price_max'])
        if filters.get('discount_price_min') is not None:
            query = query.filter(Goods.discount_price >= filters['discount_price_min'])
        if filters.get('discount_price_max') is not None:
            query = query.filter(Goods.discount_price <= filters['discount_price_max'])
        if filters.get('currency'):
            query = query.filter(Goods.currency == filters['currency'])
        if filters.get('brand'):
            query = query.filter(Goods.brand.ilike(f"%{filters['brand']}%"))
        if filters.get('expiration_date_min'):
            query = query.filter(Goods.expiration_date >= filters['expiration_date_min'])
        if filters.get('expiration_date_max'):
            query = query.filter(Goods.expiration_date <= filters['expiration_date_max'])
        if filters.get('production_date_min'):
            query = query.filter(Goods.production_date >= filters['production_date_min'])
        if filters.get('production_date_max'):
            query = query.filter(Goods.production_date <= filters['production_date_max'])

        if filters.get('goods_codes'):
            query = query.filter(Goods.code.in_(filters['goods_codes']))
        
        if filters.get('company_id'):
            query = query.filter(Goods.company_id == filters['company_id'])

        # 原产国未录入 / 已录入
        if filters.get('origin_missing') is True:
            query = query.filter(or_(Goods.origin_country.is_(None), Goods.origin_country == ''))
        elif filters.get('origin_missing') is False:
            query = query.filter(Goods.origin_country.isnot(None), Goods.origin_country != '')

        if filters.get('keyword'):
            keyword = filters['keyword']
            query = query.filter(
                or_(
                    Goods.code.ilike(f"%{keyword}%"),
                    Goods.name.ilike(f"%{keyword}%"),
                    Goods.manufacturer.ilike(f"%{keyword}%"),
                    Goods.category.ilike(f"%{keyword}%"),
                    Goods.tags.ilike(f"%{keyword}%"),
                    Goods.brand.ilike(f"%{keyword}%")
                )
            )

        # 商品是公司级主数据，不按仓库过滤：新建但尚无库存的商品也要能被查到（上架前先建档）。
        # 租户隔离由上面的 company_id 过滤保证；filters 里的 warehouse_id(s) 在这里被有意忽略。

        return query


    @staticmethod
    @transactional
    def create_goods(data: dict, created_by_id: int, spec_source: str = None) -> Goods:
        """
        创建一个新的 Goods。

        :param data: Goods 数据
        :param created_by_id: 当前用户 ID
        :param spec_source: 规格来源（station / manual / import / api），缺省按调用方推断
        :return: 新创建的 Goods 对象
        """

        # 假设 data['manufacturing_date'] = "2025-01-01"
        if 'manufacturing_date' in data and isinstance(data['manufacturing_date'], str):
            data['manufacturing_date'] = datetime.strptime(data['manufacturing_date'], '%Y-%m-%d').date()
            
        new_goods = Goods(
            code=data['code'],
            company_id=data['company_id'],
            name=data['name'],
            description=data.get('description'),
            unit=data.get('unit', 'pcs'),
            weight=data.get('weight'),
            length=data.get('length'),
            width=data.get('width'),
            height=data.get('height'),
            origin_country=clean_origin_country(data.get('origin_country')),
            manufacturer=data.get('manufacturer'),
            brand=data.get('brand'),
            image_url=data.get('image_url'),
            thumbnail_url=data.get('thumbnail_url'),
            category=data.get('category'),
            tags=data.get('tags'),
            price=data.get('price'),
            discount_price=data.get('discount_price'),
            currency=data.get('currency', 'JPY'),
            expiration_date=data.get('expiration_date'),
            production_date=data.get('production_date'),
            extra_data=data.get('extra_data'),
            is_active=data.get('is_active', True),
            created_by=created_by_id
        )
        db.session.add(new_goods)
        # 新建即带规格（重量 / 尺寸 / 原产国任一有值）→ 通知订阅方
        _emit_spec_updated(new_goods, {}, spec_source or resolve_spec_source())
        # db.session.commit()
        return new_goods

    @staticmethod
    def get_goods(goods_id: int) -> Goods:
        """
        根据 ID 获取单个 Goods，如不存在则抛出 404
        """
        return get_object_or_404(Goods, goods_id)
    
    @staticmethod
    def get_goods_by_code(code: str,company_id:int) -> Goods:
        """
        根据商品编码获取单个 Goods，如不存在则返回 None
        :param code: 商品编码
        :param company_id: 公司 ID
        :return: Goods 对象或 None
        """
        return Goods.query.filter(Goods.code == code).filter(Goods.company_id == company_id).first()
        

    @staticmethod
    @transactional
    def update_goods(goods_id: int, data: dict, spec_source: str = None) -> Goods:
        """
        更新指定的 Goods 记录。

        :param goods_id: 待更新的 Goods ID
        :param data: 要更新的字段
        :param spec_source: 规格来源（station / manual / import / api），缺省按调用方推断
        :return: 更新后的 Goods 对象
        """
        goods = GoodsService.get_goods(goods_id)
        spec_before = goods_spec_snapshot(goods)
        
        goods.code = data.get('code', goods.code)
        goods.name = data.get('name', goods.name)
        goods.description = data.get('description', goods.description)
        goods.unit = data.get('unit', goods.unit)
        goods.weight = data.get('weight', goods.weight)
        goods.length = data.get('length', goods.length)
        goods.width = data.get('width', goods.width)
        goods.height = data.get('height', goods.height)
        if 'origin_country' in data:
            goods.origin_country = clean_origin_country(data['origin_country'])
        goods.manufacturer = data.get('manufacturer', goods.manufacturer)
        goods.brand = data.get('brand', goods.brand)
        goods.image_url = data.get('image_url', goods.image_url)
        goods.thumbnail_url = data.get('thumbnail_url', goods.thumbnail_url)
        goods.category = data.get('category', goods.category)
        goods.tags = data.get('tags', goods.tags)
        goods.price = data.get('price', goods.price)
        goods.discount_price = data.get('discount_price', goods.discount_price)
        goods.currency = data.get('currency', goods.currency)
        goods.expiration_date = data.get('expiration_date', goods.expiration_date)
        goods.production_date = data.get('production_date', goods.production_date)
        goods.extra_data = data.get('extra_data', goods.extra_data)
        goods.is_active = data.get('is_active', goods.is_active)

        _emit_spec_updated(goods, spec_before, spec_source or resolve_spec_source())

        # db.session.commit()
        return goods

    @staticmethod
    @transactional
    def update_origin_country(goods_id: int, origin_country, spec_source: str = None) -> Goods:
        """
        只改商品原产国（仓库作业端入库 / 打包时看包装「MADE IN」录入）。

        :param origin_country: ISO 3166-1 alpha-2；空串 / None = 清空；不合法 400（10014）
        :param spec_source: 规格来源，缺省按调用方推断
        :return: 更新后的 Goods 对象
        """
        goods = GoodsService.get_goods(goods_id)
        spec_before = goods_spec_snapshot(goods)
        goods.origin_country = clean_origin_country(origin_country)
        _emit_spec_updated(goods, spec_before, spec_source or resolve_spec_source())
        return goods

    @staticmethod
    @transactional
    def delete_goods(goods_id: int):
        """
        删除指定的 Goods。
        """
        goods = GoodsService.get_goods(goods_id)
        db.session.delete(goods)
        # db.session.commit()

    @staticmethod
    @transactional
    def bulk_create_goods(data: list, created_by_id: int, override_mode: str = 'skip',
                          spec_source: str = 'import') -> list:
        """
        四策略批量创建逻辑[6,7](@ref)
        :param override_mode: 处理策略（skip|active|append|override）
        :param spec_source: 规格变更来源（goods.spec_updated 的 source），CSV 导入为 import

        原产国（origin_country）对已有商品：append 只补空白；override 只在导入值非空时覆盖——
        导入永远不会清空已录入的原产国（清空只能在商品编辑里手工做）。
        """
        new_goods = []
        # 可更新字段白名单（排除code/company_id）
        updatable_fields = {
            'name', 'manufacturer', 'brand', 'price',
            'production_date', 'currency', 'unit',
            'description', 'image_url', 'thumbnail_url'
        }

        # 批量预查询已存在商品，避免循环内 N+1 查询
        lookup_keys = {(d['code'], d['company_id']) for d in data}
        codes = [k[0] for k in lookup_keys]
        company_ids = [k[1] for k in lookup_keys]
        existing_goods_list = Goods.query.filter(
            Goods.code.in_(codes),
            Goods.company_id.in_(company_ids)
        ).all()
        existing_map = {(g.code, g.company_id): g for g in existing_goods_list}

        for goods_data in data:
            existing = existing_map.get((goods_data['code'], goods_data['company_id']))

            if not existing:
                # 新增记录逻辑
                new_goods.append(GoodsService.create_goods(goods_data, created_by_id, spec_source=spec_source))
                continue
                
            # 策略处理分支
            if override_mode == 'skip':
                continue  # 完全跳过不处理
                
            elif override_mode == 'active':
                if not existing.is_active:
                    existing.is_active = True
                    existing.updated_at = datetime.now()
                    db.session.add(existing)
                new_goods.append(existing)
                
            elif override_mode == 'append':
                spec_before = goods_spec_snapshot(existing)
                for field in updatable_fields:
                    new_val = goods_data.get(field)
                    current_val = getattr(existing, field)
                    # 仅当原字段为空且新值不为空时更新
                    # 复合空值判断（支持None和空字符串）
                    is_current_empty = current_val in (None, "")  
                    is_new_valid = new_val not in (None, "")      
                    
                    # 仅当原字段为空且新值有效时更新
                    if is_current_empty and is_new_valid:
                        setattr(existing, field, new_val)
                origin_country = clean_origin_country(goods_data.get('origin_country'))
                if origin_country and not existing.origin_country:
                    existing.origin_country = origin_country
                _emit_spec_updated(existing, spec_before, spec_source)
                existing.is_active = True
                existing.updated_at = datetime.now()
                db.session.add(existing)
                new_goods.append(existing)
                
            elif override_mode == 'override':
                # 全量字段覆盖
                spec_before = goods_spec_snapshot(existing)
                for field in updatable_fields:
                    setattr(existing, field, goods_data.get(field))
                origin_country = clean_origin_country(goods_data.get('origin_country'))
                if origin_country:
                    existing.origin_country = origin_country
                _emit_spec_updated(existing, spec_before, spec_source)
                existing.is_active = True
                existing.updated_at = datetime.now()
                db.session.add(existing)
                new_goods.append(existing)

        return new_goods


class GoodsLocationService:
    """
    A service class that encapsulates operations related to GoodsLocation.
    """

    @staticmethod
    def list_goods_locations(filters: dict):
        """
        获取所有符合过滤条件的 GoodsLocation。

        :param filters: dict 类型，包含可能的过滤字段
        :return: SQLAlchemy 查询对象，已根据过滤条件进行筛选
        """
        query = GoodsLocation.query

        # 根据过滤条件筛选
        if filters.get('goods_id'):
            query = query.filter(GoodsLocation.goods_id == filters['goods_id'])
        if filters.get('goods_ids'):
            query = query.filter(GoodsLocation.goods_id.in_(filters['goods_ids']))
        if filters.get('location_id'):
            query = query.filter(GoodsLocation.location_id == filters['location_id'])

        # 针对 Goods 相关的过滤，先统一 join 一次
        if filters.get('goods_code') or filters.get('goods_name') or filters.get('keyword'):
            query = query.join(Goods, Goods.id == GoodsLocation.goods_id)
        if filters.get('goods_code'):
            query = query.filter(Goods.code.ilike(f"%{filters['goods_code']}%"))
        if filters.get('goods_name'):
            query = query.filter(Goods.name.ilike(f"%{filters['goods_name']}%"))
        if filters.get('quantity_min'):
            query = query.filter(GoodsLocation.quantity >= filters['quantity_min'])
        if filters.get('quantity_max'):
            query = query.filter(GoodsLocation.quantity <= filters['quantity_max'])

        # 针对 Location 相关的过滤，先统一 join 一次
        if filters.get('location_code') or filters.get('warehouse_id') or filters.get('warehouse_ids') or filters.get('keyword'):
            query = query.join(Location, GoodsLocation.location_id == Location.id)
        if filters.get('location_code'):
            query = query.filter(Location.code.ilike(f"%{filters['location_code']}%"))

        if filters.get('keyword'):
            keyword = filters['keyword']
            query = query.filter(
                or_(
                    Goods.code.ilike(f"%{keyword}%"),
                    Goods.name.ilike(f"%{keyword}%"),
                    Goods.manufacturer.ilike(f"%{keyword}%"),
                    Goods.category.ilike(f"%{keyword}%"),
                    Goods.tags.ilike(f"%{keyword}%"),
                    Goods.brand.ilike(f"%{keyword}%"),
                    Location.code.ilike(f"%{keyword}%")
                )
            )

        if filters.get('warehouse_id'):
            query = query.filter(Location.warehouse_id == filters['warehouse_id'])
        if filters.get('warehouse_ids'):
            query = query.filter(Location.warehouse_id.in_(filters['warehouse_ids']))       

        return query

    @staticmethod
    @transactional
    def create_goods_location(data: dict) -> GoodsLocation:
        """
        创建新的 GoodsLocation。

        :param data: 包含 GoodsLocation 数据的字典
        :return: 新创建的 GoodsLocation 对象
        """
        new_goods_location = GoodsLocation(
            goods_id=data['goods_id'],
            location_id=data['location_id'],
            quantity=data.get('quantity', 0)
        )
        db.session.add(new_goods_location)
        # db.session.commit()
        return new_goods_location

    @staticmethod
    def get_goods_location(goods_location_id: int) -> GoodsLocation:
        """
        根据 ID 获取单个 GoodsLocation，如不存在则抛出 404。

        :param goods_location_id: GoodsLocation 的 ID
        :return: GoodsLocation 对象
        """        
        return get_object_or_404(GoodsLocation, goods_location_id)

    @staticmethod
    @transactional
    def update_goods_location(goods_location_id: int, data: dict) -> GoodsLocation:
        """
        更新指定的 GoodsLocation 记录。

        :param goods_location: 需要更新的 GoodsLocation 对象
        :param data: 要更新的字段数据
        :return: 更新后的 GoodsLocation 对象
        """
        goods_location = GoodsLocationService.get_goods_location(goods_location_id)
        goods_location.quantity = data.get('quantity', goods_location.quantity)
        # db.session.commit()
        return goods_location

    @staticmethod
    @transactional
    def delete_goods_location(goods_location_id: int):
        """
        删除指定的 GoodsLocation。

        :param goods_location: 需要删除的 GoodsLocation 对象
        """
        goods_location = GoodsLocationService.get_goods_location(goods_location_id)
        db.session.delete(goods_location)
        # db.session.commit()

    @staticmethod
    def get_quantity_by_location_type(goods_id: int, warehouse_id: int):
        """
        根据商品 ID 和仓库 ID 获取各位置类型的库存数量汇总

        :param goods_id: 商品 ID
        :param warehouse_id: 仓库 ID（在 Location 模型中）
        :return: 包含 'standard', 'damaged', 'return' 三种位置类型库存数量的字典，
                如果某类型没有记录则返回 0
        """
        # 构造基础查询：关联 Location 与 GoodsLocation，并在过滤条件中使用 Location.warehouse_id
        query = db.session.query(
            Location.location_type,
            func.sum(GoodsLocation.quantity).label('total_quantity')
        ).join(
            GoodsLocation, GoodsLocation.location_id == Location.id
        ).filter(
            Location.location_type.in_(['standard', 'damaged', 'return']),
            Location.is_active == True,
            GoodsLocation.goods_id == goods_id,
            Location.warehouse_id == warehouse_id
        )
        
        # 进行分组并执行查询
        result = query.group_by(Location.location_type).all()

        # 使用 defaultdict 初始化默认值为 0
        quantity_by_type = defaultdict(int)

        # 将查询结果更新到字典中
        for location_type, total_quantity in result:
            quantity_by_type[location_type] = total_quantity

        # 确保返回的字典包含 'standard', 'damaged', 'return'，即使没有记录也返回 0
        return {location_type: quantity_by_type[location_type] for location_type in ['standard', 'damaged', 'return']}

    @staticmethod
    def get_goods_location_record(goods_id: int, location_id: int) -> GoodsLocation:
        """
        获取指定商品在指定库位上的库存记录

        :param goods_id: 商品 ID
        :param location_id: 库位 ID
        :return: GoodsLocation 对象
        """
        return GoodsLocation.query.filter_by(
            goods_id=goods_id,
            location_id=location_id
        ).first()
    

    @staticmethod
    def get_quantity(goods_id:int,location_id:int) -> int:
        """
        获取商品数量：根据库存记录获取指定商品在指定库位的总库存量
        """
        # 使用 SQLAlchemy 的 sum 函数对 quantity 进行聚合
        total_quantity = GoodsLocation.query.with_entities(
            func.sum(GoodsLocation.quantity).label('total')
        ).filter_by(
            goods_id=goods_id,
            location_id=location_id
        ).scalar()  # scalar() 返回单个标量值

        # 处理查询结果为 None 的情况（当没有库存记录时返回 0）
        return total_quantity if total_quantity is not None else 0
    
    @staticmethod
    def is_goods_in_location(goods_id:int,location_id:int) -> bool:
        """
        判断指定商品是否在指定库位上
        """
        return GoodsLocation.query.filter_by(
            goods_id=goods_id,
            location_id=location_id
        ).count() > 0
    