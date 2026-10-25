"""发货单「批量添加商品」用：按仓库一次查一批商品编码的可售量（GET / POST /inventory/availability）。

- 商品按仓库所属公司、编码完全一致（去首尾空白）匹配；编码去重；items 按请求的顺序只列找到的商品，找不到的编码在 not_found
- 可售 = available_stock_for_sale（onhand − locked − dn_stock：只算 standard 库位、扣掉已被发货单占用的），与库存列表同口径；
  该仓库没有库存行的商品可售为 0。批次（允发期）二期启用后再扣不可发的批次（一期库存都是无批次的）
- 一次最多 1,000 个编码（400 15019，field goods_codes）
"""
from sqlalchemy.orm import lazyload

from extensions.error import BadRequestException
from warehouse.goods.models import Goods
from .models import Inventory

MAX_CODES = 1000
QUERY_INVALID = 15019


def parse_codes(value) -> list:
    """逗号分隔的文字或列表 → 去空白、去重（保持顺序）的编码；没有 / 超过 1,000 个 → 400 15019"""
    if isinstance(value, str):
        items = value.split(',')
    elif isinstance(value, (list, tuple)):
        items = value
    else:
        items = []
    codes = []
    for item in items:
        if not isinstance(item, (str, int)) or isinstance(item, bool):
            raise BadRequestException("goods_codes must be strings", QUERY_INVALID, 'goods_codes')
        code = str(item).strip()
        if code and code not in codes:
            codes.append(code)
    if not codes:
        raise BadRequestException("goods_codes is required", QUERY_INVALID, 'goods_codes')
    if len(codes) > MAX_CODES:
        raise BadRequestException(f"At most {MAX_CODES} goods codes at a time", QUERY_INVALID, 'goods_codes',
                                  details={'max_codes': MAX_CODES, 'count': len(codes)})
    return codes


def availability(warehouse, codes) -> dict:
    goods_by_code = {}
    for start in range(0, len(codes), 500):
        chunk = codes[start:start + 500]
        for goods in (Goods.query.options(lazyload('*'))
                      .filter(Goods.company_id == warehouse.company_id, Goods.code.in_(chunk)).all()):
            goods_by_code[goods.code] = goods
    goods_ids = [goods.id for goods in goods_by_code.values()]
    inventories = {}
    for start in range(0, len(goods_ids), 500):
        chunk = goods_ids[start:start + 500]
        for inventory in (Inventory.query.options(lazyload('*'))
                          .filter(Inventory.warehouse_id == warehouse.id, Inventory.goods_id.in_(chunk)).all()):
            inventories[inventory.goods_id] = inventory

    items, not_found = [], []
    for code in codes:
        goods = goods_by_code.get(code)
        if goods is None:
            not_found.append(code)
            continue
        inventory = inventories.get(goods.id)
        items.append({
            'goods_id': goods.id, 'code': goods.code, 'name': goods.name, 'unit': goods.unit,
            'is_active': bool(goods.is_active), 'thumbnail_url': goods.thumbnail_url,
            'available_stock_for_sale': inventory.available_stock_for_sale if inventory else 0,
            'onhand_stock': inventory.onhand_stock if inventory else 0,
            'dn_stock': inventory.dn_stock if inventory else 0,
            'locked_stock': inventory.locked_stock if inventory else 0,
        })
    return {'warehouse_id': warehouse.id, 'items': items, 'not_found': not_found}
