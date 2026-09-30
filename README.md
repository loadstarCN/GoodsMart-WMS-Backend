# GoodsMart WMS API Service

[中文 README](./README-zh.md)

GoodsMart WMS (Warehouse Management System) API is a multi-tenant warehouse management backend service built on Flask. It provides comprehensive RESTful API interfaces supporting inbound/outbound management, inventory control, warehouse operations, and RBAC permission management.

> **Client Support**:
> - Web: https://github.com/loadstarCN/GoodsMart-WMS-Web
> - Developers can build custom clients based on the API

## License

This project is licensed under the **GNU Affero General Public License v3.0 (AGPL-3.0)**.

- Free to use, modify, and distribute
- Derivative works must remain open source under the same license
- Commercial use requires separate authorization — contact the author for a commercial license

## Tech Stack

- **Backend Framework**: Flask 3.1.x
- **Database**: PostgreSQL 13+
- **ORM**: SQLAlchemy + Flask-SQLAlchemy
- **Authentication**: JWT (Flask-JWT-Extended)
- **Authorization**: RBAC (Role-Based Access Control)
- **API Documentation**: OpenAPI / Swagger (Flask-RESTx)
- **Caching**: Redis
- **Object Storage**: Alibaba Cloud OSS
- **Scheduled Tasks**: Flask CLI + cron

## Prerequisites

- Python 3.10+
- PostgreSQL 13+
- Redis 6.x+
- pip

## Quick Start

### 1. Clone

```bash
git clone https://github.com/loadstarCN/GoodsMart-WMS-Backend.git
cd GoodsMart-WMS-Backend
```

### 2. Virtual Environment

```bash
python -m venv venv
source venv/bin/activate  # Linux/Mac
# or
venv\Scripts\activate     # Windows

pip install -r requirements.txt
```

### 3. Configuration

```bash
cp .env.example .env
```

Edit `.env`:

```env
FLASK_ENV=development
FLASK_DEBUG=True

# Database
SQLALCHEMY_DATABASE_URI=postgresql://user:password@localhost:5432/warehouse
SQLALCHEMY_DATABASE_URI_DEV=postgresql://user:password@localhost:5432/warehouse

# JWT
JWT_SECRET_KEY=your_secure_random_key

# Redis
REDIS_URL=redis://localhost:6379/0

# OSS (Alibaba Cloud)
OSS_ACCESS_KEY_ID=your_key
OSS_ACCESS_KEY_SECRET=your_secret
OSS_ENDPOINT=oss-your-region.aliyuncs.com
OSS_BUCKET_NAME=your-bucket
OSS_HOST=https://your-bucket.oss-your-region.aliyuncs.com
```

### 4. Database Setup

```bash
flask db init
flask db migrate -m "Initial migration"
flask db upgrade
```

### 5. Seed Permissions (First Time)

```bash
python seed_permissions.py
```

### 6. Start

```bash
python app.py
```

The API runs at http://localhost:5000.

## Project Structure

