import datetime
import os
import sys
import pytest
from flask import Flask
from flask_restx import Api
from flask_jwt_extended import JWTManager, create_access_token
import uuid
# 扩展和系统工具
from extensions import db, error, redis_client, limiter
from system.third_party.models import APIKey
from system.user.models import Permission, User, Role
from system.logs.models import ActivityLog
from system.limiter.models import IPBlacklist, IPWhitelist
from system.third_party.utils import validate_jwt_and_api_key

# Warehouse 模块 - 模型
from warehouse.asn.models import ASN, ASNDetail
from warehouse.carrier.models import Carrier
from warehouse.company.models import Company
from warehouse.cyclecount.models import CycleCountTask, CycleCountTaskDetail
from warehouse.delivery.models import DeliveryTask
from warehouse.department.models import Department
from warehouse.dn.models import DN, DNDetail
from warehouse.inventory.models import Inventory
from warehouse.inventory.services import InventoryService
from warehouse.location.models import Location
from warehouse.packing.models import PackingBatch, PackingTask, PackingTaskDetail
from warehouse.picking.models import PickingBatch, PickingTask, PickingTaskDetail
from warehouse.putaway.models import PutawayRecord
from warehouse.recipient.models import Recipient
from warehouse.sorting.models import (
    SortingTask,
    SortingTaskDetail,
    SortingBatch,
    SortingTaskStatusLog
)
from warehouse.staff.models import Staff
from warehouse.supplier.models import Supplier
from warehouse.transfer.models import TransferRecord
from warehouse.adjustment.models import Adjustment, AdjustmentDetail
from warehouse.goods.models import Goods, GoodsLocation
from warehouse.warehouse.models import Warehouse
from warehouse.removal.models import RemovalRecord

# Warehouse 模块 - API 命名空间（Schemas）
from warehouse.company.schemas import api_ns as company_ns
from warehouse.department.schemas import api_ns as department_ns
from warehouse.staff.schemas import api_ns as staff_ns
from warehouse.warehouse.schemas import api_ns as warehouse_ns
from warehouse.location.schemas import api_ns as location_ns
from warehouse.supplier.schemas import api_ns as supplier_ns
from warehouse.recipient.schemas import api_ns as recipient_ns
from warehouse.carrier.schemas import api_ns as carrier_ns
from warehouse.goods.schemas import api_ns as goods_ns
from warehouse.inventory.schemas import api_ns as inventory_ns
from warehouse.asn.schemas import api_ns as asn_ns
from warehouse.sorting.schemas import api_ns as sorting_ns
from warehouse.putaway.schemas import api_ns as putaway_ns
from warehouse.removal.schemas import api_ns as removal_ns
from warehouse.dn.schemas import api_ns as dn_ns
from warehouse.picking.schemas import api_ns as picking_ns
from warehouse.packing.schemas import api_ns as packing_ns
from warehouse.delivery.schemas import api_ns as delivery_ns
from warehouse.cyclecount.schemas import api_ns as cyclecount_ns
from warehouse.adjustment.schemas import api_ns as adjustment_ns
from warehouse.transfer.schemas import api_ns as transfer_ns
from warehouse.payment.schemas import api_ns as payment_ns
from warehouse.common import extract_warehouse_id
from system.user.schemas import api_ns as user_ns
from tasks.views import api_ns as task_ns
from system.logs.schemas import api_ns as logs_ns
from system.limiter.schemas import api_ns as limiter_ns
from system.third_party.schemas import api_ns as third_party_ns

# 添加父目录到 Python 路径，以便导入 config
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))

# 导入配置类
from config import TestingConfig

