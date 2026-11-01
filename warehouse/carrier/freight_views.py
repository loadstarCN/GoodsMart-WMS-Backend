"""Carrier invoice endpoints; every file and detail access checks company and warehouse scope."""
from decimal import Decimal
from flask import g, request, make_response
from flask_restx import Resource
from sqlalchemy.orm import defer, selectinload
from extensions.error import BadRequestException
from system.common import permission_required
from system.common.permissions import get_actor_company_id
from warehouse.common import warehouse_required, get_company_owned, clock
from warehouse.goods import tabular
from .models import Carrier
from .schemas import api_ns
from .freight_models import CarrierInvoice
from . import freight_services as service
from . import freight_files as files

READ = ['all_access', 'company_all_access', 'freight_read']
EDIT = ['all_access', 'company_all_access', 'freight_edit']


@api_ns.route('/freight/carriers')
class FreightCarriers(Resource):
    @permission_required(READ + EDIT)
    @warehouse_required()
    def get(self):
        query = Carrier.query
        company_id = get_actor_company_id()
        if company_id is not None:
            query = query.filter(Carrier.company_id == company_id)
        return dict(items=[dict(id=row.id, name=row.name) for row in query.order_by(Carrier.name)])


@api_ns.route('/freight/report')
class FreightReport(Resource):
    @permission_required(READ)
    @warehouse_required()
    def get(self):
        from .freight_report import report
        return report(invoices_query())


@api_ns.route('/freight/dn/<int:dn_id>')
class FreightDocument(Resource):
    @permission_required(READ)
    @warehouse_required()
    def get(self, dn_id):
        from .freight_report import report
        # Reuse the report's company, warehouse, invoice and currency rules;
        # document detail is independent of the report's current date window.
        invoices = invoices_query().filter(CarrierInvoice.lines.any(dn_id=dn_id))
        rows = report(invoices, export=True, dn_id=dn_id)['items']
        return dict(item=rows[0] if rows else None)


@api_ns.route('/freight/report/export')
class FreightReportExport(Resource):
    @permission_required(READ)
    @warehouse_required()
    def get(self):
        from .freight_report import report
        result = report(invoices_query(), export=True)
        headers = ['dn_id','order_number','tracking_number','shipping_date','carrier','service','destination_country',
                   'package_count','actual_weight_kg','invoice_id','invoice_number','invoice_status','billed_weight_kg',
                   'estimate_currency','estimate','billed_amounts','difference','state']
        rows = []
        for row in result['items']:
            # One row per DN: do not repeat the whole-shipment estimate for each invoice.
            invoices = row['invoices']
            rows.append([row['dn_id'],row['order_number'],row['tracking_number'],row['shipping_date'],row['carrier_name'],
                row['service'],row['destination_country'],row['package_count'],row['actual_weight'],
                ' | '.join(str(i['id']) for i in invoices), ' | '.join(i['invoice_number'] for i in invoices),
                ' | '.join(i['status'] for i in invoices),
                ' | '.join(f"{i['invoice_number']}: {row['billed_weights'].get(i['id']) or 'unknown'}" for i in invoices),
                row['estimated_currency'],row['estimated_amount'],
                ' | '.join(f'{unit} {value}' for unit,value in row['billed'].items()),row['difference'],row['state']])
        fmt = request.args.get('format','csv')
        if fmt == 'xlsx':
            content = tabular.write_xlsx('Freight',headers,rows)
        elif fmt == 'csv':
            def safe(value):
                return "'"+value if isinstance(value,str) and (value.lstrip().startswith(('=','+','-','@')) or value.startswith(('\t','\r','\n'))) else value
            content = tabular.write_csv(headers,([safe(value) for value in row] for row in rows),'utf-8')
        else:
            raise BadRequestException('Expected csv or xlsx',field='format')
        return download(content,f"freight-{result['start_date']}-{result['end_date']}.{fmt}",tabular.CONTENT_TYPES[fmt])