```
GoodsMart-WMS-Backend/
├── app.py                     # Application entry point
├── config.py                  # Environment-based configuration
├── seed_permissions.py        # Permission seeding script
├── extensions/                # Flask extensions
│   ├── db.py                  # SQLAlchemy
│   ├── jwt.py                 # JWT authentication
│   ├── redis.py               # Redis client
│   ├── cache.py               # Cache layer
│   ├── oss.py                 # Alibaba Cloud OSS
│   ├── limiter.py             # Rate limiting
│   ├── error.py               # Custom exceptions
│   └── transaction.py         # Transaction decorator
├── system/                    # System modules
│   ├── user/                  # Users, roles, permissions (RBAC)
│   ├── third_party/           # API key management
│   ├── webhook/               # Webhook event queue & push
│   ├── logs/                  # Request logging
│   ├── limiter/               # IP whitelist/blacklist
│   └── common/                # Shared utilities (pagination, etc.)
├── warehouse/                 # Business modules
│   ├── company/               # Tenant/company management
│   ├── staff/                 # Staff management
│   ├── department/            # Department management
│   ├── warehouse/             # Warehouse management
│   ├── location/              # Storage location management
│   ├── goods/                 # Product management
│   ├── inventory/             # Inventory management
│   ├── inventory_snapshot/    # Inventory snapshots
│   ├── supplier/              # Supplier management
│   ├── carrier/               # Carrier management
│   ├── recipient/             # Recipient management
│   ├── asn/                   # Inbound: ASN (Advanced Shipping Notice)
│   ├── sorting/               # Inbound: Sorting
│   ├── putaway/               # Inbound: Putaway
│   ├── dn/                    # Outbound: DN (Delivery Note)
│   ├── picking/               # Outbound: Picking
│   ├── packing/               # Outbound: Packing
│   ├── delivery/              # Outbound: Delivery
│   ├── payment/               # Outbound: Payment/COD
│   ├── adjustment/            # Inventory: Stock adjustment
│   ├── cyclecount/            # Inventory: Cycle counting
│   ├── transfer/              # Inventory: Stock transfer
│   └── removal/               # Inventory: Stock removal
├── tasks/                     # Scheduled tasks
│   ├── commands.py            # Flask CLI (flask snapshot run)
│   ├── snapshot.py            # Inventory snapshot logic
│   └── views.py               # Task trigger API
├── migrations/                # Alembic database migrations
├── tests/                     # Test suite
└── doc/                       # SQL migration scripts
```

## API Modules

### System Management
- **Users**: Registration, login, JWT token management
- **Roles & Permissions**: RBAC with granular permission control (84 permissions)
- **API Keys**: Third-party system integration with company-level isolation
- **Logs**: Request logging and audit trail
- **IP Control**: Whitelist/blacklist management

### Master Data
- **Companies**: Multi-tenant company management
- **Staff**: Employee management bound to companies
- **Warehouses**: Warehouse and storage location management
- **Products**: Product information with categories, pricing, and images
- **Suppliers / Carriers / Recipients**: Trading partner management

### Inbound (ASN)
- ASN creation and management
- Goods receiving
- Quality sorting (actual quantity, damage tracking)
- Putaway to storage locations

### Outbound (DN)
- Delivery note creation and management
- Order picking
- Packing
- Delivery with tracking number (or create the shipment in FedEx automatically)
- Payment/COD management

### Inventory Operations
- Real-time inventory tracking (multi-stage: ASN → received → sorted → onhand → DN → picked → packed → delivered)
- Stock adjustment (increase/decrease with reason tracking)
- Cycle counting (with approval workflow)
- Stock transfer between locations
- Stock removal
- Inventory snapshots (daily scheduled)

## Webhook Integration

WMS pushes event notifications to external systems via Webhook with HMAC-SHA256 signature verification.

### Supported Events

| Event | Trigger |
|-------|---------|
| `asn.received` | ASN marked as received |
| `asn.completed` | ASN completed with actual quantities |
| `dn.in_progress` | DN processing started |
| `dn.delivered` | DN delivered (includes tracking number) |
| `dn.completed` | DN completed |
| `goods.spec_updated` | Goods weight / dimensions / country of origin changed (company-wide, subscription required — see below) |

Document events (`asn.*`, `dn.*`) are sent only to the API Key that created the document.
`asn.completed` details also carry the goods master data: `goods_weight_kg`, `goods_length_mm`,
`goods_width_mm`, `goods_height_mm` and `goods_origin_country` (ISO 3166-1 alpha-2, `null` = not recorded).

### Setup

1. Configure webhook URL and secret on an API Key:

```sql
UPDATE api_keys
SET webhook_url    = 'https://your-system.example.com/webhook',
    webhook_secret = 'your-hmac-secret'
WHERE key = 'your-api-key';
```

2. Set up the push job (every minute):

```bash
* * * * * cd /path/to/project && flask webhook push >> /var/log/wms-webhook.log 2>&1
```

### Payload Format