def setup_app():
    app = Flask(__name__)
    config = TestingConfig()

    # 应用配置
    app.config.from_object(config)
    # 确保测试环境使用内存数据库
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///:memory:'

    db.init_app(app)
    jwt = JWTManager(app)
    redis_client.init_app(app)
    limiter.init_app(app)

    # 注册 JWT 回调
    @jwt.user_identity_loader
    def user_identity_lookup(user):
        """定义如何将用户对象序列化到 JWT 中"""
        return str(user.id)  # 强制转换为字符串

    @jwt.user_lookup_loader
    def user_lookup_callback(_jwt_header, jwt_data):
        """根据 JWT 数据加载用户"""
        identity = jwt_data.get("sub")
        if identity:
            return db.session.get(User, identity)
    # 注册 JWT and API Key 验证逻辑
    app.before_request(validate_jwt_and_api_key)


    # 注册仓库 ID 加载逻辑
    app.before_request(extract_warehouse_id)

    error.register_error_handlers(app)    


    api = Api(app)
    # 注册所有模块的命名空间及对应 path
    api.add_namespace(company_ns, path='/company')
    api.add_namespace(department_ns, path='/department')
    api.add_namespace(warehouse_ns, path='/warehouse')
    api.add_namespace(location_ns, path='/location')
    api.add_namespace(staff_ns, path='/staff')
    api.add_namespace(supplier_ns, path='/supplier')
    api.add_namespace(recipient_ns, path='/recipient')
    api.add_namespace(carrier_ns, path='/carrier')
    api.add_namespace(goods_ns, path='/goods')
    api.add_namespace(inventory_ns, path='/inventory')
    api.add_namespace(asn_ns, path='/asn')
    api.add_namespace(sorting_ns, path='/sorting')
    api.add_namespace(putaway_ns, path='/putaway')
    api.add_namespace(removal_ns, path='/removal')
    api.add_namespace(dn_ns, path='/dn')
    api.add_namespace(picking_ns, path='/picking')
    api.add_namespace(packing_ns, path='/packing')
    api.add_namespace(delivery_ns, path='/delivery')
    api.add_namespace(cyclecount_ns, path='/cyclecount')
    api.add_namespace(adjustment_ns, path='/adjustment')
    api.add_namespace(transfer_ns, path='/transfer')
    api.add_namespace(payment_ns, path='/payment')
    api.add_namespace(user_ns, path='/user')
    api.add_namespace(logs_ns, path='/logs')
    api.add_namespace(limiter_ns, path='/ip-lists')
    api.add_namespace(third_party_ns, path='/api-keys')
    api.add_namespace(task_ns, path='/task')

    return app


