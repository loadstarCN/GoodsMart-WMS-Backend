"""主数据删除前的引用检查。

供应商 / 承运商 / 收件人 / 商品 / 仓库 / 部门被单据、库存等记录引用时，外键是 RESTRICT，
直接 delete 会撞数据库约束（以前一律兜成 500）。删除前先按外键反查引用数，有就 409 44004，
details 里给出各表的引用行数，前端提示改用停用。

引用关系从 SQLAlchemy 元数据里自动找（所有指向该表的外键），以后新增引用表不用再改这里。
"""
from sqlalchemy import func, select

from extensions.db import db
from extensions.error import ConflictException

MASTER_DATA_IN_USE = 44004

# 删除时由数据库自动处理、本身不妨碍删除的外键
_NON_BLOCKING_ONDELETE = frozenset({'CASCADE', 'SET NULL'})


def count_references(model, object_id, include=(), exclude=()) -> dict:
    """统计仍引用 model(id=object_id) 的记录：{表名: 行数}，没有引用返回 {}。

    :param include: 即使外键是 CASCADE / SET NULL 也要算作引用的表（例如部门下的员工）
    :param exclude: 不统计的表（例如删除时由 ORM 清掉的多对多关联表）
    """
    target = model.__table__
    counts = {}
    for table in db.metadata.sorted_tables:
        if table.name in exclude:
            continue
        for fk in table.foreign_keys:
            if fk.column.table is not target:
                continue
            if (fk.ondelete or '').upper() in _NON_BLOCKING_ONDELETE and table.name not in include:
                continue
            count = db.session.execute(
                select(func.count()).select_from(table).where(fk.parent == object_id)
            ).scalar()
            if count:
                counts[table.name] = counts.get(table.name, 0) + count
    return counts


def ensure_not_referenced(model, object_id, entity, include=(), exclude=()):
    """仍被引用 → 409 44004，details {entity, id, references}"""
    references = count_references(model, object_id, include=include, exclude=exclude)
    if references:
        summary = ', '.join(f"{name}: {count}" for name, count in sorted(references.items()))
        raise ConflictException(
            f"{entity} is still referenced by other records ({summary}); deactivate it instead of deleting",
            MASTER_DATA_IN_USE,
            details={'entity': entity, 'id': object_id, 'references': references},
        )
