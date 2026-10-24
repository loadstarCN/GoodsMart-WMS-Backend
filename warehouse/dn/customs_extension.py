"""报关扩展：要报关的件（route.requires_customs 且有报关快照）建运单时额外参与的部分（doc/shipping-flow.md §3）。

国内件（或同一关税同盟内）建运单不碰报关：没有 CI / PL、commodities / invoice / documents 为 None。
扩展只在 applies(dn, route) 为真时由 carrier_services 调用：
- 前置条件：报关视图（CustomsService.build_view）里的错误级问题并入 blockers；收件人按报关快照的 consignee 检查
  姓名 / 电话；运送申告价额高于已打包货值时压到已打包货值（warnings）。
- 交给插件的数据：发件人 = 公司出口资料（英文）+ 仓库英文地址；收件人 = consignee；commodities（按已打包数量，
  净重按商品主数据分摊）；invoice（发票号、币种、贸易条件、运费 / 保险费、货值、申告价额）。
- 建单前：没有当前有效的 CI / PL（或已过期）先签发；插件开了电子单证（documents_enabled）时把 CI / PL 交给插件。
- 贸易单证要求（插件 trade_requirements_enabled，如 FedEx Global Trade）：建单前查目的地和货物要的单证，
  缺的拦下（REGULATORY_DOCUMENT_REQUIRED），操作员补传的随电子单证上传，要纸质的记在运单上。
- 建单后：用同样的数据重签（数据没变不出新版本；建单期间单证数据被改了 → 16076，按写库失败补偿取消）。
"""
import re
from decimal import Decimal, ROUND_FLOOR

from extensions.error import ConflictException
from warehouse.common.address_format import dn_ship_to, latin_view, warehouse_address
from warehouse.common.countries import COUNTRY_NAMES_EN
from .customs_services import CustomsService, DOC_CI, DOC_PL, DOC_TYPES

# 报关视图里已由 blockers 自己表达的问题码（不重复列）
_COVERED_PROBLEMS = ('NOT_PACKED', 'PACKAGES_MISSING')
# 操作员补传的贸易单证：这些类型按原类型上传，其它按 OTHER
UPLOADABLE_DOCUMENT_TYPES = {'CERTIFICATE_OF_ORIGIN', 'USMCA_CERTIFICATION_OF_ORIGIN',
                             'USMCA_COMMERCIAL_INVOICE_CERTIFICATION_OF_ORIGIN', 'PRO_FORMA_INVOICE', 'OTHER'}


def _blocker(code, message, **extra):
    item = {'code': code, 'message': message}
    item.update({k: v for k, v in extra.items() if v is not None})
    return item


def allocate_commodity_weights(lines, gross_total) -> list:
    """每行商品净重（kg，Decimal）。lines: [{quantity, unit_weight_kg|None}]。
    商品主数据有单件重量的行 = 单件重量 × 已打包数量；没有的行分摊「箱子总毛重 − 已知净重」
    （不为正时按数量占比分摊总毛重），按已打包数量比例分。"""
    gross_total = Decimal(str(gross_total or 0))
    known = [Decimal(str(l['unit_weight_kg'])) * l['quantity'] if l.get('unit_weight_kg') is not None else None
             for l in lines]
    unknown_qty = sum(l['quantity'] for l, w in zip(lines, known) if w is None)
    if unknown_qty:
        total_qty = sum(l['quantity'] for l in lines) or 1
        remaining = gross_total - sum(w for w in known if w is not None)
        if remaining <= 0:
            remaining = gross_total * Decimal(unknown_qty) / Decimal(total_qty)
    result = []
    for line, weight in zip(lines, known):
        if weight is None:
            weight = remaining * Decimal(line['quantity']) / Decimal(unknown_qty)
        result.append(weight)
    return result