def init_test_data(app):
    """
    在 app 上下文中创建所有的模拟数据。
    """
    with app.app_context():
        db.create_all()

        # 创建权限
        all_access_permission = Permission(name='all_access', description='All access permission')
        company_all_access_permission = Permission(name='company_all_access', description='Company all access permission')
        warehouse_all_access_permission = Permission(name='warehouse_all_access', description='Warehouse all access permission')
        operator_permission = Permission(name='operator', description='Operator permission')
        db.session.add_all([all_access_permission, company_all_access_permission, warehouse_all_access_permission, operator_permission])
        db.session.commit()

        # 创建角色，并绑定权限
        admin_role = Role(name='admin', description='Administrator', is_active=True)
        company_admin_role = Role(name='company_admin', description='公司管理员', is_active=True)
        warehouse_admin_role = Role(name='warehouse_admin', description='仓库管理员', is_active=True)
        operator_role = Role(name='warehouse_operator', description='操作员', is_active=True)
        admin_role.permissions.append(all_access_permission)
        company_admin_role.permissions.append(company_all_access_permission)
        warehouse_admin_role.permissions.append(warehouse_all_access_permission)
        operator_role.permissions.append(operator_permission)
        db.session.add_all([admin_role, company_admin_role, warehouse_admin_role, operator_role])
        db.session.commit()

        # 创建 admin 用户
        admin_user = User(
            user_name='admin',
            email='admin@example.com',
            is_active=True
        )
        admin_user.set_password('password')
        admin_user.roles.append(admin_role)
        db.session.add(admin_user)
        db.session.commit()

        # 添加公司和仓库
        company = Company(
            name="Company A",
            email="a@company.com",
            phone="123456789",
            address="123 Street",
            created_by=admin_user.id
        )
        company2 = Company(
            name="Company B",
            email="b@company.com",
            phone="123456789",
            address="123 Street",
            created_by=admin_user.id
        )
        db.session.add_all([company, company2])
        db.session.commit()

        department = Department(
            name="HR",
            company_id=company.id,
            description="Human Resources",
            created_by=admin_user.id
        )
        db.session.add(department)
        db.session.commit()

        warehouse = Warehouse(
            name="Warehouse A",
            address="Warehouse Address A",
            phone="123456789",
            zip_code="12345",
            company_id=company.id,
            created_by=admin_user.id
        )
        warehouse2 = Warehouse(
            name="Warehouse Test",
            address="Warehouse Address B",
            phone="123456789",
            zip_code="12345",
            company_id=company.id,
            created_by=admin_user.id
        )
        db.session.add_all([warehouse, warehouse2])
        db.session.commit()

        # 创建 Staff 用户
        company_admin = Staff(
            user_name='company_admin',
            email='company_admin@test.com',
            is_active=True,
            company_id=company.id,
            created_by=admin_user.id
        )
        company_admin.set_password('password')
        company_admin.roles.append(company_admin_role)
        company_admin.warehouses.append(warehouse)
        db.session.add(company_admin)
        db.session.commit()

        warehouse_admin = Staff(
            user_name='warehouse_admin',
            email='warehouse_admin@test.com',
            is_active=True,
            company_id=company.id,
            created_by=admin_user.id
        )
        warehouse_admin.set_password('password')
        warehouse_admin.roles.append(warehouse_admin_role)
        warehouse_admin.warehouses.append(warehouse)
        db.session.add(warehouse_admin)
        db.session.commit()

        operator = Staff(
            user_name='operator',
            email='operator@test.com',
            is_active=True,
            company_id=company.id,
            created_by=admin_user.id
        )
        operator.set_password('password')
        operator.roles.append(operator_role)
        operator.warehouses.append(warehouse)
        db.session.add(operator)
        db.session.commit()

        # 创建 Goods 和 Locations
        location_1 = Location(
            warehouse_id=warehouse.id,
            code="LOC001",
            description="Sample Location",
            location_type="standard",
            created_by=admin_user.id
        )
        location_2 = Location(
            warehouse_id=warehouse.id,
            code="LOC002",
            description="Sample Location",
            location_type="standard",
            created_by=admin_user.id
        )
        location_3 = Location(
            warehouse_id=warehouse.id,
            code="LOC003",
            description="Sample Location",
            location_type="standard",
            created_by=admin_user.id
        )

        goods_1 = Goods(
            code="G001",
            company_id=company.id,
            name="Sample Goods",
            description="This is a sample goods",
            unit="pcs",
            weight=1.2,
            length=10.0,
            width=5.0,
            height=2.0,
            manufacturer="Sample Manufacturer",
            is_active=True,
            created_by=admin_user.id
        )
        goods_2 = Goods(
            code="G002",
            company_id=company.id,
            name="Sample Goods",
            description="This is another sample goods",
            unit="pcs",
            weight=1.2,
            length=10.0,
            width=5.0,
            height=2.0,
            manufacturer="Sample Manufacturer",
            is_active=True,
            created_by=admin_user.id
        )

        db.session.add_all([location_1, location_2,location_3, goods_1, goods_2])
        db.session.commit()


        # Add sample inventory
        inventory_1 = Inventory(
            goods_id=goods_1.id,
            warehouse_id=warehouse.id,
            total_stock=300,
            onhand_stock=280,
            damage_stock=5,
            return_stock=15,
            dn_stock=100,
            asn_stock=1000,
            remark="Sample inventory for goods 1"
        )
        inventory_2 = Inventory(
            goods_id=goods_2.id,
            warehouse_id=warehouse.id,
            total_stock=300,
            onhand_stock=200,
            damage_stock=25,
            return_stock=25,
            dn_stock=100,
            asn_stock=200,
            remark="Sample inventory for goods 2"
        )
        db.session.add(inventory_1)
        db.session.add(inventory_2)
        db.session.commit()

        goods_location_1 = GoodsLocation(
            goods_id=goods_1.id,
            location_id=location_1.id,
            quantity=100
        )
        goods_location_2 = GoodsLocation(
            goods_id=goods_2.id,
            location_id=location_2.id,
            quantity=200
        )
        goods_location_3 = GoodsLocation(
            goods_id=goods_2.id,
            location_id=location_1.id,
            quantity=200
        )
        db.session.add_all([goods_location_1, goods_location_2,goods_location_3])
        db.session.commit()

        # 创建 Adjustment 和 AdjustmentDetails
        adjustment = Adjustment(
            warehouse_id=warehouse.id,
            status='pending',
            is_active=True,
            created_by=admin_user.id
        )
        db.session.add(adjustment)
        db.session.commit()

        adjustment_detail_1 = AdjustmentDetail(
            adjustment_id=adjustment.id,
            goods_id=goods_1.id,
            location_id=location_1.id,
            system_quantity=10,
            actual_quantity=15,
            adjustment_quantity=5,
        )
        adjustment_detail_2 = AdjustmentDetail(
            adjustment_id=adjustment.id,
            goods_id=goods_2.id,
            location_id=location_2.id,
            system_quantity=20,
            actual_quantity=25,
            adjustment_quantity=5,
        )
        db.session.add_all([adjustment_detail_1, adjustment_detail_2])
        db.session.commit()

        # Add transfer record
        transfer_record = TransferRecord(
            goods_id=goods_1.id,
            from_location_id=location_1.id,
            to_location_id=location_1.id,
            quantity=100,
            operator_id=operator.id,
            remark="Initial transfer"
        )
        db.session.add(transfer_record)
        db.session.commit()

        # Add sample supplier
        supplier = Supplier(
            name="Supplier A",
            address="123 Supplier Street",
            phone="123456789",
            email="supplierA@example.com",
            contact="John Doe",
            created_by=admin_user.id,
            company_id=company.id
        )
        supplier2 = Supplier(
            name="Supplier T",
            address="123 Supplier Street",
            phone="123456789",
            email="supplierT@example.com",
            contact="John Doe",
            created_by=admin_user.id,
            company_id=company.id
        )
        db.session.add_all([supplier, supplier2])
        db.session.commit()

        # 4. 创建示例 Carrier
        carrier = Carrier(
            name='Test Carrier',
            address='Carrier Address',
            phone='987654',
            email='carrier@example.com',
            contact='Carrier Contact',
            is_active=True,
            created_by=admin_user.id,
            company_id=company.id
        )
        db.session.add(carrier)
        db.session.commit()


        # Add sample recipient
        recipient = Recipient(
            name="Recipient A",
            address="123 Recipient Street",
            zip_code="12345",
            phone="123456789",
            email="recipientA@example.com",
            contact="John Doe",
            country="us",
            created_by=admin_user.id,
            company_id=1
        )
        recipient2 = Recipient(
            name="Recipient T",
            address="123 Recipient Street",
            zip_code="12345",
            phone="123456789",
            email="recipienT@example.com",
            contact="John Doe",
            country="us",
            created_by=admin_user.id,
            company_id=1
        )
        db.session.add_all([recipient, recipient2])
        db.session.commit()

        # 创建 ASN（最小化示例）
        asn = ASN(
            warehouse_id=warehouse.id,
            supplier_id=supplier.id,
            carrier_id=carrier.id,
            asn_type='inbound',
            status='pending',
            remark='Test ASN creation',
            created_by=admin_user.id
        )
        asn2 = ASN(
            warehouse_id=warehouse.id,
            supplier_id=supplier.id,
            carrier_id=carrier.id,
            asn_type='inbound',
            status='pending',
            remark='Test ASN creation',
            created_by=admin_user.id
        )
        db.session.add_all([asn, asn2])
        db.session.commit()

        # 创建 ASNDetail
        asn_detail_1 = ASNDetail(
            asn_id=asn.id,
            goods_id=goods_1.id,
            quantity=1000,
            actual_quantity=0,
            remark='Sample detail',
            created_by=admin_user.id
        )
        asn_detail_2 = ASNDetail(
            asn_id=asn.id,
            goods_id=goods_2.id,
            quantity=200,
            actual_quantity=0,
            remark='Sample detail',
            created_by=admin_user.id
        )
        db.session.add(asn_detail_1)
        db.session.add(asn_detail_2)
        db.session.commit()

        # 创建一个 SortingTask（初始 pending，不在这里创建 detail）
        task = SortingTask(
            asn_id=asn.id,
            status='pending',
            is_active=True,
            created_by=admin_user.id
        )
        db.session.add(task)
        db.session.commit()


        # 创建一个示例 RemovalRecord
        record_1 = RemovalRecord(
            goods_id=goods_1.id,
            location_id=location_1.id,
            quantity=10,
            operator_id=admin_user.id,
            reason="damage",
            remark="Initial removal record"
        )
        db.session.add(record_1)
        db.session.commit()


        # 3. 创建示例 CycleCount
        cyclecount = CycleCountTask(
            task_name='CC-001',
            warehouse_id=warehouse.id,
            status='pending',
            is_active=True,
            created_by=admin_user.id
        )
        db.session.add(cyclecount)
        db.session.commit()

        # 4. 创建示例 CycleCountDetail
        cyclecount_detail_1 = CycleCountTaskDetail(
            task_id=cyclecount.id,
            goods_id=1,
            location_id=1,
            actual_quantity=10,
            status='pending',
            operator_id=admin_user.id
        )
        cyclecount_detail_2 = CycleCountTaskDetail(
            task_id=cyclecount.id,
            goods_id=2,
            location_id=2,
            actual_quantity=20,
            status='completed',
            operator_id=admin_user.id
        )

        db.session.add(cyclecount_detail_1)
        db.session.add(cyclecount_detail_2)
        db.session.commit()


        # 创建一个示例 PutawayRecord
        putway_record_1 = PutawayRecord(
            goods_id=goods_1.id,
            location_id=location_1.id,
            quantity=100,
            operator_id=admin_user.id,
            remark="Initial putaway record"
        )
        db.session.add(putway_record_1)
        db.session.commit()


        


        # 6. 创建示例 DN 及其明细
        dn = DN(
            recipient_id=recipient.id,
            warehouse_id=warehouse.id,
            shipping_address='123 Test Street',
            expected_shipping_date=db.func.current_date(),
            carrier_id=carrier.id,
            dn_type='shipping',
            status='pending',
            remark='Test DN creation',
            created_by=admin_user.id
        )

        # 6. 创建示例 DN 及其明细
        dn2 = DN(
            recipient_id=recipient.id,
            warehouse_id=warehouse.id,
            shipping_address='123 Test Street',
            expected_shipping_date=db.func.current_date(),
            carrier_id=carrier.id,
            dn_type='shipping',
            status='pending',
            remark='Test DN creation',
            created_by=admin_user.id
        )

        dn3 = DN(
            recipient_id=recipient.id,
            warehouse_id=warehouse.id,
            shipping_address='123 Test Street',
            expected_shipping_date=db.func.current_date(),
            carrier_id=carrier.id,
            dn_type='shipping',
            status='in_progress',
            remark='Test DN creation',
            created_by=admin_user.id
        )

        
        db.session.add_all([dn, dn2,dn3])
        db.session.flush()  # 使 dn.id 可用

        dn_detail_1 = DNDetail(
            dn_id=dn.id,
            goods_id=goods_1.id,
            quantity=10,
            picked_quantity=0,
            remark='Sample DN detail 1',
            created_by=admin_user.id
        )
        dn_detail_2 = DNDetail(
            dn_id=dn.id,
            goods_id=goods_1.id,
            quantity=20,
            picked_quantity=0,
            remark='Sample DN detail 2',
            created_by=admin_user.id
        )

        dn_detail_3 = DNDetail(
            dn_id=dn2.id,
            goods_id=goods_1.id,
            quantity=20,
            picked_quantity=0,
            remark='Sample DN detail 3',
            created_by=admin_user.id
        )
        dn_detail_4 = DNDetail(
            dn_id=dn3.id,
            goods_id=goods_1.id,
            quantity=20,
            picked_quantity=0,
            remark='Sample DN detail 3',
            created_by=admin_user.id
        )
        dn_detail_5 = DNDetail(
            dn_id=dn3.id,
            goods_id=goods_1.id,
            quantity=20,
            picked_quantity=0,
            remark='Sample DN detail 3',
            created_by=admin_user.id
        )
        db.session.add_all([dn_detail_1, dn_detail_2, dn_detail_3, dn_detail_4, dn_detail_5])
        db.session.commit()

        picking_task = PickingTask(
            dn_id=dn3.id,
            status="pending",
            is_active=True,
            created_by=admin_user.id
        )
        db.session.add(picking_task)
        db.session.flush()

        # 创建 PickingTaskDetail
        picking_detail_1 = PickingTaskDetail(
            picking_task_id=picking_task.id,
            goods_id=goods_1.id,
            location_id=location_1.id,
            picked_quantity=10,
            operator_id=admin_user.id,
            batch_id=3
        )
        picking_detail_2 = PickingTaskDetail(
            picking_task_id=picking_task.id,
            goods_id=goods_2.id,
            location_id=location_1.id,
            picked_quantity=5,
            operator_id=admin_user.id,
            batch_id=2
        )
        db.session.add(picking_detail_1)
        db.session.add(picking_detail_2)
        db.session.commit()

        # 3. 创建 PackingTask
        packing_task = PackingTask(
            dn_id=dn.id,
            status="pending",
            is_active=True,
            created_by=admin_user.id
        )
        db.session.add(packing_task)
        db.session.commit()

        # 4. 创建 PackingTaskDetail
        packing_task_detail = PackingTaskDetail(
            packing_task_id=packing_task.id,
            goods_id=goods_1.id,
            packed_quantity=10,
            operator_id=admin_user.id,
            batch_id=1
        )
        db.session.add(packing_task_detail)
        db.session.commit()


        # 3. 创建 get_department
        delivery_task = DeliveryTask(
            dn_id = dn.id,
            tracking_number='DT-TEST-001',
            recipient_id=recipient.id,
            carrier_id=carrier.id,
            expected_shipping_date=db.func.current_date(),
            shipping_address='123 Delivery Street',
            status='pending',
            created_by=admin_user.id
        )
        db.session.add(delivery_task)
        db.session.commit()


        # 创建测试日志
        log1 = ActivityLog(
            actor='admin',
            endpoint='/api/test',
            method='GET',
            status_code=200,
            ip_address='127.0.0.1',
            created_at=datetime.datetime.now()
        )
        db.session.add(log1)
        db.session.commit()


        # 添加测试数据
        db.session.add(IPBlacklist(ip_address="192.168.0.1", reason="Spam"))
        db.session.add(IPBlacklist(ip_address="192.168.0.2", reason="Bot"))
        db.session.add(IPWhitelist(ip_address="10.0.0.1", reason="Trusted"))
        db.session.commit()


        # Add test API keys
        db.session.add(APIKey(key=str(uuid.uuid4()), system_name="System1", user_id=admin_user.id))
        db.session.add(APIKey(key=str(uuid.uuid4()), system_name="System2", user_id=admin_user.id))
        db.session.commit()


        # 计算出正确的库存信息,前面插入的测试数据不是一一对应完全正确的
        goods_list = db.session.query(Goods).all()
        for goods in goods_list:
            # 获取goods存放的库位
            for inventory in goods.inventories:
                # 获取库位的库存信息
                InventoryService.update_and_calculate_stock(inventory.goods_id, inventory.warehouse_id)
                InventoryService.update_and_calculate_asn_stock(inventory.goods_id, inventory.warehouse_id)
                InventoryService.update_and_calculate_dn_stock(inventory.goods_id, inventory.warehouse_id)



