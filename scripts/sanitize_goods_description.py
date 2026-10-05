"""存量商品描述（goods.description）的 HTML 白名单清洗（B-73，手工运行，不是迁移）。

新写入的描述已在后端按白名单清洗（warehouse/goods/sanitize.py：JSON 新建 / 修改、CSV 导入）；
这个脚本把上线前已经入库的描述按同一份白名单清洗一遍。

默认 dry-run：只统计、打印会变更的条数和商品 id，不写库。加 --apply 才写库（按批提交）。
只处理含 "<" 的描述（纯文本不可能带标签，清洗器对它也原样返回）。描述不是规格字段，不发 webhook。

用法（在 Backend 目录、生产 .env 生效的环境里）：
    python scripts/sanitize_goods_description.py              # dry-run
    python scripts/sanitize_goods_description.py --apply      # 写库
    python scripts/sanitize_goods_description.py --apply --batch-size 200 --show 50
"""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def sanitize_existing_descriptions(apply=False, batch_size=500):
    """在当前 app context 里扫描并（可选）清洗存量描述。

    :return: (扫描条数, 会变更 / 已变更的商品 id 列表)
    """
    from extensions.db import db
    from warehouse.goods.models import Goods
    from warehouse.goods.sanitize import sanitize_description

    scanned = 0
    changed_ids = []
    last_id = 0
    while True:
        rows = (
            Goods.query
            .filter(Goods.id > last_id, Goods.description.isnot(None), Goods.description.like('%<%'))
            .order_by(Goods.id)
            .limit(batch_size)
            .all()
        )
        if not rows:
            break
        for goods in rows:
            scanned += 1
            cleaned = sanitize_description(goods.description)
            if cleaned != goods.description:
                changed_ids.append(goods.id)
                if apply:
                    goods.description = cleaned
        last_id = rows[-1].id
        if apply:
            db.session.commit()
        else:
            db.session.rollback()
        db.session.expunge_all()
    return scanned, changed_ids


def main(argv=None):
    parser = argparse.ArgumentParser(description='Sanitize existing goods.description with the nh3 whitelist')
    parser.add_argument('--apply', action='store_true', help='write the cleaned descriptions (default: dry-run)')
    parser.add_argument('--batch-size', type=int, default=500, help='rows per batch / commit (default 500)')
    parser.add_argument('--show', type=int, default=20, help='how many changed goods ids to print (default 20)')
    args = parser.parse_args(argv)
    if args.batch_size <= 0:
        parser.error('--batch-size must be > 0')

    # 延迟导入：测试里直接调 sanitize_existing_descriptions，不建生产 app（app.py 导入时即建好 app）
    from app import app

    with app.app_context():
        scanned, changed_ids = sanitize_existing_descriptions(apply=args.apply, batch_size=args.batch_size)

    mode = '已写库' if args.apply else 'dry-run（未写库，加 --apply 才写）'
    print(f'[{mode}] 扫描含 HTML 的描述 {scanned} 条，需要清洗 {len(changed_ids)} 条')
    if changed_ids:
        shown = changed_ids[:max(args.show, 0)]
        more = f' …（另有 {len(changed_ids) - len(shown)} 条）' if len(changed_ids) > len(shown) else ''
        print('商品 id：' + ', '.join(str(i) for i in shown) + more)
    return 0


if __name__ == '__main__':
    sys.exit(main())
