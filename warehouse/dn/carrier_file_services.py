"""文件方式（doc/shipping-flow.md §6.1）：没有 API 合同时，按承运商软件的导入格式导出 CSV，在承运商软件里打完单，
再把带运单号的 CSV 导回，按匹配键（DN{dn.id}）找回 DN。模板引擎见 warehouse/carrier/file_templates.py，
承运商的设置（预设 / 改动 / 账号项）见 warehouse/carrier/file_settings.py。

- 导出（POST /warehouse/dn/carrier-files/export）：承运商的 waybill_method 要是 file、模板齐全（必填账号项都有值，
  否则 409 16138）。DN 要属于同一仓库、已打包、承运商（发货任务的，没有用 DN 的）= 这家承运商、有未完成的发货任务、
  没有未结束的运单（已是 exported 的可以重新导出）、运单服务选项合法；再按模板逐行逐列检查（必填为空、超长、编码不了的
  字符……）。任何一行有问题整批不导出：409 16137（details.rows = [{dn_id, order_number, problems: [{column, code,
  message}]}]），不建记录。全部没问题：记一个导出批次（carrier_file_batches，kind export），每张 DN 建 source = file、
  status = exported 的运单记录（重新导出的记录不变，只换批次），返回文件。
- 导入（POST /warehouse/dn/carrier-files/import，multipart）：按导入设置读文件（编码 + 备选编码、表头行、列号），
  匹配键按 reference_parse 解析出 DN；一张 DN 多行（多件）= 多个运单号，第一个为主单号，其余进
  package_tracking_numbers。逐行结果：matched（可以写入）/ not_found（找不到 DN，或不是本仓库 / 本公司的）/
  already_has_number（DN 已有不同的运单号，需人工决定）/ same_number（已导入过，忽略）/ invalid（运单号为空、
  DN 已关闭、承运商不同、插件运单结果不明、号码已属于别的 DN……）。dry_run = true 只返回预览；false 时在一个事务里
  逐张 DN 加锁重新判断后写入 matched 的（exported → active，没导出过的直接建 file 运单），同步到发货任务的运单号，
  并记一个导入批次。
- 作废导出（POST /warehouse/dn/<id>/carrier-shipment/discard-export）：exported → cancelled（reason discarded），只改本地。
- 批次（GET /warehouse/dn/carrier-files/batches）：最近的导出 / 导入记录。
"""
import re

from extensions.db import db
from extensions.error import BadRequestException, ConflictException, NotFoundException
from extensions.transaction import transactional
from warehouse.carrier import file_settings
from warehouse.carrier.file_templates import (
    ImportFileError, content_type, parse_import, render_rows, write_csv,
)
from warehouse.carrier.models import Carrier
from warehouse.common.address_format import dn_ship_to, format_single_line, warehouse_address
from warehouse.common.countries import COUNTRY_NAMES_EN
from warehouse.common.ownership import get_company_owned
from warehouse.common.permissions import check_warehouse_access
from warehouse.common import clock
from warehouse.shipping import options as shipping_options
from warehouse.shipping.common import current_delivery_task, local_today, lock_dn
from warehouse.shipping.waybills import SOURCE_FILE, current_waybill, exported_waybill

from .models import DN, CarrierFileBatch, DNCarrierShipment

FILE_ROWS_INVALID = 16137
FILE_UNAVAILABLE = 16138
MAX_EXPORT_DNS = 500
MAX_IMPORT_BYTES = 5 * 1024 * 1024
MAX_IMPORT_ROWS = 5000
RESULTS = ('matched', 'not_found', 'already_has_number', 'same_number', 'invalid')
OPEN_TASK_STATUSES = ('pending', 'in_progress')
SHIPPED = ('delivered', 'completed')
_FILE_SAFE = re.compile(r'[^A-Za-z0-9._-]+')


def _unavailable(message, field=None, status=409, details=None):
    if status == 400:
        return BadRequestException(message, FILE_UNAVAILABLE, field=field, details=details)
    return ConflictException(message, FILE_UNAVAILABLE, field=field, details=details)


def _normalize(value) -> str:
    """运单号比对用（去空白 / 连字符，与 carrier_services.normalize_tracking 同一口径）"""
    return ''.join(str(value or '').split()).replace('-', '')