# ---------------------------------------------------------
# Fixtures
# ---------------------------------------------------------

@pytest.fixture
def client():
    """
    Provides a test client and sets up necessary test data:
    - admin user + role
    - Adjustment, AdjustmentDetail, Goods, Location, etc.
    """
    app = setup_app()
    with app.app_context():
        # 使用通用方法初始化测试数据
        init_test_data(app)

    # Use test client and cleanup after test execution
    with app.test_client() as test_client:
        yield test_client

    with app.app_context():
        db.drop_all()


@pytest.fixture
def access_token(client):
    """
    Get an access token for the admin user (JWT)
    """
    with client.application.app_context():
        user = User.query.filter_by(user_name='admin').first()
        return create_access_token(identity=user)


@pytest.fixture
def access_company_admin_token(client):
    """
    Get an access token for the admin user (JWT)
    """
    with client.application.app_context():
        user = User.query.filter_by(user_name='company_admin').first()
        return create_access_token(identity=user)


@pytest.fixture
def access_warehouse_admin_token(client):
    """
    Get an access token for the admin user (JWT)
    """
    with client.application.app_context():
        user = User.query.filter_by(user_name='warehouse_admin').first()
        return create_access_token(identity=user)
    
@pytest.fixture
def access_operator_token(client):
    """
    Get an access token for the admin user (JWT)
    """
    with client.application.app_context():
        user = User.query.filter_by(user_name='operator').first()
        return create_access_token(identity=user)
    
