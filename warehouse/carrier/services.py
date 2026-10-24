from extensions.db import *
from extensions.transaction import transactional
from warehouse.common.references import ensure_not_referenced
from . import file_settings
from .methods import parse_settings
from .models import Carrier


class CarrierService:

    @staticmethod
    def _get_instance(carrier_or_id: int | Carrier) -> Carrier:
        """
        根据传入参数返回 Carrier 实例。
        如果参数为 int，则调用 get_carrier 获取 Carrier 实例；
        否则直接返回传入的 Carrier 实例。
        """
        if isinstance(carrier_or_id, int):
            return CarrierService.get_carrier(carrier_or_id)
        return carrier_or_id

    @staticmethod
    def list_carriers(filters: dict):
        """
        根据过滤条件返回 Carrier 查询对象
        """
        query = Carrier.query.order_by(Carrier.id.desc())

        if filters.get('company_id'):
            query = query.filter(Carrier.company_id == filters['company_id'])
        if filters.get('name'):
            query = query.filter(Carrier.name.ilike(f"%{filters['name']}%"))

        if 'is_active' not in filters or filters['is_active'] is None:            
            query = query.filter(Carrier.is_active == True)
        else:
            # 否则按用户传入的值进行过滤
            query = query.filter(Carrier.is_active == filters['is_active'])

        return query

    @staticmethod
    def get_carrier(carrier_id: int) -> Carrier:
        """
        根据 ID 获取单个 Carrier，不存在时抛出 404
        """
        carrier = get_object_or_404(Carrier, carrier_id)
        return carrier

    @staticmethod
    @transactional
    def create_carrier(data: dict, created_by_id: int) -> Carrier:
        """
        创建新 Carrier。对接方式等（waybill_method / pickup_method / tracking_method / pickup_schedule /
        pickup_contact / tracking_url_template / auto_sign_days）按 warehouse/carrier/methods.py 校验（400 16135）；
        新建时还没有在线对接，api 方式不能选。文件方式（file_preset / file_template / file_account）按
        warehouse/carrier/file_settings.py 校验（400 16135）；同一请求里设了预设就可以选 waybill_method = file。
        """
        files = file_settings.parse(data, None)
        methods = parse_settings(data, None, file_configured=files['template'] is not None)
        new_carrier = Carrier(
            name=data['name'],
            code=data.get('code'),
            address=data.get('address'),
            phone=data.get('phone'),
            zip_code=data.get('zip_code'),
            email=data.get('email'),
            contact=data.get('contact'),
            company_id=data['company_id'],
            is_active=data.get('is_active', True),
            created_by=created_by_id,
            **methods,
            **files['values'],
        )
        db.session.add(new_carrier)
        # db.session.commit()
        return new_carrier

    @staticmethod
    @transactional
    def update_carrier(carrier_id: int, data: dict) -> Carrier:
        """
        更新 Carrier 信息。对接方式等字段只改请求里带了的；改成不可选的方式（如对接没启用却选 api）→ 400 16135
        （field 指明哪一项），没改的方式不重新校验。
        """
        carrier = CarrierService.get_carrier(carrier_id)
        files = file_settings.parse(data, carrier)
        methods = parse_settings(data, carrier, file_configured=files['template'] is not None)

        carrier.name = data.get('name', carrier.name)
        carrier.code = data.get('code', carrier.code)
        carrier.address = data.get('address', carrier.address)
        carrier.phone = data.get('phone', carrier.phone)
        carrier.zip_code = data.get('zip_code', carrier.zip_code)
        carrier.email = data.get('email', carrier.email)
        carrier.contact = data.get('contact', carrier.contact)
        carrier.is_active = data.get('is_active', carrier.is_active)
        for field, value in {**methods, **files['values']}.items():
            setattr(carrier, field, value)
        # company_id 不可通过更新修改（归属固定）

        # db.session.commit()
        return carrier

    @staticmethod
    @transactional
    def delete_carrier(carrier_id: int):
        """
        删除 Carrier：仍被 ASN / DN / 发货任务 / 收款等引用时 409 44004（改用停用）
        """
        carrier = CarrierService.get_carrier(carrier_id)
        ensure_not_referenced(Carrier, carrier.id, 'carrier')
        db.session.delete(carrier)
        # db.session.commit()
