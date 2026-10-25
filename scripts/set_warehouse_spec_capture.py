"""一次性设置仓库的收货规格采集默认规则（手工运行，不是迁移；不改已建的收货通知）。

默认把名为 Kyoto#1 的仓库设为「缺什么采什么」：采集项 weight + dimensions + origin_country，模式 missing（只补商品资料
里缺的项），分拣完成时提示不拦截（warn）。之后新建的收货通知（含批发站经 API 建的）没传 spec_capture 时复制这套设置。
同样的设置也可以在仓库编辑页 / PUT /warehouse/warehouse/<id> 里改（default_spec_capture_fields /
default_spec_capture_mode / spec_capture_enforcement）。

默认 dry-run：只打印仓库现在的设置和要改成的设置，不写库。加 --apply 才写库。
同名仓库在多家公司都有时要加 --company-id；也可以直接用 --warehouse-id。

用法（在 Backend 目录、生产 .env 生效的环境里）：
    python scripts/set_warehouse_spec_capture.py                      # dry-run，Kyoto#1
    python scripts/set_warehouse_spec_capture.py --apply              # 写库
    python scripts/set_warehouse_spec_capture.py --warehouse-id 2 --fields weight,dimensions --mode remeasure \\
        --enforcement block --apply
"""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DEFAULT_WAREHOUSE_NAME = 'Kyoto#1'
DEFAULT_FIELDS = 'weight,dimensions,origin_country'


def find_warehouses(name=None, warehouse_id=None, company_id=None):
    """按 id 或名字（可再按公司）找仓库"""
    from warehouse.warehouse.models import Warehouse
    query = Warehouse.query
    if warehouse_id is not None:
        query = query.filter(Warehouse.id == warehouse_id)
    else:
        query = query.filter(Warehouse.name == name)
    if company_id is not None:
        query = query.filter(Warehouse.company_id == company_id)
    return query.order_by(Warehouse.id).all()


def apply_settings(warehouse, fields, mode, enforcement, apply=False) -> tuple:
    """校验并（可选）写入设置 → (原来的设置, 新设置)。取值不合法抛 BadRequestException（同仓库接口 400 17020）"""
    from extensions.db import db
    from warehouse.common.spec_capture import warehouse_spec_settings

    settings = warehouse_spec_settings({
        'default_spec_capture_fields': fields,
        'default_spec_capture_mode': mode,
        'spec_capture_enforcement': enforcement,
    })
    before = {field: getattr(warehouse, field) for field in settings}
    if apply:
        for field, value in settings.items():
            setattr(warehouse, field, value)
        db.session.commit()
    else:
        db.session.rollback()
    return before, settings


def main(argv=None):
    parser = argparse.ArgumentParser(description='Set the default spec capture rule of a warehouse')
    parser.add_argument('--warehouse-name', default=DEFAULT_WAREHOUSE_NAME,
                        help=f'warehouse name (default {DEFAULT_WAREHOUSE_NAME})')
    parser.add_argument('--warehouse-id', type=int, help='warehouse id (instead of the name)')
    parser.add_argument('--company-id', type=int, help='company id, when several companies have the same name')
    parser.add_argument('--fields', default=DEFAULT_FIELDS,
                        help=f'comma separated weight / dimensions / origin_country; "" = no capture '
                             f'(default {DEFAULT_FIELDS})')
    parser.add_argument('--mode', default='missing', choices=('missing', 'remeasure'), help='default missing')
    parser.add_argument('--enforcement', default='warn', choices=('warn', 'block'), help='default warn')
    parser.add_argument('--apply', action='store_true', help='write the settings (default: dry-run)')
    args = parser.parse_args(argv)
    fields = [name.strip() for name in args.fields.split(',') if name.strip()]

    # 延迟导入：app.py 导入时即建好 app（生产配置）
    from app import app
    from extensions.error import BadRequestException

    with app.app_context():
        warehouses = find_warehouses(args.warehouse_name, args.warehouse_id, args.company_id)
        target = f'id={args.warehouse_id}' if args.warehouse_id is not None else repr(args.warehouse_name)
        if not warehouses:
            print(f'找不到仓库 {target}' + (f'（公司 {args.company_id}）' if args.company_id else ''))
            return 1
        if len(warehouses) > 1:
            listed = ', '.join(f'id={w.id} 公司={w.company_id}' for w in warehouses)
            print(f'仓库 {target} 不止一个（{listed}），请加 --company-id 或改用 --warehouse-id')
            return 1
        warehouse = warehouses[0]
        label = f'id={warehouse.id} {warehouse.name}（公司 {warehouse.company_id}）'
        try:
            before, after = apply_settings(warehouse, fields, args.mode, args.enforcement, apply=args.apply)
        except BadRequestException as exc:
            print(f'设置不合法：{exc.message}')
            return 1

    mode = '已写库' if args.apply else 'dry-run（未写库，加 --apply 才写）'
    print(f'[{mode}] 仓库 {label}')
    for field, value in after.items():
        print(f'  {field}: {before[field]!r} -> {value!r}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
