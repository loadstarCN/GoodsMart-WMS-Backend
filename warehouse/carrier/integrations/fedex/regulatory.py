"""Build and read FedEx Global Trade regulatory-details requests.

Schema: FedEx Developer Portal, GlobalTrade-Resource.json, Retrieve Regulatory Details.
The response may contain a single object or an array at each detail level.
"""
from .shipment import fedex_currency


def _money(amount, currency):
    return {'amount': float(amount), 'currency': currency}


def _address(party):
    value = {'countryCode': party['country_code']}
    if party.get('postal_code'):
        value['postalCode'] = str(party['postal_code'])
    if party.get('state'):
        value['stateOrProvinceCode'] = str(party['state'])
    return value


def build_regulatory_request(shipment, service_type):
    invoice = shipment['invoice']
    currency = fedex_currency(invoice['currency'])
    commodities = []
    for line in shipment['commodities']:
        item = {
            'harmonizedCode': str(line['hs_code']),
            'description': line['description'],
            'countryOfManufacture': line['origin_country'],
            'quantity': str(line['quantity']),
            'quantityUOM': line.get('quantity_unit') or 'PCS',
            'customsValue': _money(line['amount'], currency),
            'weight': {'units': 'KG', 'value': float(line['weight_kg'])},
        }
        commodities.append(item)
    payload = {
        'carrierCode': 'FDXE',
        'originAddress': _address(shipment['shipper']),
        'destinationAddress': _address(shipment['recipient']),
        'serviceType': service_type,
        'totalCommodityValue': _money(invoice['goods_value'], currency),
        'includeURLReferences': True,
        'totalWeight': {
            'units': 'KG',
            'value': float(sum(p['gross_weight_kg'] for p in shipment['packages'])),
        },
        'customsClearanceDetail': {
            'documentContent': 'COMMODITY',
            'customsValue': _money(invoice['goods_value'], currency),
            'commodities': commodities,
        },
    }
    if shipment.get('ship_date'):
        payload['shipDate'] = shipment['ship_date'].isoformat()
    return payload


def _objects(value):
    if isinstance(value, dict):
        return [value]
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    return []


def _document_nodes(value):
    for item in _objects(value):
        for doc in _objects(item.get('regulatoryDocuments')):
            yield doc
        for statement in _objects(item.get('regulatoryStatements')):
            yield {**statement, 'documentType': 'REGULATORY_STATEMENT',
                   'documentUrl': statement.get('statementUrl') or statement.get('documentUrl')}
        for key in ('countryDetails', 'shipmentDetails', 'commodityDetails'):
            if key in item:
                yield from _document_nodes(item[key])


def _message_nodes(value):
    for item in _objects(value):
        for alert in _objects(item.get('cxsalerts')):
            yield alert
        for message in _objects(item.get('userMessages')):
            yield message
        for advisory in _objects(item.get('regulatoryCountryAdvisories')):
            yield from _objects(advisory.get('messages'))
        for key in ('countryDetails', 'shipmentDetails', 'commodityDetails'):
            if key in item:
                yield from _message_nodes(item[key])


def parse_regulatory_response(body):
    output = body.get('output') if isinstance(body, dict) else None
    if not isinstance(output, dict) or not _objects(output.get('countryDetails')):
        raise ValueError('FedEx Global Trade response has no countryDetails')
    documents = []
    seen = set()
    for doc in _document_nodes(output):
        kind = str(doc.get('documentType') or '').upper()
        localized = doc.get('localizedAttributes')
        name = ((localized.get('name') if isinstance(localized, dict) else None)
                or doc.get('name') or doc.get('fileName') or kind)
        key = (kind, str(doc.get('id') or ''), str(name))
        if (not kind and not name) or key in seen:
            continue
        seen.add(key)
        documents.append({
            'document_type': kind,
            'name': str(name),
            'required': str(doc.get('allowability') or '').upper() in ('REQUIRED', 'MANDATORY'),
            'support_type': str(doc.get('supportType') or '').upper() or None,
            'completion_type': str(doc.get('completionType') or '').upper() or None,
            'document_url': doc.get('documentUrl'),
            'message': (doc.get('userMessage') or {}).get('text'),
        })
    return {
        'source': 'fedex_global_trade',
        'documents': documents,
        'messages': list(dict.fromkeys(str(m.get('text') or m.get('localizedText') or m.get('message') or '').strip()
                                       for m in _message_nodes(output)
                                       if m.get('text') or m.get('localizedText') or m.get('message'))),
        'transaction_id': body.get('transactionId'),
    }