def download(content, filename, content_type):
    response = make_response(content)
    response.headers['Content-Type'] = content_type
    response.headers['Content-Disposition'] = tabular.attachment_header(filename)
    response.headers['Access-Control-Expose-Headers'] = 'Content-Disposition'
    response.headers['Cache-Control'] = 'private, no-store'
    return response


def upload():
    file = request.files.get('file')
    if file is None:
        raise BadRequestException('Invoice file is required', field='file')
    return file.read(files.MAX_BYTES+1), file.filename or 'invoice.csv', request.form.to_dict()


def invoices_query():
    query = CarrierInvoice.query.options(defer(CarrierInvoice.file_content), selectinload(CarrierInvoice.lines))
    company_id = get_actor_company_id()
    if company_id is not None:
        query = query.filter(CarrierInvoice.company_id == company_id)
    warehouse_id = getattr(g, 'warehouse_id', None)
    if warehouse_id is not None:
        query = query.filter(CarrierInvoice.warehouse_id == warehouse_id)
    elif company_id is not None and not service.company_wide(company_id):
        query = query.filter(CarrierInvoice.warehouse_id.in_([w.id for w in getattr(g, 'accessible_warehouses', [])]))
    for key in ('carrier_id',):
        if request.args.get(key):
            try:
                value = int(request.args[key])
            except ValueError:
                raise BadRequestException('Invalid carrier ID', field=key)
            get_company_owned(Carrier, value)
            query = query.filter(getattr(CarrierInvoice, key) == value)
    for key in ('currency', 'status'):
        if request.args.get(key):
            query = query.filter(getattr(CarrierInvoice, key) == request.args[key])
    try:
        start = files.iso_date(request.args['start_date'], 'start_date') if request.args.get('start_date') else None
        end = files.iso_date(request.args['end_date'], 'end_date') if request.args.get('end_date') else None
    except ValueError as exc:
        raise BadRequestException(str(exc))
    if start and end and start > end:
        raise BadRequestException('Invalid date range', field='end_date')
    if start:
        query = query.filter(CarrierInvoice.period_to >= start)
    if end:
        query = query.filter(CarrierInvoice.period_from <= end)
    return query.order_by(CarrierInvoice.invoice_date.desc(), CarrierInvoice.id.desc())


@api_ns.route('/<int:carrier_id>/freight-settings')
class FreightSettings(Resource):
    @permission_required(READ + ['carrier_read'])
    @warehouse_required()
    def get(self, carrier_id):
        get_company_owned(Carrier, carrier_id)
        return service.settings_values(carrier_id)

    @permission_required(['all_access', 'company_all_access', 'carrier_edit'])
    @warehouse_required()
    def put(self, carrier_id):
        return service.save_settings(carrier_id, request.get_json(silent=True))


@api_ns.route('/freight/template')
class FreightTemplate(Resource):
    @permission_required(EDIT)
    def get(self):
        sample = ['TRACKING-001', clock.now().date().isoformat(), 'SERVICE', 'JP', '1.000', '1000', '100', '0', '0', '110', '1210']
        return download(tabular.write_csv(files.COLUMNS, [sample], 'utf-8'), 'carrier-invoice-template.csv', 'text/csv; charset=utf-8')


@api_ns.route('/<int:carrier_id>/freight-preview')
class FreightPreview(Resource):
    @permission_required(EDIT)
    @warehouse_required()
    def post(self, carrier_id):
        content, filename, body = upload()
        return service.preview(service.check_file(carrier_id, body, content, filename))


@api_ns.route('/<int:carrier_id>/freight-metadata')
class FreightMetadata(Resource):
    @permission_required(EDIT)
    @warehouse_required()
    def post(self, carrier_id):
        get_company_owned(Carrier, carrier_id)
        content, filename, body = upload()
        return {'metadata': files.inspect_metadata(content, filename,
                service.settings_values(carrier_id)['column_mapping'], body.get('encoding', 'auto'))}