# ---------------------------------------------------------
# 获取第一个测试数据（SQLAlchemy 2.0 兼容版本）
# ---------------------------------------------------------

def get_admin_user():
    """
    Get the first admin user from the database
    """
    return db.session.query(User).filter_by(user_name='admin').first()

def get_company_admin_user():
    """
    Get the first company admin user from the database
    """
    return db.session.query(Staff).filter_by(user_name='company_admin').first()

def get_warehouse_admin_user():
    """
    Get the first warehouse admin user from the database
    """
    return db.session.query(Staff).filter_by(user_name='warehouse_admin').first()

def get_operator_user():
    """
    Get the first operator user from the database
    """
    return db.session.query(Staff).filter_by(user_name='operator').first()

def get_admin_role():
    """
    Get the admin role from the database
    """
    return db.session.query(Role).filter_by(name='admin').first()

def get_company():
    """
    Get the first company from the database
    """
    return db.session.query(Company).first()

def get_company_by_id(company_id):
    """
    Get the company by ID
    """
    return db.session.get(Company, company_id)

def get_department():
    """
    Get the first department from the database
    """
    return db.session.query(Department).first()

def get_department_by_id(department_id):
    """
    Get the department by ID
    """
    return db.session.get(Department, department_id)

def get_warehouse():
    """
    Get the first warehouse from the database
    """
    return db.session.query(Warehouse).first()

