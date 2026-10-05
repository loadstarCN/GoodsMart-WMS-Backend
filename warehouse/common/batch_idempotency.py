"""分拣 / 拣货 / 打包「追加批次」的幂等（B-102）。

POST /sorting|picking|packing/<task_id>/batches/ 可带 client_batch_id（1~64 个字符的字符串，前端每次「提交一批」生成一个，
断网 / 超时重试时原样带上）：
- 第一次提交：正常创建，201
- 同一任务下同一 client_batch_id 再次提交、明细相同：不再新增，200，返回已存在的那个批次（结构同 201）
- 同一 client_batch_id、明细不同：409 16121，details {client_batch_id, batch_id}
- 不带 client_batch_id：行为不变（每次都新增）
- client_batch_id 不是 1~64 个字符的字符串：400 16123

「明细相同」只比商品与数量（拣货还比库位），与明细顺序无关；备注、操作时间不比（重试时前端可能重新取时间）。
比的是批次当前的明细：批次建好后明细被改过 / 删过，再用原内容重放会是 16121；批次被删掉后再重放会重新创建（201）。

并发：调用方先锁父单据与任务行（各模块 _lock），在锁内查重；三张批次表上「同一任务内 client_batch_id 唯一」的约束兜底
（NULL 不参与唯一判断）。兜底撞到 IntegrityError 时只回滚 savepoint，再按已存在的批次返回 200 或 409，不会 500。
调用方把业务校验放在 insert_batch 之前，插入批次是最后一步可能失败的写入（测试用的 SQLite 驱动在事务里还没有写操作时，
释放最外层 savepoint 会直接提交，之后再失败就回滚不掉这条批次）。
"""
from sqlalchemy.exc import IntegrityError

from extensions.db import db
from extensions.error import BadRequestException, ConflictException

CLIENT_BATCH_ID_MAX_LENGTH = 64


def parse_client_batch_id(data) -> str | None:
    """取出并校验 client_batch_id；没带（或为 null）返回 None"""
    if not isinstance(data, dict) or data.get('client_batch_id') is None:
        return None
    value = data['client_batch_id']
    if not isinstance(value, str) or not 1 <= len(value) <= CLIENT_BATCH_ID_MAX_LENGTH:
        raise BadRequestException(
            f"client_batch_id must be a string of 1 to {CLIENT_BATCH_ID_MAX_LENGTH} characters",
            16123, field='client_batch_id',
        )
    return value


def find_batch(batch_model, task_fk: str, task_id: int, client_batch_id: str | None):
    """同一任务下 client_batch_id 对应的已有批次（populate_existing：读数据库里的最新明细关联）"""
    if client_batch_id is None:
        return None
    return (
        batch_model.query
        .filter(getattr(batch_model, task_fk) == task_id, batch_model.client_batch_id == client_batch_id)
        .populate_existing()
        .first()
    )


def signature(rows) -> list:
    """明细内容的比较用签名：元组列表排序后比较（与顺序无关、保留重复行）"""
    return sorted(tuple(row) for row in rows)


def replay_or_conflict(batch, client_batch_id: str, existing_rows, incoming_rows):
    """已存在同 client_batch_id 的批次：明细相同 → 返回该批次（重放）；不同 → 409 16121"""
    db.session.expire(batch, ['details'])
    if signature(existing_rows(batch)) != signature(incoming_rows):
        raise ConflictException(
            f"client_batch_id {client_batch_id!r} was already used for batch {batch.id} with different details.",
            16121, details={'client_batch_id': client_batch_id, 'batch_id': batch.id},
        )
    return batch


def insert_batch(batch, *, batch_model, task_fk: str, task_id: int, client_batch_id: str | None,
                 existing_rows, incoming_rows):
    """插入新批次并 flush 拿到 id，返回 (batch, created)。

    带 client_batch_id 时插入放在 savepoint 里：撞上「同一任务内唯一」约束（锁外的并发请求先插入了同一个 id）
    → 回滚 savepoint，按已存在的批次重放（200）或 409 16121；调用方据 created=False 直接返回，不再写明细。
    """
    if client_batch_id is None:
        db.session.add(batch)
        db.session.flush()
        return batch, True
    try:
        with db.session.begin_nested():
            db.session.add(batch)
            db.session.flush()
    except IntegrityError:
        existing = find_batch(batch_model, task_fk, task_id, client_batch_id)
        if existing is None:
            raise
        return replay_or_conflict(existing, client_batch_id, existing_rows, incoming_rows), False
    return batch, True