Events are POSTed as JSON with headers:
- `X-Webhook-Event`: Event type (e.g., `dn.delivered`)
- `X-Webhook-Signature`: `sha256=<HMAC-SHA256 hex digest>`

Failed deliveries are retried every 30 minutes, up to 10 attempts (`system/webhook/services.py`).

### `goods.spec_updated` (company-wide subscription)

Sent to **every** API Key of the goods' company that is active, has a `webhook_url`, and lists the
event in `webhook_subscriptions`. Keys not bound to a company never receive it. Subscribe via the
API Key endpoints (only `goods.spec_updated` is accepted; anything else → 400 `14018`):

```http
PUT /system/third-party/api-keys/<id>
{ "webhook_subscriptions": ["goods.spec_updated"] }
```

The event is recorded (in the same transaction) whenever creating or updating a goods changes any of
weight, length, width, height or origin country. The payload is a full snapshot of the current values
(`null` = not recorded):

```json
{ "goods_code": "4900000000000", "goods_id": 1,
  "goods_weight_kg": 0.235, "goods_length_mm": 120, "goods_width_mm": 80, "goods_height_mm": 45,
  "goods_origin_country": "CN",
  "changed_fields": ["goods_weight_kg", "goods_origin_country"],
  "source": "station", "changed_at": "2026-01-01T10:00:00+09:00" }
```

- `source`: `spec_source` from the goods create/update request body (`station` / `manual` / `import` / `api`);
  otherwise `api` for API Key calls and `manual` for logged-in users. CSV import uses `import`.
- While an event for the same goods is still pending for a key, a newer change overwrites its payload
  instead of queueing another one (`dedupe_key = goods:<id>`; `changed_fields` becomes the union).
- Headers, signatures and retries are the same as for the other events.

Goods `origin_country` accepts ISO 3166-1 alpha-2 codes only (upper-cased; empty string clears it;
anything else → 400 `10014`). There is no default value. `GET /warehouse/goods/?origin_missing=true`
lists goods without it. Warehouse operators can set only the origin with
`PUT /warehouse/goods/<id>/origin-country` (`{"origin_country": "CN" | "" | null, "spec_source"?: ...}`),
which requires any of `goods_edit`, `sorting_edit` or `packing_edit` and also emits `goods.spec_updated`.
The CSV import accepts an optional `origin_country` column (for existing goods,
`append` only fills empty values and `override` only overwrites with non-empty values).

## Inventory Snapshot

Daily inventory snapshots for historical analysis:

```bash
# Manual
flask snapshot run

# Crontab (daily at 2 AM)
0 2 * * * cd /path/to/project && flask snapshot run >> /var/log/wms-snapshot.log 2>&1
```

Or via API:
```http
POST /tasks/task/inventory_snapshot
Authorization: Bearer <token>
```

## Export Shipments: Customs Snapshot, Packages and Documents