def parse_id(value, field):
    """正整数（或正整数字符串）；不合法 400 16138"""
    if isinstance(value, str) and value.strip().isdigit():
        value = int(value.strip())
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise _unavailable(f"{field} must be a positive integer", field, status=400)
    return value


def parse_dn_ids(value) -> list:
    if not isinstance(value, list) or not value:
        raise _unavailable("dn_ids must be a non-empty list of DN ids", 'dn_ids', status=400)
    if len(value) > MAX_EXPORT_DNS:
        raise _unavailable(f"dn_ids must not exceed {MAX_EXPORT_DNS} DNs", 'dn_ids', status=400)
    ids = []
    for item in value:
        dn_id = parse_id(item, 'dn_ids')
        if dn_id not in ids:
            ids.append(dn_id)
    return ids


def _reason(code, message, **fields):
    """导入预览一行的说明：reason_code（界面按它出本地化文字）+ 英文 message（兜底）+ 拼文字用的字段
    （existing_tracking_number / other_carrier / other_carrier_id / other_dn_id / shipment_status，没有的不给）。

    reason_code：not_found → DN_NOT_FOUND / NOT_WMS_REFERENCE / NO_REFERENCE；invalid → NO_TRACKING_NUMBER / DN_CLOSED /
    OTHER_CARRIER / NUMBER_OWNED_BY_OTHER_DN / API_SHIPMENT_UNRESOLVED / EXPORTED_FOR_OTHER_CARRIER /
    SHIPPED_WITHOUT_FILE_WAYBILL / NO_OPEN_TASK；same_number → ALREADY_IMPORTED / ALREADY_SAVED；
    already_has_number → HAS_OTHER_NUMBER"""
    return {'reason_code': code, 'message': message, **{k: v for k, v in fields.items() if v is not None}}


def _problem(code, message, column=None, **extra):
    item = {'column': column, 'code': code, 'message': message}
    item.update({k: v for k, v in extra.items() if v is not None})
    return item


def _adapter_code(carrier):
    integration = getattr(carrier, 'integration', None)
    return integration.adapter if integration is not None else None