def get_warehouse_by_id(warehouse_id):
    """
    Get the warehouse by ID
    """
    return db.session.get(Warehouse, warehouse_id)

def get_goods():
    """
    Get the first goods from the database
    """
    return db.session.query(Goods).first()

def get_goods_by_id(goods_id):
    """
    Get the goods by ID
    """
    return db.session.get(Goods, goods_id)

def get_location():
    """
    Get the first location from the database
    """
    return db.session.query(Location).first()

def get_location_by_id(location_id):
    """
    Get the location by ID
    """
    return db.session.get(Location, location_id)

def get_adjustment():
    """
    Get the first adjustment from the database
    """
    return db.session.query(Adjustment).first()

def get_adjustment_detail():
    """
    Get the first adjustment detail from the database
    """
    return db.session.query(AdjustmentDetail).first()

def get_adjustment_by_id(adjustment_id):
    """
    Get the adjustment by ID
    """
    return db.session.get(Adjustment, adjustment_id)

def get_goods_location():
    """
    Get the first goods location from the database
    """
    return db.session.query(GoodsLocation).first()

def get_goods_location_by_id(goods_location_id):
    """
    Get the goods location by ID
    """
    return db.session.get(GoodsLocation, goods_location_id)

def get_supplier():
    """
    Get the first supplier from the database
    """
    return db.session.query(Supplier).first()

