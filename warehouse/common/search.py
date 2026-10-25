"""列表关键字按单号搜索：界面把单号显示成「前缀-编号」（ASN-12、SORT-3、DN-163、PICK-5、PACK-5、DEL-7、CC-2、ADJ-4），
用户照抄搜索时也要能搜到，不只认纯数字。

- 纯数字（「12」）：照旧，按各列表原来的编号列找（如分拣任务按任务编号或入库单编号）
- 前缀 + 编号（大小写不限，前缀与编号之间可以有 - _ # 或空格：「ASN-12」「asn12」「DN #163」）：按前缀对应的列找；
  列表不认识的前缀不按编号找
其它条件（运单号、订单号、商品编码……）仍用原关键字照常匹配。
"""
import re

_NUMBER_RE = re.compile(r'([A-Za-z]+)?[\s\-_#]*([0-9]+)')
# 编号列是 int（PostgreSQL integer）：超出范围的数字不当单号（否则比较时报 integer out of range）
_MAX_ID = 2 ** 31 - 1


def parse_document_number(keyword):
    """关键字 → (前缀大写或 None, 编号)；不像单号 → None"""
    if keyword is None:
        return None
    match = _NUMBER_RE.fullmatch(str(keyword).strip())
    if not match:
        return None
    number = int(match.group(2))
    if number > _MAX_ID:
        return None
    prefix = match.group(1).upper() if match.group(1) else None
    return prefix, number


def number_conditions(keyword, by_prefix: dict, plain) -> list:
    """关键字是单号时的过滤条件：by_prefix {前缀: [列, ...]}；plain：纯数字时比较的列"""
    parsed = parse_document_number(keyword)
    if parsed is None:
        return []
    prefix, number = parsed
    columns = plain if prefix is None else by_prefix.get(prefix, [])
    return [column == number for column in columns]