class CarrierFileService:

    # ------------------------------------------------------------------
    # 承运商与模板
    # ------------------------------------------------------------------

    @staticmethod
    def file_carrier(carrier_id, for_import=False):
        """→ (承运商, 规范化后的模板)。carrier_id 不合法 400 16138；没有 / 别家公司 404 / 403；
        不是文件方式 / 没有可用模板 / 必填账号项没填 / 模板不能导入 → 409 16138（field 指明哪一项）"""
        if carrier_id in (None, ''):
            raise _unavailable("carrier_id is required", 'carrier_id', status=400)
        carrier = get_company_owned(Carrier, parse_id(carrier_id, 'carrier_id'), 'carrier')
        if (carrier.waybill_method or 'manual') != 'file':
            raise _unavailable(f"Carrier {carrier.name} does not use the file waybill method (waybill_method: "
                               f"{carrier.waybill_method})", 'carrier_id')
        template = file_settings.template_of(carrier)
        if template is None:
            error = file_settings.template_error(carrier)
            raise _unavailable(f"Carrier {carrier.name} has no usable file template"
                               + (f": {error}" if error else ""), 'carrier_id')
        if for_import:
            if not template.get('import'):
                raise _unavailable(f"The file template of carrier {carrier.name} cannot import tracking numbers "
                                   "(no import settings)", 'carrier_id')
        else:
            missing = file_settings.missing_account_fields(template, carrier.file_account)
            if missing:
                raise _unavailable(f"Carrier {carrier.name}: account fields {', '.join(missing)} must be filled in "
                                   "the carrier settings before exporting", f'file_account.{missing[0]}',
                                   details={'missing_account_fields': missing})
        return carrier, template

    # ------------------------------------------------------------------
    # 导出
    # ------------------------------------------------------------------

    @staticmethod
    def context(dn: DN, carrier, template, today=None) -> dict:
        """一张 DN 交给模板的变量（file_templates 模块说明里列的那些）"""
        ship_to = dn_ship_to(dn)
        shipper = warehouse_address(dn.warehouse)
        for address in (ship_to, shipper):
            address['address_text'] = format_single_line(address, 'local') or None
            address['country_name'] = COUNTRY_NAMES_EN.get(address.get('country') or '')
        names, quantity, kinds = [], 0, 0
        for detail in sorted(dn.details, key=lambda d: d.id or 0):
            count = detail.packed_quantity if dn.status in ('packed', 'delivered', 'completed') else detail.quantity
            if not count:
                continue
            kinds += 1
            quantity += count
            goods = detail.goods
            name = (getattr(goods, 'name', None) or getattr(goods, 'code', None) or '').strip()
            if name and name not in names:
                names.append(name)
        packages = list(dn.packages)
        weight = sum(float(p.gross_weight_kg or 0) for p in packages)
        schema = template['service_options']
        return {
            'dn': {
                'id': dn.id, 'order_number': dn.order_number, 'remark': dn.remark,
                'expected_shipping_date': dn.expected_shipping_date, 'special_handling': dn.special_handling,
                'packaging_info': dn.packaging_info,
            },
            'ship_date': today or local_today(dn),           # 发货日 = 仓库当地的今天
            'ship_to': ship_to,
            'shipper': shipper,
            'goods': {'summary': ' / '.join(names) or None, 'first_name': names[0] if names else None,
                      'quantity': quantity or None, 'count': kinds or None},
            'package': {'count': len(packages) or None, 'total_weight_kg': round(weight, 3) if packages else None},
            'options': shipping_options.effective(dn, schema),
            'carrier': {'name': carrier.name, 'code': carrier.code, 'account': dict(carrier.file_account or {})},
        }

    @staticmethod
    def dn_problems(dn: DN, carrier, template) -> list:
        """导出前 DN 本身的检查（不含模板逐列的）：[{column: None | 'options.<key>', code, message}]"""
        problems = []
        if dn.status == 'closed':
            problems.append(_problem('DN_CLOSED', "The DN is closed (cancelled)"))
        elif dn.status in SHIPPED:
            problems.append(_problem('DN_SHIPPED', f"The DN has already been shipped (status: {dn.status})"))
        elif dn.status != 'packed':
            problems.append(_problem('NOT_PACKED', f"The DN must be packed first (status: {dn.status})"))
        task = current_delivery_task(dn)
        if task is None or task.status not in OPEN_TASK_STATUSES:
            if dn.status == 'packed':
                problems.append(_problem('NO_OPEN_TASK', "The DN has no open delivery task"))
        from .carrier_services import CarrierShipmentService
        dn_carrier = CarrierShipmentService.carrier_of(dn, task)
        if dn_carrier is None:
            problems.append(_problem('CARRIER_NOT_SET', f"Choose the carrier of the delivery task first "
                                                        f"({carrier.name})"))
        elif dn_carrier.id != carrier.id:
            problems.append(_problem('OTHER_CARRIER', f"The DN's carrier is {dn_carrier.name}, not {carrier.name}"))
        record = current_waybill(dn)
        if record is not None and not (record.source == SOURCE_FILE and record.status == 'exported'
                                       and record.carrier_id == carrier.id):
            problems.append(_problem(
                'WAYBILL_EXISTS',
                f"The DN already has a {record.source} waybill ({record.status}"
                f"{' ' + record.tracking_number if record.tracking_number else ''})",
                shipment_id=record.id))
        elif record is None and task is not None and task.tracking_number:
            problems.append(_problem('TRACKING_NUMBER_EXISTS',
                                     f"The delivery task already has a tracking number ({task.tracking_number})"))
        for item in shipping_options.problems(dn, template['service_options']):
            problems.append(_problem(item['code'], item['message'], column=f"options.{item['key']}"))
        return problems

    @staticmethod
    def _file_name(carrier, template, now) -> str:
        code = _FILE_SAFE.sub('_', (carrier.code or '').strip()).strip('_') or \
            _FILE_SAFE.sub('_', str(template.get('code') or '').split('.')[-1]).strip('_') or f'carrier{carrier.id}'
        return f"{code[:40]}_{now.strftime('%Y%m%d-%H%M%S')}.csv"

    @staticmethod
    def _service(template, options) -> dict:
        """导出时记在运单上的服务：模板 service_option 指的那个选项的值与名称"""
        key = template.get('service_option')
        if not key:
            return {'service_type': None, 'service_name': None, 'service_label': None}
        option = next((o for o in template['service_options'] if o['key'] == key), None)
        value = options.get(key)
        entry = next((v for v in (option or {}).get('values') or [] if v['value'] == str(value)), None) \
            if value not in (None, '') else None
        label = entry['label'] if entry else None
        return {'service_type': str(value)[:50] if value not in (None, '') else None,
                'service_name': (label.get('en-US') or next(iter(label.values()), None)) if label else None,
                'service_label': label}

    @staticmethod
    @transactional
    def export(carrier_id, dn_ids, warehouse_id, user_id) -> dict:
        """POST /warehouse/dn/carrier-files/export → {content, file_name, content_type, batch, rows}（见模块说明）"""
        carrier, template = CarrierFileService.file_carrier(carrier_id)
        ids = parse_dn_ids(dn_ids)
        dns = {dn_id: db.session.get(DN, dn_id) for dn_id in sorted(ids)}
        # 按 id 顺序加锁（与其它批量操作同一顺序，避免死锁）
        for dn_id in sorted(ids):
            dn = dns[dn_id]
            if dn is not None and dn.warehouse.company_id == carrier.company_id \
                    and check_warehouse_access(dn.warehouse_id):
                dns[dn_id] = lock_dn(dn)
        if warehouse_id is None:
            first = next((dns[i] for i in ids if dns[i] is not None and check_warehouse_access(dns[i].warehouse_id)
                          and dns[i].warehouse.company_id == carrier.company_id), None)
            warehouse_id = first.warehouse_id if first is not None else None

        today = local_today(warehouse_id)               # 一批文件只属于一个仓库：发货日按它当地的今天
        rows, problem_rows = [], []
        for dn_id in ids:
            dn = dns[dn_id]
            if dn is None or dn.warehouse.company_id != carrier.company_id or not check_warehouse_access(dn.warehouse_id):
                problem_rows.append({'dn_id': dn_id, 'order_number': None,
                                     'problems': [_problem('DN_NOT_FOUND', f"DN {dn_id} not found")]})
                continue
            problems = []
            if dn.warehouse_id != warehouse_id:
                problems.append(_problem('OTHER_WAREHOUSE', "The DN belongs to another warehouse"))
            problems += CarrierFileService.dn_problems(dn, carrier, template)
            context = CarrierFileService.context(dn, carrier, template, today)
            rendered = render_rows(template, [context])[0]
            problems += rendered['problems']
            if problems:
                problem_rows.append({'dn_id': dn.id, 'order_number': dn.order_number, 'problems': problems})
                continue
            rows.append((dn, rendered['values'], context))
        if problem_rows:
            raise ConflictException(
                f"{len(problem_rows)} of {len(ids)} DNs cannot be exported; fix the listed problems and export again "
                "(nothing was exported)", FILE_ROWS_INVALID,
                details={'rows': problem_rows, 'total': len(ids)})

        now = clock.now()
        local_now = clock.local_now(warehouse_id)       # 文件名里的时刻：仓库当地时间
        file_name = CarrierFileService._file_name(carrier, template, local_now)
        content = write_csv(template, [values for _dn, values, _ctx in rows])
        batch = CarrierFileBatch(carrier_id=carrier.id, warehouse_id=warehouse_id, kind='export',
                                 file_name=file_name, row_count=len(rows), created_by=user_id, created_at=now)
        db.session.add(batch)
        db.session.flush()
        created, re_exported = 0, 0
        for dn, _values, context in rows:
            service = CarrierFileService._service(template, context['options'])
            record = exported_waybill(dn)
            data = dict((record.carrier_data if record is not None else None) or {})
            data.update({'preset': carrier.file_preset, 'service_name': service['service_name'],
                         'service_label': service['service_label'], 'exported_at': now.isoformat(timespec='seconds'),
                         'export_count': int(data.get('export_count') or 0) + 1})
            if record is None:
                record = DNCarrierShipment(
                    dn_id=dn.id, source=SOURCE_FILE, carrier=_adapter_code(carrier), carrier_id=carrier.id,
                    status='exported', created_by=user_id, created_at=now)
                db.session.add(record)
                created += 1
            else:
                re_exported += 1
            record.file_batch_id = batch.id
            record.service_type = service['service_type']
            record.package_count = len(dn.packages) or None
            record.carrier_data = data
            record.updated_at = now
            db.session.flush()
            db.session.expire(dn, ['carrier_shipments'])
        batch.summary = {'dn_ids': [dn.id for dn, _v, _c in rows], 'created': created, 're_exported': re_exported,
                         'preset': carrier.file_preset, 'encoding': template['export']['encoding']}
        db.session.flush()
        return {'content': content, 'file_name': file_name, 'content_type': content_type(template),
                'batch': batch.to_dict(), 'rows': len(rows)}

    # ------------------------------------------------------------------
    # 导入
    # ------------------------------------------------------------------

    @staticmethod
    def _dn_for(dn_id, carrier, warehouse_id):
        dn = db.session.get(DN, dn_id) if dn_id else None
        if dn is None or dn.warehouse.company_id != carrier.company_id or not check_warehouse_access(dn.warehouse_id):
            return None
        if warehouse_id is not None and dn.warehouse_id != warehouse_id:
            return None
        return dn

    @staticmethod
    def _evaluate(dn, carrier, numbers):
        """一张 DN 导入这些运单号的结果 → (result, 说明, 已导出的运单记录 | None)。
        说明 = _reason(...)：reason_code + 英文 message + 界面拼本地化文字用的字段；matched 为 None"""
        if dn is None:
            return 'not_found', _reason('DN_NOT_FOUND', "No such DN in this warehouse"), None
        if dn.status == 'closed':
            return 'invalid', _reason('DN_CLOSED', "The DN is closed (cancelled)"), None
        if not numbers:
            return 'invalid', _reason('NO_TRACKING_NUMBER', "No tracking number"), None
        from .carrier_services import CarrierShipmentService
        task = current_delivery_task(dn)
        dn_carrier = CarrierShipmentService.carrier_of(dn, task)
        if dn_carrier is not None and dn_carrier.id != carrier.id:
            return 'invalid', _reason('OTHER_CARRIER', f"The DN's carrier is {dn_carrier.name}, not {carrier.name}",
                                      other_carrier=dn_carrier.name, other_carrier_id=dn_carrier.id), None
        owned = (DNCarrierShipment.query
                 .filter(DNCarrierShipment.dn_id != dn.id, DNCarrierShipment.status.in_(('active', 'cancelling')),
                         DNCarrierShipment.tracking_number.in_(numbers))
                 .first())
        if owned is not None:
            return 'invalid', _reason('NUMBER_OWNED_BY_OTHER_DN',
                                      f"Tracking number {owned.tracking_number} already belongs to DN {owned.dn_id}",
                                      existing_tracking_number=owned.tracking_number, other_dn_id=owned.dn_id), None
        record = current_waybill(dn)
        main = _normalize(numbers[0])
        if record is not None:
            if record.source == 'api' and record.status in DNCarrierShipment.UNRESOLVED_STATUSES:
                return 'invalid', _reason('API_SHIPMENT_UNRESOLVED',
                                          "The carrier shipment request created through the integration is unresolved "
                                          f"({record.status})", shipment_status=record.status), None
            if record.status == 'exported':
                if record.carrier_id != carrier.id:
                    other = db.session.get(Carrier, record.carrier_id) if record.carrier_id else None
                    return 'invalid', _reason('EXPORTED_FOR_OTHER_CARRIER', "The DN was exported for another carrier",
                                              other_carrier=other.name if other is not None else None,
                                              other_carrier_id=record.carrier_id), None
                return 'matched', None, record
            existing = {_normalize(record.tracking_number)} | {_normalize(n) for n in
                                                                (record.package_tracking_numbers or [])}
            if main == _normalize(record.tracking_number) or all(_normalize(n) in existing for n in numbers):
                return 'same_number', _reason('ALREADY_IMPORTED', f"Already imported ({record.tracking_number})",
                                              existing_tracking_number=record.tracking_number), None
            return 'already_has_number', _reason(
                'HAS_OTHER_NUMBER', f"The DN already has tracking number {record.tracking_number}",
                existing_tracking_number=record.tracking_number), None
        if task is not None and task.tracking_number:
            if main == _normalize(task.tracking_number):
                return 'same_number', _reason('ALREADY_SAVED', f"Already saved ({task.tracking_number})",
                                              existing_tracking_number=task.tracking_number), None
            return 'already_has_number', _reason(
                'HAS_OTHER_NUMBER', f"The DN already has tracking number {task.tracking_number}",
                existing_tracking_number=task.tracking_number), None
        if dn.status in SHIPPED:
            return 'invalid', _reason('SHIPPED_WITHOUT_FILE_WAYBILL',
                                      "The DN has been shipped without an exported file waybill"), None
        if task is None or task.status not in OPEN_TASK_STATUSES:
            return 'invalid', _reason('NO_OPEN_TASK', "The DN has no open delivery task (it is not packed yet)"), None
        return 'matched', None, None

    @staticmethod
    def _apply(dn, carrier, numbers, record, batch, user_id):
        """写入一张 DN 的运单号（DN 已锁）：exported → active，没导出过的建 file 运单；同步到发货任务"""
        from warehouse.delivery.services import DeliveryTaskService
        now = clock.now()
        main, rest = numbers[0], numbers[1:]
        data = dict((record.carrier_data if record is not None else None) or {})
        data.update({'preset': carrier.file_preset, 'import_batch_id': batch.id,
                     'imported_at': now.isoformat(timespec='seconds')})
        if record is None:
            record = DNCarrierShipment(
                dn_id=dn.id, source=SOURCE_FILE, carrier=_adapter_code(carrier), carrier_id=carrier.id,
                file_batch_id=batch.id, created_by=user_id, created_at=now)
            db.session.add(record)
        record.status = 'active'
        record.reason = None
        record.tracking_number = main[:100]
        record.package_tracking_numbers = [n[:100] for n in rest] or None
        record.package_count = len(dn.packages) or len(numbers)
        record.carrier_data = data
        record.updated_at = now
        db.session.flush()
        task = current_delivery_task(dn)
        if task is not None and task.status in OPEN_TASK_STATUSES:
            payload = {'tracking_number': record.tracking_number}
            if task.carrier_id is None:
                payload['carrier_id'] = carrier.id
            DeliveryTaskService.save_tracking(task, payload, user_id)
        elif task is not None and not task.tracking_number:
            # 已发货（导出后没等导回就完成了发货）：补上任务上的运单号
            task.tracking_number = record.tracking_number
            if task.carrier_id is None:
                task.carrier_id = carrier.id
        db.session.flush()
        db.session.expire(dn, ['carrier_shipments'])
        return record

    @staticmethod
    def _read(template, content: bytes) -> dict:
        if not content:
            raise _unavailable("The file is empty", 'file', status=400)
        if len(content) > MAX_IMPORT_BYTES:
            raise _unavailable(f"The file must not exceed {MAX_IMPORT_BYTES // (1024 * 1024)} MB", 'file', status=400)
        try:
            return parse_import(template, content, MAX_IMPORT_ROWS)
        except ImportFileError as exc:
            raise _unavailable(str(exc), 'file', status=400) from None

    @staticmethod
    def _plan(parsed):
        """逐行 → 按 DN 分组：→ (lines, groups {dn_id: {numbers, lines}})"""
        lines, groups = [], {}
        for row in parsed['rows']:
            line = {'line': row['line'], 'reference': row['reference'], 'dn_id': row['dn_id'], 'order_number': None,
                    'tracking_numbers': [row['tracking_number']] if row['tracking_number'] else [],
                    'result': None, 'reason_code': None, 'message': None}
            lines.append(line)
            if row['dn_id'] is None:
                line['result'] = 'not_found'
                line.update(_reason('NOT_WMS_REFERENCE', f"Reference {row['reference']} is not a WMS DN reference")
                            if row['reference'] else _reason('NO_REFERENCE', "No reference"))
                continue
            if not row['tracking_number']:
                line['result'] = 'invalid'
                line.update(_reason('NO_TRACKING_NUMBER', "No tracking number"))
                continue
            group = groups.setdefault(row['dn_id'], {'numbers': [], 'lines': []})
            group['lines'].append(line)
            if row['tracking_number'] not in group['numbers']:
                group['numbers'].append(row['tracking_number'])
        return lines, groups

    @staticmethod
    def _result(lines, groups, parsed, dry_run, batch_id, file_name) -> dict:
        summary = {key: 0 for key in RESULTS}
        for line in lines:
            summary[line['result']] += 1
        summary['skipped'] = parsed['skipped']
        summary['dns'] = {key: sum(1 for g in groups.values() if g.get('result') == key) for key in RESULTS}
        return {'dry_run': dry_run, 'file_name': file_name, 'encoding': parsed['encoding'], 'rows': lines,
                'summary': summary, 'batch_id': batch_id}

    @staticmethod
    def _decide(group, dn, carrier):
        result, reason, record = CarrierFileService._evaluate(dn, carrier, group['numbers'])
        group.update(result=result, record=record)
        for index, line in enumerate(group['lines']):
            line['dn_id'] = dn.id if dn is not None else line['dn_id']
            line['order_number'] = dn.order_number if dn is not None else None
            line['result'] = result
            line.update(reason or {'reason_code': None, 'message': None})
            if result == 'matched':
                line['primary'] = index == 0            # 第一个号码为主单号，其余进 package_tracking_numbers

    @staticmethod
    def preview(carrier_id, content: bytes, file_name, warehouse_id) -> dict:
        """dry_run = true：只读"""
        carrier, template = CarrierFileService.file_carrier(carrier_id, for_import=True)
        parsed = CarrierFileService._read(template, content)
        lines, groups = CarrierFileService._plan(parsed)
        for dn_id, group in groups.items():
            CarrierFileService._decide(group, CarrierFileService._dn_for(dn_id, carrier, warehouse_id), carrier)
        return CarrierFileService._result(lines, groups, parsed, True, None, file_name)

    @staticmethod
    @transactional
    def apply(carrier_id, content: bytes, file_name, warehouse_id, user_id) -> dict:
        """dry_run = false：逐张 DN 加锁（按 id 顺序）重新判断，写入 matched 的，记导入批次"""
        carrier, template = CarrierFileService.file_carrier(carrier_id, for_import=True)
        parsed = CarrierFileService._read(template, content)
        lines, groups = CarrierFileService._plan(parsed)
        batch = CarrierFileBatch(carrier_id=carrier.id, warehouse_id=warehouse_id, kind='import',
                                 file_name=(file_name or '')[:255] or None, row_count=len(lines), created_by=user_id,
                                 created_at=clock.now())
        db.session.add(batch)
        db.session.flush()
        applied = []
        for dn_id in sorted(groups):
            group = groups[dn_id]
            dn = CarrierFileService._dn_for(dn_id, carrier, warehouse_id)
            if dn is not None:
                dn = lock_dn(dn)
            CarrierFileService._decide(group, dn, carrier)
            if group['result'] == 'matched':
                CarrierFileService._apply(dn, carrier, group['numbers'], group['record'], batch, user_id)
                applied.append(dn.id)
        result = CarrierFileService._result(lines, groups, parsed, False, batch.id, file_name)
        batch.summary = {**{k: v for k, v in result['summary'].items() if k != 'dns'}, 'dn_ids': applied,
                         'encoding': parsed['encoding']}
        db.session.flush()
        return result

    # ------------------------------------------------------------------
    # 作废导出 / 批次
    # ------------------------------------------------------------------

    @staticmethod
    @transactional
    def discard_export(dn: DN, user_id) -> DN:
        """exported → cancelled（reason discarded），只改本地；没有已导出的文件运单 → 409 16075"""
        dn = lock_dn(dn)
        record = exported_waybill(dn)
        if record is None:
            raise ConflictException("The DN has no exported file waybill to discard.", 16075,
                                    details={'waybill': None})
        now = clock.now()
        record.status = 'cancelled'
        record.reason = 'discarded'
        record.cancelled_at = now
        record.cancelled_by = user_id
        record.updated_at = now
        db.session.flush()
        db.session.expire(dn, ['carrier_shipments'])
        return dn

    @staticmethod
    def batches(carrier_id=None, warehouse_id=None, company_id=None, limit=50) -> list:
        """最近的导出 / 导入批次（新的在前）"""
        query = CarrierFileBatch.query.join(Carrier, Carrier.id == CarrierFileBatch.carrier_id)
        if company_id is not None:
            query = query.filter(Carrier.company_id == company_id)
        if carrier_id is not None:
            query = query.filter(CarrierFileBatch.carrier_id == carrier_id)
        if warehouse_id is not None:
            query = query.filter(CarrierFileBatch.warehouse_id == warehouse_id)
        rows = query.order_by(CarrierFileBatch.id.desc()).limit(limit).all()
        return [row.to_dict() for row in rows if row.warehouse_id is None or check_warehouse_access(row.warehouse_id)]


def get_batch_carrier(carrier_id):
    """批次列表的 carrier_id 参数：不合法 400 16138；别家公司 403 / 没有 404"""
    if carrier_id in (None, ''):
        return None
    carrier = get_company_owned(Carrier, parse_id(carrier_id, 'carrier_id'), 'carrier')
    if carrier is None:
        raise NotFoundException("Carrier not found", 13001)
    return carrier
