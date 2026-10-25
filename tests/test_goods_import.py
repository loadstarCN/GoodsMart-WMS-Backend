"""商品导入（POST /goods/bulk_upload）与模板（GET /goods/bulk_upload/template）

- 模板由后端给，列与导入读的完全一致（含 origin_country）；模板原样导入能成功
- 补空白（append）/ 覆盖（override）也更新已有商品的重量、长宽高、分类、标签、折扣价（覆盖时空值不清空这些字段）
- 成功返回计数（created / updated / skipped）给界面拼翻译后的提示；失败带业务码与 details（row / rows / code / columns）
"""
import csv
import io

from tests.helpers import *  # noqa: F401,F403
from warehouse.goods.views import GOODS_IMPORT_COLUMNS


def _h(token):
    return {'Authorization': f'Bearer {token}'}


def _upload(client, token, content, overwrite='skip', filename='goods.csv'):
    data = content if isinstance(content, bytes) else content.encode('utf-8')
    return client.post('/goods/bulk_upload', headers=_h(token), data={
        'file': (io.BytesIO(data), filename), 'company_id': '1', 'overwrite': overwrite,
    }, content_type='multipart/form-data')


def _err(response, code):
    body = response.get_json()
    assert (response.status_code, body.get('code')) == (400, code), body
    assert body['message'].isascii()
    return body.get('details') or {}


def _goods(client, code):
    with client.application.app_context():
        goods = Goods.query.filter_by(code=code, company_id=1).first()
        return {k: getattr(goods, k) for k in ('weight', 'length', 'width', 'height', 'category', 'tags', 'price',
                                               'discount_price', 'origin_country', 'name', 'brand')}


def test_template_lists_every_imported_column_and_imports(client, access_token):
    response = client.get('/goods/bulk_upload/template', headers=_h(access_token))
    assert response.status_code == 200
    assert response.headers['Content-Type'].startswith('text/csv')
    text = response.data.decode('utf-8')
    assert text.startswith('﻿')
    rows = list(csv.reader(io.StringIO(text.lstrip('﻿'))))
    assert tuple(rows[0]) == GOODS_IMPORT_COLUMNS and 'origin_country' in rows[0]
    assert len(rows) == 2 and len(rows[1]) == len(rows[0])

    # 模板原样上传：样例行导入成功，原产国、规格都读到
    body = _upload(client, access_token, response.data).get_json()
    assert (body['created'], body['updated'], body['skipped'], body['processed']) == (1, 0, 0, 1)
    assert body['ignored_columns'] == [] and body['columns'] == list(GOODS_IMPORT_COLUMNS)
    goods = _goods(client, 'SAMPLE-001')
    assert goods['origin_country'] == 'JP' and float(goods['weight']) == 0.328
    assert (goods['length'], goods['width'], goods['height']) == (140, 130, 13)
    assert goods['category'] == 'Sample category' and goods['tags'] == 'tag1,tag2'
    assert float(goods['discount_price']) == 1500


def test_append_and_override_update_specs_category_tags_and_discount(client, access_token):
    header = 'code,name,weight,length,width,height,category,tags,price,discount_price,brand\n'
    response = _upload(client, access_token, header + 'IMP-1,Import One,,,,,,,1000,,\n')
    assert response.status_code == 200, response.get_json()

    # 补空白：原来为空的补上
    response = _upload(client, access_token, header + 'IMP-1,Import One,1.5,100,80,60,Toys,a;b,1000,900,BrandA\n',
                       overwrite='append')
    body = response.get_json()
    assert response.status_code == 200 and (body['created'], body['updated']) == (0, 1), body
    goods = _goods(client, 'IMP-1')
    assert float(goods['weight']) == 1.5 and (goods['length'], goods['width'], goods['height']) == (100, 80, 60)
    assert (goods['category'], goods['tags'], float(goods['discount_price']), goods['brand']) == ('Toys', 'a;b', 900,
                                                                                                    'BrandA')
    # 补空白不覆盖已有值
    _upload(client, access_token, header + 'IMP-1,Import One,9,9,9,9,Other,c,1000,100,BrandB\n', overwrite='append')
    goods = _goods(client, 'IMP-1')
    assert float(goods['weight']) == 1.5 and goods['category'] == 'Toys' and float(goods['discount_price']) == 900

    # 覆盖：非空的替换，空的不清空规格 / 分类 / 标签 / 折扣价
    response = _upload(client, access_token, header + 'IMP-1,Import One,2.25,,90,,Games,,1200,1100,\n',
                       overwrite='override')
    assert response.status_code == 200, response.get_json()
    goods = _goods(client, 'IMP-1')
    assert float(goods['weight']) == 2.25 and (goods['length'], goods['width'], goods['height']) == (100, 90, 60)
    assert (goods['category'], goods['tags'], float(goods['price']), float(goods['discount_price'])) == (
        'Games', 'a;b', 1200, 1100)

    # 覆盖后价格低于（保留的）折扣价 → 400 10012，带行号与列，整批不落库
    details = _err(_upload(client, access_token, header + 'IMP-1,Import One,,,,,,,1000,,\n', overwrite='override'),
                   10012)
    assert details['row'] == 2 and details['columns'] == ['discount_price', 'price'] and details['code'] == 'IMP-1'
    assert float(_goods(client, 'IMP-1')['price']) == 1200

    # 跳过：计数
    body = _upload(client, access_token, header + 'IMP-1,Import One,,,,,,,,,\nIMP-2,Import Two,,,,,,,,,\n').get_json()
    assert (body['created'], body['updated'], body['skipped'], body['processed']) == (1, 0, 1, 1)