def get_supplier_by_id(supplier_id):
    """
    Get the supplier by ID
    """
    return db.session.get(Supplier, supplier_id)

def get_carrier():
    """
    Get the first carrier from the database
    """
    return db.session.query(Carrier).first()

def get_carrier_by_id(carrier_id):
    """
    Get the carrier by ID
    """
    return db.session.get(Carrier, carrier_id)

def get_asn():
    """
    Get the first ASN from the database
    """
    return db.session.query(ASN).first()

def get_asn_by_id(asn_id):
    """
    Get the ASN by ID
    """
    return db.session.get(ASN, asn_id)

def get_asn_detail():
    """
    Get the first ASN detail from the database
    """
    return db.session.query(ASNDetail).first()

def get_asn_detail_by_id(detail_id):
    """
    Get the ASN detail by ID
    """
    return db.session.get(ASNDetail, detail_id)

def get_asn_detail_by_asn_id(asn_id):
    """
    Get the ASN detail by ASN ID
    """
    return db.session.query(ASNDetail).filter_by(asn_id=asn_id).first()

def get_asn_details_by_asn_id(asn_id):
    """
    Get the ASN detail by ASN ID
    """
    return db.session.query(ASNDetail).filter_by(asn_id=asn_id).all()

def get_sorting_task():
    """
    Get the first sorting task from the database
    """
    return db.session.query(SortingTask).first()

def get_sorting_task_by_id(task_id):    
    """
    Get the sorting task by ID
    """
    return db.session.get(SortingTask, task_id)

def get_sorting_task_status_log():
    """
    Get the first sorting task status log from the database
    """
    return db.session.query(SortingTaskStatusLog).first()

def get_sorting_batch():
    """
    Get the first sorting batch from the database
    """
    return db.session.query(SortingBatch).first()

def get_sorting_batch_by_id(batch_id):
    """
    Get the sorting batch by ID
    """
    return db.session.get(SortingBatch, batch_id)

def get_sorting_task_details():
    """
    Get the first sorting task details from the database
    """
    return db.session.query(SortingTaskDetail).first()

def get_sorting_task_detail_by_id(detail_id):
    """
    Get the sorting task detail by ID
    """
    return db.session.get(SortingTaskDetail, detail_id)

def get_removal_record():
    """
    Get the first removal record from the database
    """
    return db.session.query(RemovalRecord).first()

def get_removal_record_by_id(record_id):
    """
    Get the removal record by ID
    """
    return db.session.get(RemovalRecord, record_id)

def get_recipient():
    """
    Get the first recipient from the database
    """
    return db.session.query(Recipient).first()

def get_recipient_by_id(recipient_id):  
    """
    Get the recipient by ID
    """
    return db.session.get(Recipient, recipient_id)

def get_putaway_record():
    """
    Get the first putaway record from the database
    """
    return db.session.query(PutawayRecord).first()

def get_putaway_record_by_id(record_id):
    """
    Get the putaway record by ID
    """
    return db.session.get(PutawayRecord, record_id)

def get_inventory():
    """
    Get the first inventory from the database
    """
    return db.session.query(Inventory).first()

def get_inventory_by_id(inventory_id):
    """
    Get the inventory by ID
    """
    return db.session.get(Inventory, inventory_id)

def get_inventory_by_goods_id_and_warehouse_id(goods_id, warehouse_id):
    """
    Get the inventory by ID
    """
    return db.session.query(Inventory).filter_by(goods_id=goods_id, warehouse_id=warehouse_id).first()

