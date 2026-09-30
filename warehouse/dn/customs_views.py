"""DN 海外件接口：报关快照、箱子、出口单证（商业发票 / 装箱单）。

权限：读 = dn_read 或 packing_read；改箱子、出单证 = packing_edit；替换报关快照 = dn_edit。
DN 归属校验与其它 DN 接口一致（跨仓库 403、不存在 404）。
"""
from flask import Response
from flask_restx import Resource

from system.common import permission_required
from warehouse.common import warehouse_required, get_warehouse_owned, require_actor_user_id

from .customs_services import CustomsService
from .models import DN
from .schemas import (
    api_ns,
    dn_customs_input_model,
    dn_packages_input_model,
    dn_packages_result_model,
    dn_document_meta_model,
    dn_customs_documents_parser,
)

_READ = ["all_access", "company_all_access", "dn_read", "packing_read"]
_PACKING_EDIT = ["all_access", "company_all_access", "packing_edit"]
_DN_EDIT = ["all_access", "company_all_access", "dn_edit"]


def _owned_dn(dn_id: int) -> DN:
    return get_warehouse_owned(DN, dn_id, what='DN')


@api_ns.doc(security="jsonWebToken")
@api_ns.route('/<int:dn_id>/customs', '/<int:dn_id>/customs/')
class DNCustomsResource(Resource):

    @permission_required(_READ)
    @warehouse_required()
    def get(self, dn_id):
        """
        Customs view of a DN: snapshot, invoice lines (packed quantity), packages, totals,
        exporter profile, problems and the current documents.
        """
        return CustomsService.build_view(_owned_dn(dn_id)), 200

    @permission_required(_DN_EDIT)
    @warehouse_required()
    @api_ns.expect(dn_customs_input_model)
    def put(self, dn_id):
        """
        Replace the customs snapshot (not allowed after the DN has been shipped: 409 16065).
        - Structure errors: 400 16063 (details.field) / 16064 (goods_code not in the DN or duplicated)
        - Incomplete content is accepted and reported in `problems`
        - If the content changed, current documents are voided (`voided_documents`)
        """
        dn = _owned_dn(dn_id)
        return CustomsService.replace_customs(dn, api_ns.payload, require_actor_user_id()), 200


@api_ns.doc(security="jsonWebToken")
@api_ns.route('/<int:dn_id>/packages', '/<int:dn_id>/packages/')
class DNPackagesResource(Resource):

    @permission_required(_READ)
    @warehouse_required()
    @api_ns.marshal_with(dn_packages_result_model)
    def get(self, dn_id):
        """List the packages of a DN"""
        dn = _owned_dn(dn_id)
        return {'packages': CustomsService.packages_payload(dn), 'voided_documents': []}, 200

    @permission_required(_PACKING_EDIT)
    @warehouse_required()
    @api_ns.expect(dn_packages_input_model)
    @api_ns.marshal_with(dn_packages_result_model)
    def put(self, dn_id):
        """
        Replace all packages of a DN (DN must be picked or packed).
        - 1 - 99 packages; package_no consecutive from 1 (auto-numbered when omitted)
        - gross_weight_kg 0.01 - 999.999 (3 decimals); length/width/height_mm integers 1 - 3000
        - Invalid data: 400 16066; wrong DN status: 409 16067; shipped: 409 16065
        - Changing packages voids issued customs documents (`voided_documents`)
        """
        dn = _owned_dn(dn_id)
        return CustomsService.replace_packages(dn, api_ns.payload, require_actor_user_id()), 200


@api_ns.doc(security="jsonWebToken")
@api_ns.route('/<int:dn_id>/customs-documents/issue')
class DNCustomsDocumentIssue(Resource):

    @permission_required(_PACKING_EDIT)
    @warehouse_required()
    def post(self, dn_id):
        """
        Issue the commercial invoice and packing list (PDF).
        - 201: new version issued (previous version voided)
        - 200: data unchanged, the current version is returned
        - 409 16068: conditions not met (details.problems); 16070: not an export DN; 16065: shipped
        """
        dn = _owned_dn(dn_id)
        status_code, payload = CustomsService.issue_documents(dn, require_actor_user_id())
        return payload, status_code


@api_ns.doc(security="jsonWebToken")
@api_ns.route('/<int:dn_id>/customs-documents/')
class DNCustomsDocumentList(Resource):

    @permission_required(_READ)
    @warehouse_required()
    @api_ns.expect(dn_customs_documents_parser)
    @api_ns.marshal_list_with(dn_document_meta_model)
    def get(self, dn_id):
        """List documents of a DN (`status=issued` for the current ones; `doc_type` to filter,
        e.g. shipping_label for carrier labels)"""
        dn = _owned_dn(dn_id)
        args = dn_customs_documents_parser.parse_args()
        return CustomsService.list_documents(dn, args.get('status'), args.get('doc_type')), 200


@api_ns.doc(security="jsonWebToken")
@api_ns.route('/<int:dn_id>/customs-documents/<int:doc_id>/file')
class DNCustomsDocumentFile(Resource):

    @permission_required(_READ)
    @warehouse_required()
    @api_ns.produces(['application/pdf'])
    def get(self, dn_id, doc_id):
        """Download a document PDF (CI / PL / carrier shipping label; inline; header X-Content-SHA256)"""
        dn = _owned_dn(dn_id)
        doc = CustomsService.get_document(dn, doc_id)
        return Response(
            doc.content,
            mimetype='application/pdf',
            headers={
                'Content-Disposition': f'inline; filename="{doc.file_name}"',
                'X-Content-SHA256': doc.sha256,
                'Access-Control-Expose-Headers': 'Content-Disposition, X-Content-SHA256',
                'Cache-Control': 'private, no-store',
            },
        )