For cross-border shipments the WMS keeps a customs snapshot per DN, records the packages, and issues a
**Commercial Invoice (CI)** and **Packing List (PL)** as PDF (A4, English, generated with
[reportlab](https://www.reportlab.com/), BSD license). A DN with a customs snapshot is an *export DN*
(`is_export: true` in DN list / detail and in the DN nested in packing / delivery tasks).

### Exporter profile (company / warehouse)

All exporter data comes from the WMS company / warehouse settings — nothing is hard-coded.

| Entity | Fields |
|--------|--------|
| Company (`PUT /warehouse/company/<id>`) | `legal_name_en`, `address_en`, `country_code` (ISO 3166-1 alpha-2, default `JP`), `tax_id_label`, `tax_id`, `export_contact_name`, `export_signatory_name`, `export_signatory_title` |
| Warehouse (`PUT /warehouse/warehouse/<id>`) | `address_en`, `country_code`, `contact_name_en` (printed as *Ship From* when it differs from the company address) |

Invalid `country_code` → 400 `14020`; text longer than the column → 400 `14019`.

### Customs snapshot

`POST /warehouse/dn/` accepts a top-level `customs` object (omit it for domestic shipments);
`PUT /warehouse/dn/<id>/customs` replaces it (permission `dn_edit`, not allowed after shipping).

```json
{ "invoice_number": "INV-0001", "currency": "JPY", "incoterm": "DAP", "export_reason": "SALE",
  "recipient_country": "DE", "recipient_tax_id": "DE123456789", "recipient_tax_id_type": "EORI",
  "freight_charge": 8200, "insurance_charge": null, "declared_value_carriage": null,
  "consignee": { "name": "...", "company": "...", "address_line1": "...", "address_line2": null,
                 "city": "...", "state": null, "postal_code": "...", "country": "DE", "phone": "..." },
  "lines": [ { "goods_code": "4900000000000", "quantity": 3, "unit_value": 1200, "total_value": 3600,
               "description_en": "Plastic figure", "hs_code": "9503.00", "jp_export_code": "950300000",
               "origin_country": "CN", "quantity_unit": "PCS" } ] }
```

- Structure errors are rejected: `customs` not an object, `lines` not an array, wrong field types → 400 `16063`
  (`details.field`); a line whose `goods_code` is not in the DN details or is duplicated → 400 `16064`.
- Incomplete content is accepted and reported as `problems` (see below).
- `invoice_number` defaults to the DN `order_number`. Invoice quantities are always the **packed** quantities
  (amount = unit value × packed quantity; lines with nothing packed are left out). The country of origin comes
  from the goods master data (`goods.origin_country`); the value in the line is recorded only.
- `freight_charge` is printed as a separate *Freight* line and included in the *Total Invoice Value*.
- Optional shipping insurance: `insurance_charge` (non-negative integer or `null`) is printed as an *Insurance*
  line under *Freight* when it is greater than 0 and included in the *Total Invoice Value*;
  `declared_value_carriage` (non-negative integer or `null`) is the declared value for carriage that the
  warehouse enters when booking the shipment in the carrier's system — it is shown in the customs view but
  not printed. Both are optional (requests without them work as before); invalid values → 400 `16063`.
  Changing either of them voids the issued documents; snapshots without them keep their document fingerprint.
- `jp_export_code` (optional, 9-digit Japanese export statistics code whose first 6 digits equal the HS code)
  is stored and returned but not printed on the CI / PL (they print the HS code only).
- Replacing the snapshot voids the issued documents only when the printed content changes.

`GET /warehouse/dn/<id>/customs` (permission `dn_read` or `packing_read`) returns
`{dn_id, is_export, locked, customs, lines[], packages[], totals, exporter, problems[], ready, current_documents[], documents_outdated}`.
`totals = {quantity, goods_value, freight, insurance, invoice_total, package_count, gross_weight_kg, net_weight_kg}`
with `invoice_total = goods_value + freight + insurance` (`insurance` is 0 when not insured).

| Problem (error) | Meaning |
|-----------------|---------|
| `NOT_PACKED` | DN is not packed yet (or nothing is packed) |
| `PACKAGES_MISSING` | No packages recorded |
| `EXPORTER_PROFILE_INCOMPLETE` | Company `legal_name_en` / `address_en` / phone (warehouse or company) / country missing |
| `INCOTERM_MISSING`, `EXPORT_REASON_MISSING`, `CURRENCY_INVALID`, `RECIPIENT_COUNTRY_MISSING` | Header data missing |
| `LINE_MISSING`, `HS_CODE_MISSING`, `DESCRIPTION_MISSING`, `DESCRIPTION_NOT_ASCII`, `ORIGIN_MISSING`, `UNIT_VALUE_MISSING` | Per packed line (HS code: 6–10 digits after removing `.`, spaces and `-`) |

Warnings (do not block): `RECIPIENT_TAX_ID_MISSING`, `NON_LATIN_TEXT`, `NET_WEIGHT_UNKNOWN`, `GROSS_LT_NET`,
`JP_EXPORT_CODE_MISMATCH` (`jp_export_code` not 9 digits or its first 6 digits differ from the HS code),
`JP_EXPORT_CODE_MISSING` (JPY invoice total — goods value + freight + insurance — above 200,000 and a line has no `jp_export_code`).

### Packages

`GET / PUT /warehouse/dn/<id>/packages` (read: `dn_read` or `packing_read`; write: `packing_edit`).
The DN must be `picked` or `packed` (otherwise 409 `16067`).

```json
{ "packages": [ { "package_no": 1, "gross_weight_kg": 3.25, "length_mm": 400, "width_mm": 300, "height_mm": 250, "remark": null } ] }
→ 200 { "packages": [...], "voided_documents": [12, 13] }
```

1–99 packages, numbered consecutively from 1 (auto-numbered when omitted); gross weight 0.01–999.999 kg
(3 decimals); dimensions 1–3000 mm (integers). Invalid data → 400 `16066`. Changing the packages voids
the issued documents (`void_reason: packages_changed`).

### Documents

| Method | Path | Permission |
|--------|------|------------|
| `POST` | `/warehouse/dn/<id>/customs-documents/issue` | `packing_edit` |
| `GET` | `/warehouse/dn/<id>/customs-documents/?status=issued` | `dn_read` / `packing_read` |
| `GET` | `/warehouse/dn/<id>/customs-documents/<doc_id>/file` | `dn_read` / `packing_read` |

- `issue` requires: DN `packed`, customs snapshot, at least one package, complete exporter profile and no
  error-level problems (otherwise 409 `16068` with `details.problems`; not an export DN → 409 `16070`).
- The CI and PL are issued together and stored in the database (`dn_documents`), final once generated.
  Issuing again with unchanged data returns the current version (200); if the data changed (including the
  AWB / tracking number) the old version is voided and a new version is issued (201).
- `file` returns `application/pdf` inline with the header `X-Content-SHA256`.
- The CI prints the AWB number when the delivery task already has a tracking number. Save it before
  shipping with `PUT /warehouse/delivery/<task_id>/tracking` `{ "tracking_number": "...", "carrier_id": 1 }`
  (permission `delivery_edit`; 409 `16065` after shipping), then issue again (`documents_outdated: true`
  in the customs view means the current documents no longer match the data).

### Shipping gate and lock

- Completing the delivery task of an export DN requires a current CI **and** PL → otherwise 409 `16069`.
  If the tracking number given at completion differs from the saved one, the request wins; the existing
  documents are still accepted.
- After the DN is shipped (`delivered` / `completed`) the customs snapshot, packages and documents are locked → 409 `16065`.
- `dn.delivered` for export DNs additionally carries (domestic payloads are unchanged):

```json
"customs_documents": [ { "id": 88, "doc_type": "commercial_invoice", "version": 2, "document_number": "INV-0001",
    "invoice_date": "2026-01-01", "issued_at": "...", "sha256": "...", "size_bytes": 48213,
    "file_name": "CI_INV-0001_v2.pdf", "download_path": "/warehouse/dn/123/customs-documents/88/file" }, { "...": "packing_list" } ],
"packages": [ { "package_no": 1, "gross_weight_kg": 3.25, "length_mm": 400, "width_mm": 300, "height_mm": 250 } ],
"invoice_total": { "currency": "JPY", "goods_value": 45600, "freight": 8200, "insurance": 0, "total": 53800 }
```

### Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `DOCUMENT_TIMEZONE` | `Asia/Tokyo` | Time zone of the invoice date |
| `CUSTOMS_PDF_FONT_PATH` | (unset) | Optional TTF font for the documents; default Helvetica. Characters the font cannot print (e.g. Japanese) fall back to reportlab's built-in CID font |

## Carrier Integration (FedEx)

For export DNs whose delivery task uses a carrier with code `fedex`, WMS can create the shipment in FedEx
(Ship API) instead of creating it by hand on the FedEx website and typing the tracking number. Everything
else stays the same: the tracking number is saved on the delivery task by the existing tracking logic, the
CI / PL are re-issued with the AWB, shipping completes and `dn.delivered` is sent as before. Saving a tracking
number by hand (`PUT /warehouse/delivery/<task_id>/tracking`) remains the fallback when FedEx is unavailable.

| Method | Path | Permission |
|--------|------|------------|
| `GET` | `/warehouse/dn/<id>/carrier-shipment` | `dn_read` / `packing_read` |
| `POST` | `/warehouse/dn/<id>/carrier-shipment` | `packing_edit` / `delivery_edit` |
| `POST` | `/warehouse/dn/<id>/carrier-shipment/cancel` | `packing_edit` / `delivery_edit` |

`GET` returns `{enabled, carrier: "fedex", can_create, blockers[{code, message, goods_code?, field?}], etd_enabled,
default_label_format, label_formats{A4|THERMAL: {image_type, stock_type}}, declared_value_carriage, delivery_task_id,
shipment}`; `shipment` is the latest shipment (active first) or `null`:
`{tracking_number, package_tracking_numbers, status: active|cancelled, service_type, ship_date, package_count,
net_charge, currency, declared_value, label_format, image_type, label_stock_type, label_document_id,
label_download_path, label_file_name, label_content_type, label_parts[], etd_document_id, transaction_id,
created_at, created_by, cancelled_at, cancelled_by}`.

`POST` takes an optional body `{"label_format": "A4" | "THERMAL"}` (how the label will be printed; omitted =
`FEDEX_DEFAULT_LABEL_FORMAT`; anything else → 400 `16077`):

| `label_format` | Printer | Default image / stock | Pages |
|----------------|---------|-----------------------|-------|
| `A4` | Laser printer, plain A4 / Letter paper | `PDF` / `PAPER_85X11_TOP_HALF_LABEL` | Letter page, label on the top half, folding instructions below |
| `THERMAL` | 4-inch label printer used through its driver | `PDF` / `STOCK_4X6` | 4 × 6 in (≈ 100 × 150 mm) pages |

Do not use `PAPER_4X6` for label printers: FedEx returns a Letter page with the label in the top-left corner.
Each format's image type (`PDF` / `PNG` / `ZPLII` / `EPL2`) and stock can be overridden by configuration.

### Creating a shipment

- **Blockers** (all listed at once; `POST` answers 409 `16072` with `details.blockers`): FedEx not configured
  (`FEDEX_NOT_CONFIGURED`) or invalid options (`FEDEX_CONFIG_INVALID`), not an export DN (`NOT_EXPORT`), DN not
  `packed` / already shipped, no active delivery task / its carrier code is not `fedex` / task completed, an active
  shipment already exists (`SHIPMENT_EXISTS`) or a tracking number was saved by hand (`TRACKING_NUMBER_EXISTS`),
  no packages / more than 30 packages, error-level customs problems (same codes as the customs view),
  declared value for carriage above the customs value of the packed goods
  (`DECLARED_VALUE_EXCEEDS_CUSTOMS_VALUE` — FedEx rejects it), ship-from or consignee address that does not fit the
  FedEx format (`SHIPPER_ADDRESS_INVALID` / `RECIPIENT_ADDRESS_INVALID`), consignee name or phone missing.
- If there is no current CI / PL (or they are outdated) they are issued first with the normal logic.
- With `FEDEX_ETD_ENABLED` the current CI PDF is uploaded first (Trade Documents Upload API, pre-shipment) and
  the shipment references it (`ELECTRONIC_TRADE_DOCUMENTS`); without it the warehouse prints the CI and packs it
  with the goods as before.
- The request is built from the DN: shipper = company export profile (English name, phone — warehouse first,
  contact, tax ID) with the warehouse English address (company address if the warehouse has none); consignee
  and recipient tax ID from the customs snapshot (tax ID types mapped to FedEx `tinType`: EORI → `BUSINESS_UNION`,
  PCCC / CPF → `PERSONAL_NATIONAL`, others → `BUSINESS_NATIONAL`); commodities from the invoice lines
  (**packed quantity**, HS code, country of origin from the goods master data, unit value, amount, weight);
  one package line per WMS package (gross weight kg, dimensions cm; order number and invoice number as label
  references); the declared value for carriage is split
  evenly across the packages; freight / insurance as in the snapshot; transportation billed to the account,
  duties per `FEDEX_DUTIES_PAYMENT_TYPE`.
- Address rules: FedEx accepts at most 3 street lines of 35 characters and a city of 35 characters. The English
  address string is split at commas / line breaks — the last part (after removing the country name and the postal
  code) is the city, e.g. `1-2-3 Example, Minato-ku, Tokyo 105-0000`. The postal code is the warehouse / company
  `zip_code` (for Japan it is also recognised in the address). A consignee without a postal code is sent with an
  empty `postalCode`; US / CA / PR require a 2-letter state code.
- Commodity weight: goods with a unit weight → unit weight × packed quantity; goods without one share the rest of
  the total gross weight (or, if nothing is left, the gross weight by quantity).
- FedEx is called **before** anything is written. On success, in one transaction: tracking number → delivery task,
  CI / PL re-issued with the AWB, the label archive stored as a document with `doc_type: shipping_label`, and a
  `dn_carrier_shipments` record. The archive holds **every** document of the response (package labels →
  auxiliary documents → others; for international Express the "FEDEX AWB COPY" pages come inside the first
  package's label, nothing extra has to be requested): PDF / PNG are merged into one PDF, ZPLII / EPL2 are
  concatenated raw printer commands (`.zpl` / `.epl`, `application/octet-stream`). `label_parts` lists each
  document (source, package, content type, doc type, pages, archived). FedEx errors → 502 `16073` (`details.errors`,
  `transaction_id`; nothing saved), timeouts → 504 `16074` (`details.maybe_processed: true` means the shipment may
  exist on the FedEx side — check before retrying). If saving fails after FedEx created the shipment, WMS asks
  FedEx to cancel it and logs the result.
- The label is downloaded with `GET /warehouse/dn/<id>/customs-documents/<label_document_id>/file` (PDF inline,
  ZPL / EPL as attachment). Labels
  do not count for the "current CI and PL" shipping gate. While a shipment is active, changing the packages is
  refused (409 `16076`) — cancel the shipment first.

### Cancelling

Allowed until the DN is shipped (409 `16065` afterwards; 409 `16075` without an active shipment). FedEx
`PUT /ship/v1/shipments/cancel` (`DELETE_ALL_PACKAGES`); if FedEx refuses → 502 `16073` with the reason. On
success the record is marked `cancelled`, the tracking number on the delivery task is cleared, the label is voided
(`void_reason: shipment_cancelled`) and the CI / PL are re-issued without the AWB (voided if they can no longer be issued).

### Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `FEDEX_API_BASE` | `https://apis-sandbox.fedex.com` | `https://apis.fedex.com` for production |
| `FEDEX_API_KEY` / `FEDEX_SECRET_KEY` / `FEDEX_ACCOUNT_NUMBER` | (unset) | Credentials of the FedEx developer project; any one missing = feature disabled |
| `FEDEX_SERVICE_TYPE` | `INTERNATIONAL_ECONOMY` | FedEx service type |
| `FEDEX_PICKUP_TYPE` | `USE_SCHEDULED_PICKUP` | Pickup type |
| `FEDEX_DEFAULT_LABEL_FORMAT` | `A4` | Label format when the request does not give one (`A4` / `THERMAL`) |
| `FEDEX_LABEL_A4_IMAGE_TYPE` / `FEDEX_LABEL_A4_STOCK_TYPE` | `PDF` / `PAPER_85X11_TOP_HALF_LABEL` | Override for `A4` |
| `FEDEX_LABEL_THERMAL_IMAGE_TYPE` / `FEDEX_LABEL_THERMAL_STOCK_TYPE` | `PDF` / `STOCK_4X6` | Override for `THERMAL` (e.g. `ZPLII` for Zebra printers) |
| `FEDEX_ETD_ENABLED` | `False` | Electronic trade documents (upload the CI) |
| `FEDEX_DUTIES_PAYMENT_TYPE` | `RECIPIENT` | `RECIPIENT` or `SENDER` (the account) |
| `FEDEX_DOCUMENT_API_BASE` | (derived) | Trade Documents Upload host; derived from `FEDEX_API_BASE` (sandbox / production) |
| `FEDEX_CONNECT_TIMEOUT_SECONDS` / `FEDEX_TIMEOUT_SECONDS` | `5` / `30` | Timeouts |

The OAuth token (`/oauth/token`, client credentials) is cached in the process until shortly before it expires.
Credentials are never logged or returned. Merging multi-package PDF labels uses [pypdf](https://pypi.org/project/pypdf/) (BSD).

### Sandbox smoke test and label certification

`scripts/fedex_sandbox_smoke.py` creates test shipments in the FedEx **sandbox** for each label format and package
count, saves the labels as `<service>_<n>pkg_<format>.<ext>` and cancels the shipments (it refuses to run unless
`FEDEX_API_BASE` contains `sandbox`). Shipper and recipient are fictitious unless given as JSON files (same fields
as the exporter profile / customs consignee):

```bash
python scripts/fedex_sandbox_smoke.py [--label-format A4|THERMAL|both] [--packages 1,2] [--to US|DE|HK] \
    [--shipper-json shipper.json] [--recipient-json recipient.json] [--etd] [--out ./fedex-sandbox-labels] [--env-file .env]
```

Before production use, FedEx requires label certification: print the sandbox labels of the services you use,
scan them and send them with the label cover sheet to FedEx (see developer.fedex.com → Certification). After
approval switch `FEDEX_API_BASE` and the credentials to production.

## Deployment

### Production (Gunicorn)

```bash
pip install gunicorn
gunicorn -w 4 -b 0.0.0.0:5000 "app:create_app()"
```

### Supervisord

```ini
[program:wms-api]
directory=/path/to/GoodsMart-WMS-Backend
command=gunicorn -w 4 -b 0.0.0.0:5000 "app:create_app()"
autostart=true
autorestart=true
environment=FLASK_ENV="production"
stderr_logfile=/var/log/wms-api.err.log
stdout_logfile=/var/log/wms-api.out.log
```

## Error Codes

| Category | Range | HTTP | Description |
|----------|-------|------|-------------|
| General | 10000-10999 | 400 | Business logic errors |
| Authentication | 11000-11999 | 401 | Identity verification |
| Permission | 12000-12999 | 403 | Insufficient access |
| Resource | 13000-13999 | 404 | Resource not found |
| Validation | 14000-14999 | 400 | Data format errors |
| Inventory | 15000-15999 | 400 | Stock-related errors |
| State | 16000-16999 | 400 | State transition errors |

Business codes of the export-document features: `14019` export profile text too long, `14020` invalid country code, `16063` customs structure invalid, `16064` customs line goods code not in the DN / duplicated, `16065` shipped — customs data / packages / documents locked (409), `16066` invalid packages, `16067` packages cannot be edited in the current DN status (409), `16068` documents cannot be issued yet (409, `details.problems`), `16069` documents required before shipping (409), `16070` not an export DN (409), `16071` document not found (404). Carrier shipments (FedEx): `16072` preconditions not met (409, `details.blockers`), `16073` FedEx returned an error (502, `details.errors` / `transaction_id`), `16074` FedEx timed out (504), `16075` no active carrier shipment (409), `16076` packages cannot be changed while a carrier shipment is active (409), `16077` invalid `label_format` (400). Errors may carry a structured `details` object.

## Related Projects

- https://github.com/loadstarCN/GoodsMart-WMS - System documentation
- https://github.com/loadstarCN/GoodsMart-WMS-Web - Web frontend

## Contributing

1. Fork the project
2. Create a feature branch (`git checkout -b feature/AmazingFeature`)
3. Commit changes (`git commit -m 'Add AmazingFeature'`)
4. Push (`git push origin feature/AmazingFeature`)
5. Open a Pull Request

---

**Note**: This project uses AGPLv3 license. Commercial use requires separate authorization.