def get_dn():
    """
    Get the first DN from the database
    """
    return db.session.query(DN).first()

def get_dn_by_id(dn_id):
    """
    Get the DN by ID
    """
    return db.session.get(DN, dn_id)

def get_dn_detail():
    """
    Get the first DN detail from the database
    """
    return db.session.query(DNDetail).first()

def get_picking_task():
    """
    Get the first picking task from the database
    """
    return db.session.query(PickingTask).first()

def get_picking_task_by_id(task_id):
    """
    Get the picking task by ID
    """
    return db.session.get(PickingTask, task_id)

def get_picking_task_by_status(status):
    """
    Get the picking task by status
    """
    return db.session.query(PickingTask).filter_by(status=status).first()

def get_picking_task_detail():
    """
    Get the first picking task detail from the database
    """
    return db.session.query(PickingTaskDetail).first()

def get_picking_task_detail_by_id(detail_id):
    """
    Get the picking task detail by ID
    """
    return db.session.get(PickingTaskDetail, detail_id)

def get_picking_task_batch():
    """
    Get the first picking task batch from the database
    """
    return db.session.query(PickingBatch).first()

def get_picking_task_batch_by_id(batch_id):
    """
    Get the picking task batch by ID
    """
    return db.session.get(PickingBatch, batch_id)

def get_packing_task():
    """
    Get the first packing task from the database
    """
    return db.session.query(PackingTask).first()

def get_packing_task_by_id(task_id):
    """
    Get the packing task by ID
    """
    return db.session.get(PackingTask, task_id)

def get_packing_task_detail():
    """
    Get the first packing task detail from the database
    """
    return db.session.query(PackingTaskDetail).first()

def get_packing_task_detail_by_id(detail_id):
    """
    Get the packing task detail by ID
    """
    return db.session.get(PackingTaskDetail, detail_id)

def get_packing_task_batch():
    """
    Get the first packing task batch from the database
    """
    return db.session.query(PackingBatch).first()

def get_packing_task_batch_by_id(batch_id):
    """
    Get the packing task batch by ID
    """
    return db.session.get(PackingBatch, batch_id)

def get_activity_log():
    """
    Get the first activity log from the database
    """
    return db.session.query(ActivityLog).first()

def get_activity_log_by_id(log_id):
    """
    Get the activity log by ID
    """
    return db.session.get(ActivityLog, log_id)

def get_delivery_task():
    """
    Get the first delivery task from the database
    """
    return db.session.query(DeliveryTask).first()

def get_delivery_task_by_id(task_id):
    """
    Get the delivery task by ID
    """
    return db.session.get(DeliveryTask, task_id)

def get_cyclecount_task():
    """
    Get the first cyclecount task from the database
    """
    return db.session.query(CycleCountTask).first()

def get_cyclecount_task_by_id(task_id):
    """
    Get the cyclecount task by ID
    """
    return db.session.get(CycleCountTask, task_id)

def get_cyclecount_task_detail():
    """
    Get the first cyclecount task detail from the database
    """
    return db.session.query(CycleCountTaskDetail).first()

def get_cyclecount_task_detail_by_id(detail_id):
    """
    Get the cyclecount task detail by ID
    """
    return db.session.get(CycleCountTaskDetail, detail_id)

def get_cyclecount_task_detail_by_task_id(task_id):
    """
    Get the cyclecount task detail by task ID
    """
    return db.session.query(CycleCountTaskDetail).filter_by(task_id=task_id).first()

def get_adjustment():
    """
    Get the first adjustment from the database
    """
    return db.session.query(Adjustment).first()

def get_adjustment_by_id(adjustment_id):
    """
    Get the adjustment by ID
    """
    return db.session.get(Adjustment, adjustment_id)

def get_adjustment_detail():
    """
    Get the first adjustment detail from the database
    """
    return db.session.query(AdjustmentDetail).first()

def get_adjustment_detail_by_id(adjustment_detail_id):
    """
    Get the adjustment detail by ID
    """
    return db.session.get(AdjustmentDetail, adjustment_detail_id)

def get_transfer_record():
    """
    Get the first transfer record from the database
    """
    return db.session.query(TransferRecord).first()

def get_transfer_record_by_id(transfer_record_id):
    """
    Get the transfer record by ID
    """
    return db.session.get(TransferRecord, transfer_record_id)

def get_api_key():
    """
    Get the first API key from the database
    """
    return db.session.query(APIKey).first()

def get_api_key_by_id(api_key_id):
    """
    Get the API key by ID
    """
    return db.session.get(APIKey, api_key_id)

# ---------------------------------------------------------