class CustomsExtension:

    @staticmethod
    def applies(dn, route) -> bool:
        """这票货建运单要带报关扩展：route.requires_customs 且有报关快照"""
        return bool(route and route['requires_customs'] and dn.customs is not None)

    # ------------------------------------------------------------------
    # 前置条件与交给插件的数据
    # ------------------------------------------------------------------

    @staticmethod
    def sender_country(dn) -> str:
        """发件国（报关件）：仓库有英文地址用仓库的国家（没有则公司的），否则公司的国家（与 shipper 同一口径）"""
        warehouse = dn.warehouse
        company = warehouse.company
        if (warehouse.address_en or '').strip():
            return (warehouse.country_code or company.country_code or '').upper()
        return (company.country_code or '').upper()

    @staticmethod
    def shipper(dn, exporter: dict) -> dict:
        """发件人 = 公司出口资料（英文名、电话（仓库优先）、联系人、税号）+ 发货地址 = 仓库英文地址（没有用公司的）。
        地址是一个英文字符串，怎么拆行由插件决定。"""
        warehouse = dn.warehouse
        company = warehouse.company
        if (warehouse.address_en or '').strip():
            text, postal = warehouse.address_en, warehouse.zip_code
        else:
            text, postal = company.address_en, company.zip_code
        country = CustomsExtension.sender_country(dn)
        local = warehouse_address(warehouse)
        return {
            'local': local,
            'latin': latin_view(local),
            'company_name': exporter['legal_name_en'],
            'person_name': company.export_contact_name or warehouse.contact_name_en,
            'phone': exporter['phone'],
            'email': company.email,
            'address_text': text,
            'postal_code': postal,
            'country_code': country,
            'country_name': COUNTRY_NAMES_EN.get(country),
            'tax_id': company.tax_id,
        }

    @staticmethod
    def recipient(customs, consignee: dict, country: str) -> dict:
        return {
            'company_name': consignee.get('company'),
            'person_name': consignee.get('name') or consignee.get('company'),
            'phone': consignee.get('phone'),
            'email': None,
            'address_line1': consignee.get('address_line1'),
            'address_line2': consignee.get('address_line2'),
            'city': consignee.get('city'),
            'state': consignee.get('state'),
            'postal_code': consignee.get('postal_code'),
            'country_code': (country or '').strip().upper(),
            'tax_id': customs.recipient_tax_id,
            'tax_id_type': customs.recipient_tax_id_type,
        }

    @staticmethod
    def declared_value(customs, view):
        """随运单提交的申告价额（整数，报关币种）与说明：高于已打包货值时压到已打包货值（取整数部分）。
        承运商拒收申告价额高于报关货值的运单（FedEx：TOTALCARRIAGEVALUE.EXCEEDS.CUSTOMSVALUE，sandbox 实测）。"""
        requested = customs.declared_value_carriage
        if not requested:
            return None, None
        goods_value = Decimal(str(view['totals']['goods_value'] or 0)).to_integral_value(rounding=ROUND_FLOOR)
        if requested <= goods_value:
            return requested, None
        capped = int(goods_value) or None
        return capped, {
            'code': 'DECLARED_VALUE_CAPPED',
            'message': (f"Declared value for carriage {requested} exceeds the customs value of the packed goods "
                        f"({goods_value}); {capped or 0} is submitted to the carrier instead"),
            'requested': requested,
            'applied': capped,
        }

    @staticmethod
    def collect(dn, ctx: dict, has_shipment: bool) -> dict:
        """报关件的前置条件（并入 ctx['blockers'] / ctx['warnings']）与交给插件的数据 →
        {shipper, recipient, commodities, invoice}；ctx 里补上 view、declared_value。
        has_shipment：已有有效 / 取消中的运单（那时不再提示申告价额会被压低）。"""
        blockers = ctx['blockers']
        customs = dn.customs
        view = CustomsService.build_view(dn)
        ctx['view'] = view
        for problem in view['problems']:
            if problem['level'] != 'error' or problem['code'] in _COVERED_PROBLEMS:
                continue
            blockers.append(_blocker(problem['code'], problem['message'],
                                     goods_code=problem.get('goods_code'), field=problem.get('field')))

        ctx['declared_value'], warning = CustomsExtension.declared_value(customs, view)
        if warning and not has_shipment:
            ctx['warnings'].append(warning)

        exporter = view['exporter']
        shipper = None
        if exporter['legal_name_en'] and (exporter['address_en'] or (dn.warehouse.address_en or '').strip()):
            shipper = CustomsExtension.shipper(dn, exporter)

        consignee = view['consignee'] or {}
        country = view['recipient_country']
        recipient = CustomsExtension.recipient(customs, consignee, country) if country else None
        if recipient is not None:
            # 结构化地址：本地文字用 DN 的收件地址，拉丁文字用报关快照的 consignee（英文）
            recipient['local'] = dn_ship_to(dn)
            recipient['latin'] = {
                'country': recipient['country_code'], 'postal_code': consignee.get('postal_code'),
                'admin_area': consignee.get('state'), 'locality': consignee.get('city'),
                'address_line1': consignee.get('address_line1'), 'address_line2': consignee.get('address_line2'),
                'organization': consignee.get('company'), 'name': consignee.get('name'),
                'phone': consignee.get('phone'),
            }
        if not (consignee.get('name') or consignee.get('company')):
            blockers.append(_blocker('RECIPIENT_NAME_MISSING', "Consignee name and company are both missing",
                                     field='consignee.name'))
        if not re.sub(r'\D', '', str(consignee.get('phone') or '')):
            blockers.append(_blocker('RECIPIENT_PHONE_MISSING', "Consignee phone number is missing",
                                     field='consignee.phone'))
        commodities, invoice = CustomsExtension.commodities_and_invoice(dn, view, ctx['declared_value'])
        return {'shipper': shipper, 'recipient': recipient, 'commodities': commodities, 'invoice': invoice}

    @staticmethod
    def commodities_and_invoice(dn, view: dict, declared_total):
        """交给插件的 commodities（已打包数量 > 0 的行）与 invoice"""
        customs = dn.customs
        lines = [line for line in view['lines'] if line['packed_quantity'] > 0]
        weights = allocate_commodity_weights(
            [{'quantity': l['packed_quantity'], 'unit_weight_kg': l['unit_weight_kg']} for l in lines],
            view['totals']['gross_weight_kg'],
        )
        commodities = [{
            'description': line['description_en'],
            'origin_country': line['origin_country'],
            'hs_code': line['hs_code'],
            'quantity': line['packed_quantity'],
            'quantity_unit': line['quantity_unit'],
            'unit_value': line['unit_value'],
            'amount': line['amount'],
            'weight_kg': weight,
            'part_number': line['goods_code'],
        } for line, weight in zip(lines, weights)]

        totals = view['totals']
        invoice = {
            'invoice_number': view['invoice_number'],
            'currency': customs.currency,
            'incoterm': customs.incoterm,
            'export_reason': customs.export_reason,
            'freight': customs.freight_charge or 0,
            'insurance': customs.insurance_charge or 0,
            'goods_value': totals['goods_value'],
            'declared_value': declared_total if declared_total else None,
        }
        return commodities, invoice

    # ------------------------------------------------------------------
    # 建单前后的单证
    # ------------------------------------------------------------------

    @staticmethod
    def prepare_documents(dn, user_id, view: dict, adapter, shipment: dict):
        """建单前（DN 已锁）：没有当前有效的 CI / PL（或已过期）就先签发（随 pending 记录一起提交）；
        插件开了电子单证时把 CI / PL 放进 shipment['documents']。"""
        current = CustomsService.current_documents(dn)
        if any(t not in current for t in DOC_TYPES) or view['documents_outdated']:
            CustomsService.issue_documents(dn, user_id)
            current = CustomsService.current_documents(dn)
        if adapter.documents_enabled():
            ci = current[DOC_CI]
            pl = current[DOC_PL]
            shipment['documents'] = {
                'commercial_invoice': {'content': ci.content, 'file_name': ci.file_name},
                'packing_list': {'content': pl.content, 'file_name': pl.file_name},
            }

    @staticmethod
    def reissue_after_create(dn, user_id):
        """建单后重签：CI / PL 不印运单号，数据没变时不出新版本；建单期间单证数据被改了（如公司出口资料）→ 16076，
        按写库失败补偿取消（承运商拿到的申报与现在的数据对不上）"""
        CustomsService.issue_documents(dn, user_id)

    # ------------------------------------------------------------------
    # 贸易单证要求（插件 trade_requirements_enabled，如 FedEx Global Trade）
    # ------------------------------------------------------------------

    @staticmethod
    def trade_document_plan(regulatory: dict, etd_enabled: bool) -> dict:
        """Classify FedEx's required documents against the PDFs available for this DN."""
        missing, paper, upload_packing_list = [], [], False
        # Global Trade may mark both invoice variants REQUIRED for one commodity.
        # When a CI is required, the pro forma entry is an invoice alternative.
        commercial_invoice_required = any(
            doc['required'] and doc['document_type'] in ('COMMERCIAL_INVOICE', 'COMMERCIAL_OR_PRO_FORMA_INVOICE')
            for doc in regulatory['documents']
        )
        for doc in regulatory['documents']:
            if not doc['required']:
                continue
            kind = doc['document_type']
            title = (doc['name'] + ' ' + str(doc.get('message') or '')).upper()
            if kind == 'AIR_WAYBILL' or doc.get('completion_type') == 'COMPLETE':
                continue
            available = kind in ('COMMERCIAL_INVOICE', 'COMMERCIAL_OR_PRO_FORMA_INVOICE') or (
                kind == 'PRO_FORMA_INVOICE' and commercial_invoice_required
            )
            packing_list = kind == 'PACKING_LIST' or (kind == 'OTHER' and 'PACKING LIST' in title)
            if doc['support_type'] == 'PRINT_ONLY':
                paper.append(doc)
                continue
            if not etd_enabled:
                paper.append(doc)
                continue
            if not available and not packing_list:
                missing.append(doc)
                continue
            if packing_list:
                upload_packing_list = True
        return {'missing_documents': missing, 'paper_documents': paper,
                'upload_packing_list': upload_packing_list}

    @staticmethod
    def apply_trade_requirements(plan: dict, adapter, regulatory: dict, fail):
        """建单第 2 段查到的贸易单证要求 → 补传的单证、要不要上传 PL、纸质单证；缺单证 / 补传的不对时
        调 fail(reason) 把记录落 failed 后抛 409 16072。"""
        plan['regulatory_requirements'] = regulatory
        document_plan = CustomsExtension.trade_document_plan(regulatory, adapter.documents_enabled())
        required = {(d['document_type'], d['name']) for d in document_plan['missing_documents']}
        supplied = {(d['document_type'], d['name']) for d in plan['supplemental_documents']}
        invalid = supplied - required
        if invalid or len(supplied) != len(plan['supplemental_documents']):
            fail('regulatory_documents_invalid')
            raise ConflictException('Supplemental documents do not match current FedEx requirements', 16072,
                                    details={'regulatory': regulatory})
        missing = [d for d in document_plan['missing_documents']
                   if (d['document_type'], d['name']) not in supplied]
        if supplied and not adapter.documents_enabled():
            missing = document_plan['missing_documents']
        plan['shipment']['upload_packing_list'] = document_plan['upload_packing_list']
        regulatory['paper_documents'] = document_plan['paper_documents']
        if supplied:
            plan['shipment']['documents']['supplemental'] = [
                {**item, 'upload_type': (item['document_type'] if item['document_type'] in UPLOADABLE_DOCUMENT_TYPES
                                         else 'OTHER')}
                for item in plan['supplemental_documents']]
        if missing:
            fail('regulatory_documents_missing')
            raise ConflictException(
                "FedEx requires additional trade documents before this shipment can be created.", 16072,
                details={'blockers': [
                    _blocker('REGULATORY_DOCUMENT_REQUIRED', d['name'], document_type=d['document_type'],
                             document_url=d['document_url']) for d in missing
                ], 'regulatory': regulatory},
            )