@api_ns.route('/<int:carrier_id>/freight-import')
class FreightImport(Resource):
    @permission_required(EDIT)
    @warehouse_required()
    def post(self, carrier_id):
        content, filename, body = upload()
        return service.import_invoice(carrier_id, body, content, filename), 201


@api_ns.route('/freight/invoices')
class FreightInvoiceList(Resource):
    @permission_required(READ)
    @warehouse_required()
    def get(self):
        try:
            page, per_page = int(request.args.get('page', 1)), int(request.args.get('per_page', 20))
            if page < 1 or not 1 <= per_page <= 100:
                raise ValueError()
        except ValueError:
            raise BadRequestException('Invalid pagination')
        query = invoices_query()
        totals = {}
        for invoice in query:
            total = sum((line.amount for line in invoice.lines), Decimal(0))
            totals[invoice.currency] = totals.get(invoice.currency, Decimal(0)) + total
        return dict(items=[service.invoice_dict(row) for row in query.offset((page-1)*per_page).limit(per_page)],
                    total=query.count(), page=page, per_page=per_page, totals={key: str(value) for key, value in totals.items()})


@api_ns.route('/freight/invoices/<int:invoice_id>')
class FreightInvoiceDetail(Resource):
    @permission_required(READ)
    @warehouse_required()
    def get(self, invoice_id):
        return service.invoice_dict(service.owned_invoice(invoice_id), True)


@api_ns.route('/freight/invoices/<int:invoice_id>/lines/<int:line_id>')
class FreightInvoiceReview(Resource):
    @permission_required(EDIT)
    @warehouse_required()
    def put(self, invoice_id, line_id):
        return service.review_line(invoice_id, line_id, request.get_json(silent=True))


@api_ns.route('/freight/invoices/<int:invoice_id>/confirm')
class FreightInvoiceConfirm(Resource):
    @permission_required(EDIT)
    @warehouse_required()
    def put(self, invoice_id):
        return service.confirm(invoice_id, request.get_json(silent=True))


@api_ns.route('/freight/invoices/<int:invoice_id>/file')
class FreightInvoiceFile(Resource):
    @permission_required(READ)
    @warehouse_required()
    def get(self, invoice_id):
        invoice = service.owned_invoice(invoice_id)
        return download(invoice.file_content, invoice.file_name, tabular.CONTENT_TYPES[invoice.file_format])


@api_ns.route('/freight/invoices/<int:invoice_id>/export')
class FreightInvoiceExport(Resource):
    @permission_required(READ)
    @warehouse_required()
    def get(self, invoice_id):
        invoice = service.owned_invoice(invoice_id)
        rows = []
        for line in invoice.lines:
            value = service.line_dict(line, invoice)
            rows.append([line.tracking_number, line.shipping_date.isoformat(), line.dn_id, line.service,
                invoice.currency, line.estimated_amount, line.amount, value['difference'], value['state'], line.note])
        headers = ['tracking_number', 'shipping_date', 'dn_id', 'service', 'currency', 'estimate', 'invoice_amount', 'difference', 'review', 'note']
        fmt = request.args.get('format', 'csv')
        if fmt == 'xlsx':
            content = tabular.write_xlsx('Invoice', headers, rows)
        elif fmt == 'csv':
            def safe(value):
                return "'"+value if isinstance(value, str) and (value.lstrip().startswith(('=', '+', '-', '@')) or value.startswith(('\t', '\r', '\n'))) else value
            content = tabular.write_csv(headers, ([safe(value) for value in row] for row in rows), 'utf-8')
        else:
            raise BadRequestException('Expected csv or xlsx', field='format')
        return download(content, f'carrier-invoice-{invoice.id}.{fmt}', tabular.CONTENT_TYPES[fmt])
