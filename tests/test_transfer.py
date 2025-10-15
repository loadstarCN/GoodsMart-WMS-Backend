from .helpers import *

def test_get_transfer_records(client, access_token):
    """
    Test listing transfer records (GET /transfer/).
    """
    response = client.get('/transfer/', headers={
        'Authorization': f'Bearer {access_token}'
    }, query_string={'page': 1, 'per_page': 10})
    
    assert response.status_code == 200
    data = response.get_json()
    assert 'items' in data
    assert data['total'] >= 1  # Expect at least one transfer record


def test_create_transfer_record(client, access_token):
    """
    Test creating a new transfer record (POST /transfer/).
    """
    with client.application.app_context():
        goods = get_goods()
        location = get_location_by_id(1)
        location2 = get_location_by_id(2)
        operator = get_operator_user()

        response = client.post('/transfer/', headers={
            'Authorization': f'Bearer {access_token}'
        }, json={
            "goods_id": goods.id,
            "from_location_id": location.id,
            "to_location_id": location2.id,
            "quantity": 5,
            "operator_id": operator.id,
            "remark": "Test transfer record"
        })
        assert response.status_code == 201
        data = response.get_json()
        assert data['goods_id'] == goods.id
        assert data['quantity'] == 5
        assert data['remark'] == "Test transfer record"


def test_get_transfer_record_details(client, access_token):
    """
    Test retrieving details of a transfer record (GET /transfer/<record_id>).
    """
    with client.application.app_context():
        transfer_record = get_transfer_record()
        response = client.get(f'/transfer/{transfer_record.id}', headers={
            'Authorization': f'Bearer {access_token}'
        })
        
        assert response.status_code == 200
        data = response.get_json()
        assert data['id'] == transfer_record.id
        assert data['goods_id'] == transfer_record.goods_id
        assert data['quantity'] == transfer_record.quantity


def test_bulk_create_transfer_record(client, access_token):
    """
    测试批量创建移库记录 (POST /transfer/bulk)
    """
    with client.application.app_context():
        goods = get_goods()
        location = get_location_by_id(1)
        location2 = get_location_by_id(2)
        operator = get_operator_user()

        # 构造批量创建的数据列表（此处创建两条记录）
        records_data = [
            {
                "goods_id": goods.id,
                "from_location_id": location.id,
                "to_location_id": location2.id,
                "quantity": 10,
                "remark": "Bulk record 1"
            },
            {
                "goods_id": goods.id,
                "from_location_id": location.id,
                "to_location_id": location2.id,
                "quantity": 15,
                "remark": "Bulk record 2"
            }
        ]

        response = client.post(
            '/transfer/bulk',
            headers={'Authorization': f'Bearer {access_token}'},
            json=records_data
        )
        assert response.status_code == 201
        data = response.get_json()
        # data 应为一个列表，包含至少两条记录
        assert isinstance(data, list)
        assert len(data) >= 2
        
        # 检查第一条记录
        record1 = data[0]
        assert record1['goods_id'] == goods.id
        assert record1['quantity'] == 10
        assert record1['remark'] == "Bulk record 1"
        
        # 检查第二条记录
        record2 = data[1]
        assert record2['goods_id'] == goods.id
        assert record2['quantity'] == 15
        assert record2['remark'] == "Bulk record 2"