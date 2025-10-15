from datetime import datetime
from extensions import db
from sqlalchemy import or_,func
from collections import defaultdict
from extensions.db import get_object_or_404
from extensions.transaction import transactional
from warehouse.inventory.models import Inventory
from warehouse.location.models import Location
from .models import Goods, GoodsLocation

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

        # 使用 Inventory 模型中的 warehouse_id 进行过滤
        # if filters.get('warehouse_id'):
        #     query = query.join(Inventory, Goods.id == Inventory.goods_id) \
        #                 .filter(Inventory.warehouse_id == filters['warehouse_id'])
            
        # if filters.get('warehouse_ids'):
        #     query = query.join(Inventory, Goods.id == Inventory.goods_id) \
        #                 .filter(Inventory.warehouse_id.in_(filters['warehouse_ids'])).distinct(Goods.id)

        return query


    @staticmethod
    @transactional
    def create_goods(data: dict, created_by_id: int) -> Goods:
        """
        创建一个新的 Goods。

        :param data: Goods 数据
        :param created_by_id: 当前用户 ID
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
    def update_goods(goods_id: int, data: dict) -> Goods:
        """
        更新指定的 Goods 记录。

        :param goods_id: 待更新的 Goods ID
        :param data: 要更新的字段
        :return: 更新后的 Goods 对象
        """
        goods = GoodsService.get_goods(goods_id)
        
        goods.code = data.get('code', goods.code)
        goods.name = data.get('name', goods.name)
        goods.description = data.get('description', goods.description)
        goods.unit = data.get('unit', goods.unit)
        goods.weight = data.get('weight', goods.weight)
        goods.length = data.get('length', goods.length)
        goods.width = data.get('width', goods.width)
        goods.height = data.get('height', goods.height)
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

        # db.session.commit()
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
    def bulk_create_goods(data: list, created_by_id: int, override_mode: str = 'skip') -> list:
        """
        四策略批量创建逻辑[6,7](@ref)
        :param override_mode: 处理策略（skip|active|append|override）
        """
        new_goods = []
        # 可更新字段白名单（排除code/company_id）
        updatable_fields = {
            'name', 'manufacturer', 'brand', 'price',
            'production_date', 'currency', 'unit',
            'description', 'image_url', 'thumbnail_url'
        }

        for goods_data in data:
            existing = Goods.query.filter(
                Goods.code == goods_data['code'],
                Goods.company_id == goods_data['company_id']
            ).first()

            if not existing:
                # 新增记录逻辑
                new_goods.append(GoodsService.create_goods(goods_data, created_by_id))                
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
                existing.is_active = True
                existing.updated_at = datetime.now()
                db.session.add(existing)
                new_goods.append(existing)
                
            elif override_mode == 'override':
                # 全量字段覆盖
                for field in updatable_fields:
                    setattr(existing, field, goods_data.get(field))
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
    