def test_override_keeps_values_of_empty_cells(client, access_token):
    """覆盖：空单元格一律保留原值（以前名称以外的品牌、价格、货币、单位、图片、描述、生产日期会被清空 / 改成默认值）"""
    full = ('code,name,unit,manufacturer,brand,image_url,thumbnail_url,price,currency,production_date,description\n'
            'OVR-1,Keep Me,box,Maker,BrandX,https://img/a.jpg,https://img/a_s.jpg,1000,USD,2025-01-02,Desc\n')
    assert _upload(client, access_token, full).status_code == 200
    header = full.split('\n')[0] + '\n'
    response = _upload(client, access_token, header + 'OVR-1,Renamed,,,,,,,,,\n', overwrite='override')
    body = response.get_json()
    assert response.status_code == 200 and (body['updated'], body['unchanged']) == (1, 0), body
    with client.application.app_context():
        goods = Goods.query.filter_by(code='OVR-1', company_id=1).first()
        assert goods.name == 'Renamed'
        assert (goods.unit, goods.manufacturer, goods.brand, goods.currency) == ('box', 'Maker', 'BrandX', 'USD')
        assert (goods.image_url, goods.thumbnail_url, float(goods.price)) == ('https://img/a.jpg',
                                                                              'https://img/a_s.jpg', 1000)
        assert goods.production_date.isoformat() == '2025-01-02' and goods.description == 'Desc'

    # 内容没变：unchanged，不改 updated_at、不算 updated
    body = _upload(client, access_token, header + 'OVR-1,Renamed,box,,,,,,,,\n', overwrite='override').get_json()
    assert (body['updated'], body['unchanged'], body['processed']) == (0, 1, 1)

    # 只给新图片、缩略图留空：缩略图原来不跟着图片 → 保留；跟着图片的商品 → 跟着新图片走
    _upload(client, access_token, 'code,name,image_url\nOVR-2,Follow,https://img/b.jpg\n')
    _upload(client, access_token, 'code,name,image_url\nOVR-1,Renamed,https://img/new.jpg\n'
                                  'OVR-2,Follow,https://img/b2.jpg\n', overwrite='override')
    with client.application.app_context():
        assert Goods.query.filter_by(code='OVR-1').first().thumbnail_url == 'https://img/a_s.jpg'
        assert Goods.query.filter_by(code='OVR-2').first().thumbnail_url == 'https://img/b2.jpg'


def test_import_errors_carry_business_codes_and_details(client, access_token):
    details = _err(_upload(client, access_token, 'code,name\nX1,One\n', filename='goods.txt'), 10004)
    assert details['filename'] == 'goods.txt'
    assert _err(_upload(client, access_token, 'code,title\nX1,One\n'), 10005) == {'columns': ['name']}
    assert _err(_upload(client, access_token, 'code,name\nX1,One\nX2,\n'), 10006) == {'row': 3, 'columns': ['name']}
    assert _err(_upload(client, access_token, 'code,name,price\nX1,One,abc\n'), 10012) == {
        'row': 2, 'columns': ['price'], 'value': 'abc'}
    assert _err(_upload(client, access_token, 'code,name,origin_country\nX1,One,ZZ\n'), 10014) == {
        'row': 2, 'columns': ['origin_country'], 'value': 'ZZ'}
    details = _err(_upload(client, access_token, 'code,name,weight\nX1,One,-1\n'), 16100)
    assert details['row'] == 2 and details['columns'] == ['weight']
    assert _err(_upload(client, access_token, 'code,name\nX1,One\nX1,Again\n'), 16101) == {
        'code': 'X1', 'rows': [2, 3], 'columns': ['code']}
    # 编码自动识别：UTF-8、Shift_JIS 都解不开 → UNKNOWN_ENCODING；手动指定 utf-8 时 → NOT_UTF8
    assert _err(_upload(client, access_token, 'code,name\nX1,\xe9\n'.encode('latin-1')), 10016) == {
        'reason': 'UNKNOWN_ENCODING'}
    response = client.post('/goods/bulk_upload', headers=_h(access_token), data={
        'file': (io.BytesIO('code,name\nX1,商品\n'.encode('cp932')), 'goods.csv'), 'company_id': '1',
        'encoding': 'utf-8'}, content_type='multipart/form-data')
    assert _err(response, 10016) == {'reason': 'NOT_UTF8'}

    # 认不出的列：照常导入，在 ignored_columns 里列出
    body = _upload(client, access_token, 'code,name,colour\nX9,Nine,red\n').get_json()
    assert body['created'] == 1 and body['ignored_columns'] == ['colour']
