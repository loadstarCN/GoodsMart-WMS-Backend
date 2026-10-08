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
# for running the tests (pytest, fakeredis, …) install the dev requirements instead:
pip install -r requirements-dev.txt
```

Production installs `requirements.txt` only; test / development tools live in `requirements-dev.txt`
(it starts with `-r requirements.txt`). Run the tests with `./run_tests.sh` (or `python -m pytest tests/`);
the script stops with a hint when the dev requirements are missing.

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

Boolean switches (`DEBUG`, `WEBHOOK_URL_STRICT`, `RATELIMIT_ENABLED`, `SCHEDULER_ENABLED`, `CHECK_WHITELIST`,
`CHECK_BLACKLIST`, `FEDEX_ETD_ENABLED`) are parsed the same way: case-insensitive, `1` / `true` / `yes` / `on` mean
true, anything else false (an unrecognised value is logged at startup). With `FLASK_ENV=production` the app refuses to
start without a strong `JWT_SECRET_KEY`, a non-SQLite database and a valid `SETTINGS_ENCRYPTION_KEY` (Fernet key; to keep
secrets stored before, use the value of `system_settings._encryption_key`); `WEBHOOK_URL_STRICT` defaults to true.
Without `FLASK_ENV` the development configuration is used; if the database is not SQLite a prominent warning is logged.

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

### Process rules
- Sorting / picking / packing / delivery tasks are created only by the document flow (ASN received → sorting; DN in
  progress → picking; picking completed → packing; packing completed → delivery). There is no endpoint to create one by
  hand: `POST /warehouse/sorting/`, `/picking/`, `/packing/`, `/delivery/` → 405.
- Deleting a sorting / picking / packing batch (task `in_progress`) also deletes the details in it.
- Adding a batch (`POST /warehouse/sorting|picking|packing/<task_id>/batches/`) accepts an optional `client_batch_id`
  (a string of 1-64 characters; the client generates one per submission and sends it again unchanged when retrying
  after a network error / timeout). The first submission creates the batch (201). Submitting the same
  `client_batch_id` again for the same task with the same details (goods and quantities, plus the location for picking;
  order does not matter; remark and operation time are not compared) creates nothing and returns the existing batch
  with 200 (same body as 201); different details → 409 `16121` `details {client_batch_id, batch_id}`; a malformed id →
  400 `16123`. Without `client_batch_id` nothing changes. The comparison uses the batch's current details: if they were
  edited / deleted after the batch was created, replaying the original content is also `16121`; if the batch was
  deleted, a replay creates it again.
- Adjustment separation of duties: the creator cannot approve (403 `16040`); the user who last added / changed /
  deleted a detail (`details_modified_by`) cannot approve either → 409 `16122`.
- Transfer: the goods is not in the source location → 404 `16120` (it used to share `14003` with "missing warehouse id").
- Documents and bulk operations touching several goods / locations lock rows in one order (inventory rows by goods_id,
  then goods-location rows by goods_id, location_id), so two requests with the goods in opposite order no longer
  deadlock; creating the inventory row of a new goods (concurrent ASNs) or the first stock of a goods in a location
  (concurrent putaway / transfer) no longer fails with 500.
- Cancelling a DN (`PUT /warehouse/dn/<id>/cancel/`): `pending` = close; `in_progress` only while picking has not
  started (else 409 `16053`); `picked` / `packed` (not shipped) can be cancelled — the picked / packed goods go back to
  `sorted_stock` to be put away (`total_stock` unchanged), the picking / packing / delivery tasks are deactivated and
  the issued export documents are voided (`void_reason: dn_cancelled`); an unfinished carrier shipment (pending /
  active / unknown / cancelling) → 409 `16110` (cancel it on the documents card first); `delivered` / `completed` /
  `closed` → 409 `16052`. Cancelling an `in_progress` / `picked` / `packed` DN emits `dn.cancelled`.
- Cancelling an adjustment (`PUT /warehouse/adjustment/<id>/cancel/`, requires `adjustment_approve`): `pending` /
  `approved` can be cancelled, stock is not touched; it becomes `is_active=false` with its `status` unchanged
  (`operator_id` = who cancelled) and any further operation → 409 `16111`; `completed` → 409 `16112`. After an
  adjustment created from a cycle count is cancelled, a new one can be created from the same cycle count. Completing
  an adjustment creates the goods-location record for a positive line whose location has none (the location must be
  active); a negative line whose location no longer holds the goods → 409 `16113` (cancel it and create a new one).
- Cycle counts: the system quantity is taken when the count is entered (rows submitted to `details-batch-save`, a
  detail completed one by one, or `actual_quantity` given when updating a detail) and the difference is computed
  against it; adjustments use these quantities. Every detail must be counted before the task is completed (a count of
  0 must be submitted explicitly) → otherwise 409 `16114` `details {detail_ids}`.
- A location referenced by history records (putaway / removal / transfer / picking / cycle count / adjustment /
  inventory snapshot) cannot be deleted → 409 `44009` `details {entity: "location", id, references}`; deactivate it
  instead. A location that still holds stock is still 409 `16084`.
- Numeric fields: ASN detail `weight` / `volume` and location `width` / `depth` / `height` / `capacity` treat an empty
  string as null; a non-number or a negative value (≤ 0 for location dimensions) → 400 `16115`.
- Cycle count and adjustment lists accept `keyword`: cycle counts by id, task name, goods / location code of a detail;
  adjustments by id, reason, goods code / name or location code of a detail.

## Webhook Integration

WMS pushes event notifications to external systems via Webhook with HMAC-SHA256 signature verification.

### Supported Events

| Event | Trigger |
|-------|---------|
| `asn.received` | ASN marked as received |
| `asn.completed` | ASN completed with actual quantities |
| `asn.cancelled` | A received (not yet sorted) ASN was cancelled |
| `dn.in_progress` | DN processing started |
| `dn.delivered` | DN delivered (includes tracking number) |
| `dn.completed` | DN completed |
| `dn.cancelled` | A DN was cancelled after processing started (`in_progress` / `picked` / `packed`; includes `previous_status` and the picked / packed quantity per goods) |
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

With `WEBHOOK_URL_STRICT` (the default in production) the URL must be `https` and its host must resolve only to public
addresses (private, loopback, link-local, shared `100.64.0.0/10` — including the cloud metadata address
`100.100.100.200` — reserved and multicast ranges are rejected). At send time the host is resolved again, every
address is checked and the connection goes to a checked IP (no second DNS lookup); TLS SNI and certificate
verification still use the host name. Proxies from environment variables are not used for webhooks.

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
  "source": "station", "changed_at": "2026-01-01T10:00:00+09:00", "changed_at_ms": 1767229200123 }
```

- **Ordering**: `changed_at` has second precision (format unchanged). `changed_at_ms` (integer, epoch milliseconds,
  strictly increasing per WMS process and taken after the goods row is written) tells which change is newer: keep the
  largest `changed_at_ms` applied per `goods_id` and ignore payloads with a smaller or equal value. Events can arrive
  out of order (retries, several push processes). Events queued before this field existed have no `changed_at_ms` —
  fall back to `changed_at` for them.

- `source`: `spec_source` from the goods create/update request body (`station` / `manual` / `import` / `api`);
  otherwise `api` for API Key calls and `manual` for logged-in users. CSV import uses `import`.
- While an event for the same goods is still pending for a key and has not been sent yet, a newer change overwrites
  its payload instead of queueing another one (`dedupe_key = goods:<id>`; `changed_fields` becomes the union). If it
  was already attempted, a new event (new `X-Webhook-Id`) replaces it and the old one is marked `superseded`; an event
  is also superseded when a newer one for the same goods exists at send time.
- A CSV import looks up the subscribed keys and the pending events once per company, not once per row.
- Headers, signatures and retries are the same as for the other events.

Goods `origin_country` accepts ISO 3166-1 alpha-2 codes only (upper-cased; empty string clears it;
anything else → 400 `10014`). There is no default value. `GET /warehouse/goods/?origin_missing=true`
lists goods without it. Warehouse operators can set only the origin with
`PUT /warehouse/goods/<id>/origin-country` (`{"origin_country": "CN" | "" | null, "spec_source"?: ...}`),
which requires any of `goods_edit`, `sorting_edit` or `packing_edit` and also emits `goods.spec_updated`.
It is warehouse-scoped like `GET /warehouse/goods/<id>` (staff other than company admins must send
`X-WAREHOUSE-ID`, and the goods must have stock in that warehouse, else 404 `13003`) and returns only
`{id, code, origin_country}`.
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
| Company (`PUT /warehouse/company/<id>`) | `legal_name_en`, `address_en`, `country_code` (ISO 3166-1 alpha-2, `JP` when omitted on create), `tax_id_label`, `tax_id`, `export_contact_name`, `export_signatory_name`, `export_signatory_title` |
| Warehouse (`PUT /warehouse/warehouse/<id>`) | `address_en`, `country_code`, `contact_name_en` (printed as *Ship From* when it differs from the company address) |

Invalid `country_code` → 400 `14020`; text longer than the column → 400 `14019`. Responses do not fill in a
default: an empty country is returned as `null` (the documents use the warehouse country, then the company
country; neither set → `EXPORTER_PROFILE_INCOMPLETE`). This *export country* is both what the completeness check
looks at and what the CI prints as *Country of Export* and in the *Shipper / Exporter* block.

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

- Structure errors are rejected: `customs` not an object, `lines` not an array, wrong field types, numbers over
  the limit → 400 `16063` (`details.field`); a line whose `goods_code` is not in the DN details or is duplicated
  → 400 `16064`. Limits: `unit_value` / `total_value` at most 1,000,000,000,000 in absolute value;
  `freight_charge` / `insurance_charge` / `declared_value_carriage` / line `quantity` at most 2,147,483,647
  (database INTEGER). A line `unit_value` with more decimal places than the currency allows (0 for JPY / KRW / VND /
  CLP / ISK / PYG / UGX / XAF / XOF, otherwise 2; also 2 when the currency is missing) → 400 `16063`
  `details {field, currency, max_decimal_places}`, so that unit value × quantity = amount on the CI. Snapshots stored
  before this rule are rounded to the currency's decimal places (half up) before the amount is calculated — in the
  view, on the CI and in the FedEx request; a unit value that rounds to 0 is reported as `UNIT_VALUE_MISSING`.
- `freight_charge`, `insurance_charge` and `declared_value_carriage` are stored as integers, so amounts with cents
  (USD etc.) are rejected as well.
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
- `hs_code` accepts 6-10 digits; the CI prints only the first 6 (the internationally harmonised part). Digits 7-10
  are shown in the customs view (`hs_code_formatted`) only.
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
| `EXPORTER_NON_LATIN_TEXT` | Exporter text printed on the documents contains non-Latin characters (company English name / address / contact / signatory / tax ID / phone / email, *Ship From* warehouse address / contact); `field` names the master-data field to fix, e.g. `company.export_signatory_name`, `warehouse.address_en`, `warehouse.phone` |
| `CARRIER_NON_LATIN_TEXT` | The carrier name printed on the CI (carrier of the current delivery task, else of the DN) contains non-Latin characters, e.g. `ヤマト運輸`; rename the carrier in English (`field: carrier.name`) |
| `INCOTERM_MISSING`, `EXPORT_REASON_MISSING`, `CURRENCY_INVALID`, `RECIPIENT_COUNTRY_MISSING` | Header data missing |
| `LINE_MISSING`, `HS_CODE_MISSING`, `DESCRIPTION_MISSING`, `DESCRIPTION_NOT_ASCII`, `ORIGIN_MISSING`, `UNIT_VALUE_MISSING` | Per packed line (HS code: 6–10 digits after removing `.`, spaces and `-`) |

Warnings (do not block): `RECIPIENT_TAX_ID_MISSING`, `NON_LATIN_TEXT` (consignee text with non-Latin characters),
`NET_WEIGHT_UNKNOWN`, `GROSS_LT_NET`,
`JP_EXPORT_CODE_MISMATCH` (`jp_export_code` not 9 digits or its first 6 digits differ from the HS code),
`JP_EXPORT_CODE_MISSING` (JPY goods value — FOB, without freight and insurance — above 200,000 and a line has no `jp_export_code`),
`JP_EXPORT_VALUE_UNVERIFIED` (currency other than JPY, export country `JP`, and a packed line has no `jp_export_code`:
without exchange rates the 200,000 JPY threshold cannot be checked automatically — confirm manually; one warning per DN,
`field: jp_export_code`).

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
- A text block that does not fit on one page (in practice: every consignee field very long, e.g. ~240 CJK
  characters each) → 409 `16117` `details {document: "CI" | "PL", reason: "LAYOUT_OVERFLOW"}`; nothing is issued
  or voided. Shorten the consignee / recipient information and issue again.
- The CI and PL are issued together and stored in the database (`dn_documents`), final once generated.
  Issuing again with unchanged data returns the current version (200); if the data changed (including the
  AWB / tracking number) the old version is voided and a new version is issued (201).
- While a carrier shipment is pending / active / unclear / being cancelled, the current documents are what was
  submitted with it: issuing documents whose data differs other than the AWB (e.g. the goods' country of origin, the
  exporter profile or the carrier changed) → 409 `16076` `details {reason: "DOCUMENT_DATA_CHANGED", status, carrier,
  carrier_shipment: {id, status, tracking_number}}`. Cancel the shipment first.
- `file` returns `application/pdf` inline with the header `X-Content-SHA256`.
- The CI prints the AWB number when the delivery task already has a tracking number. Save it before
  shipping with `PUT /warehouse/delivery/<task_id>/tracking` `{ "tracking_number": "...", "carrier_id": 1 }`
  (permission `delivery_edit`; 409 `16065` after shipping), then issue again (`documents_outdated: true`
  in the customs view means the current documents no longer match the data).

### Shipping gate and lock

- Completing a delivery task (`PUT /warehouse/delivery/<task_id>/complete/`) with `tracking_number` empty, `null`
  or omitted keeps the saved tracking number (domestic DNs too). Completion locks the DN row first, so it is
  serialised with package / customs changes and document issuing.
- Export DNs are checked against the saved data before anything is written: no current CI or PL → 409 `16069`
  `details {missing_documents: [...], outdated: false}`; documents out of date (packages / customs data /
  tracking number / exporter profile changed without re-issuing) → 409 `16069`
  `details {missing_documents: [], outdated: true}`.
- While the DN has a carrier shipment request in progress or with an unclear result, or a shipment being cancelled /
  whose cancellation is unclear (`pending` / `unknown` / `cancelling`), completion → 409 `16079`
  (`details.unresolved`) with or without a tracking number: resolve it first (cancel / dismiss).
- If the current CI prints an AWB and the tracking number the task would ship with (the given one, else the saved
  one) differs (ignoring spaces / hyphens) → 409 `16080` `details {document_tracking_number, tracking_number}`: save
  the tracking number and issue the documents again first. A matching number is stored exactly as printed on the CI.
  When the CI has no AWB, the given tracking number is saved as before.
- If the current CI prints a carrier and the request's `carrier_id` would change it (`null` falls back to the DN's
  carrier, as the CI does) → 409 `16092` `details {document_carrier, document_carrier_id, carrier_id}`.
- Delivery tasks are created only when packing completes; there is no endpoint to create one by hand. A DN has at most
  one unfinished (`pending` / `in_progress`) active delivery task: if one already exists (e.g. a legacy hand-made task),
  it is reused instead of creating a second one.
- Export DNs: documents and carrier shipments follow the current delivery task (the newest active one); completing,
  saving the tracking number of, or updating another task → 409 `16091` `details {delivery_task_id, current_status}`.
- `dn.delivered` carries the tracking number of the task that was completed.
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
| `CUSTOMS_PDF_FONT_PATH` | (unset) | TrueType font for the documents (`.ttf`, or the first font of a `.ttc`; CFF-outline OTF such as Noto Sans CJK is not supported). When set, all text is printed with it and the used glyphs are embedded in the PDF; to embed e.g. Japanese consignee text, pick a font with those glyphs (such as IPAexGothic). Characters the font lacks, and characters Helvetica cannot print when unset, fall back to reportlab's built-in Japanese CID font (not embedded; relies on the viewer's fonts) |

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
| `POST` | `/warehouse/dn/<id>/carrier-shipment/dismiss` | `packing_edit` / `delivery_edit` |

`GET` returns `{enabled, carrier: "fedex", can_create, blockers[{code, message, goods_code?, field?}], unresolved,
can_dismiss, can_cancel, etd_enabled, default_label_format, label_formats{A4|THERMAL: {image_type, stock_type}},
declared_value_carriage, delivery_task_id, shipment, warnings[]}`:

- `enabled`: FedEx is configured **and** the DN's company is listed in `FEDEX_ALLOWED_COMPANY_IDS`.
- `can_create`: no blockers **and** `unresolved` is `null`.
- `unresolved`: the record whose result is not settled yet, or `null`: `{id, status: "pending" | "unknown" |
  "cancelling", reason, tracking_number, transaction_id, created_at, updated_at}` (see *Shipment records and unclear
  results*).
- `can_dismiss`: `true` when `unresolved.status` is `unknown`, or `cancelling` while no cancellation is in progress.
- `can_cancel`: `true` for an active shipment, a `cancelling` one while no cancellation is in progress, or an `unknown`
  record with a tracking number; `false` after the DN is shipped.
- `shipment`: the active (or `cancelling`) shipment, otherwise the latest cancelled one, otherwise `null`:
  `{id, tracking_number, package_tracking_numbers, status: active|cancelling|cancelled, reason, service_type, ship_date,
  package_count, net_charge, currency, declared_value, label_format, image_type, label_stock_type, label_document_id,
  label_download_path, label_file_name, label_content_type, label_parts[], etd_document_id, transaction_id,
  sender_country, created_at, created_by, updated_at, cancelled_at, cancelled_by, dismissed_at, dismissed_by}`.

`POST` takes an optional body `{"label_format": "A4" | "THERMAL"}` (how the label will be printed; omitted =
`FEDEX_DEFAULT_LABEL_FORMAT`; anything else → 400 `16077`):

| `label_format` | Printer | Default image / stock | Pages |
|----------------|---------|-----------------------|-------|
| `A4` | Laser printer, plain A4 / Letter paper | `PDF` / `PAPER_85X11_TOP_HALF_LABEL` | Letter page, label on the top half, folding instructions below |
| `THERMAL` | 4-inch label printer used through its driver | `PDF` / `STOCK_4X6` | 100 × 150 mm pages (FedEx's 4 × 6 in pages scaled to 98.4%) |

Do not use `PAPER_4X6` for label printers: FedEx returns a Letter page with the label in the top-left corner.
Each format's image type (`PDF` / `PNG` / `ZPLII` / `EPL2`) and stock can be overridden by configuration.

FedEx's 4 × 6 in (101.6 × 152.4 mm) labels run almost to the page edge (0–0.7 mm), so printing them at actual size on
100 × 150 mm label stock cuts the edges. The `THERMAL` PDF archive therefore scales every page down uniformly (never up)
and centers it on a `FEDEX_LABEL_THERMAL_PAGE_SIZE` page (default `100x150`); print it at actual size on a paper whose
printable area is 100 × 150 mm. Pages that would need more than 10% shrinking (e.g. Letter-size documents) are kept
as they are; ZPLII / EPL2 are not touched. For real 4 × 6 in stock set `101.6x152.4` (no scaling).

### Creating a shipment

- With a pending or unclear request on record, `POST` answers 409 `16079` (`details.unresolved`) without calling FedEx.
- **Blockers** (all listed at once; `POST` answers 409 `16072` with `details.blockers`): FedEx not configured
  (`FEDEX_NOT_CONFIGURED`) or invalid options (`FEDEX_CONFIG_INVALID`), the DN's company is not listed in
  `FEDEX_ALLOWED_COMPANY_IDS` (`FEDEX_COMPANY_NOT_ALLOWED`; unset = no company is allowed, since all charges go to one
  FedEx account), incoterm DDP while `FEDEX_DUTIES_PAYMENT_TYPE` is not `SENDER` (`INCOTERM_DUTIES_MISMATCH`,
  `field: incoterm`), not an export DN (`NOT_EXPORT`), DN not
  `packed` / already shipped, no active delivery task / its carrier code is not `fedex` / task completed, an active
  shipment already exists (`SHIPMENT_EXISTS`) or a tracking number was saved by hand (`TRACKING_NUMBER_EXISTS`),
  no packages / more than 30 packages, error-level customs problems (same codes as the customs view),
  ship-from or consignee address that does not fit the FedEx format (`SHIPPER_ADDRESS_INVALID` /
  `RECIPIENT_ADDRESS_INVALID`), consignee name or phone missing.
- **Declared value for carriage** above the customs value of the packed goods is lowered to that value (FedEx
  rejects a declared value above the customs value, and after partial packing only the goods actually shipped are
  covered). The value actually submitted is stored as `shipment.declared_value`; `warnings` carries
  `{code: "DECLARED_VALUE_CAPPED", message, requested, applied}` (in `GET` as a preview while
  no shipment is active, in the `POST` response for what was done).
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
  code) is the city, the other parts become one street line each (joined and re-wrapped when there are more than
  3 lines). E.g. `4-5-6 Sample-cho, Chuo-ku, Osaka 600-0000, JAPAN` → street lines `4-5-6 Sample-cho` /
  `Chuo-ku`, city `Osaka`, postal code `6000000`. The postal code is the warehouse / company `zip_code` (for Japan
  it is also recognised in the address and sent as 7 digits). A consignee without a postal code is sent with an
  empty `postalCode`; US / CA / PR require a 2-letter state code.
- While a shipment is active the delivery task keeps its tracking number: completing the delivery, saving the
  tracking number or updating the task with a different number → 409 `16078` (cancel the shipment first; an empty
  value on completion / update means "unchanged"). Numbers are compared ignoring spaces / hyphens and a matching one
  is stored as the shipment's own spelling. A `carrier_id` on completion / update / saving the tracking number whose
  carrier (by code) differs from the shipment's → 409 `16078` as well, with the requested `carrier_id` added to
  `details`; `carrier_id: null` means "unchanged". The tracking number of a cancelled (or compensation-cancelled)
  shipment of the DN cannot be saved or shipped with any more → 409 `16078`
  `details {tracking_number: null, cancelled_tracking_number, carrier, status: "cancelled"}`.
  While a request is pending or its result is unclear, saving a
  tracking number or updating the task with one → 409 `16079` (so that nobody books a second shipment by hand;
  clearing is allowed). While a shipment is `cancelling`, saving any tracking number (the same one included) or
  clearing it → 409 `16079` (an empty value on update means "unchanged").
- While a shipment is pending, active, unclear or being cancelled: adding or deleting a delivery task of the DN → 409
  `16078` (a new task would become the current one and bypass the tracking lock; a tracking number given on creation
  is checked as above); changing the customs data (`PUT /warehouse/dn/<id>/customs`) or the packages → 409 `16076`
  (the data was submitted with the shipment; `details: {reason: "CARRIER_SHIPMENT_OPEN", status, carrier,
  carrier_shipment: {id, status, tracking_number}}`); issuing documents with different data → 409 `16076`
  (`reason: "DOCUMENT_DATA_CHANGED"`, see *Documents*). Cancel the shipment first, or resolve an unclear one. All these
  checks lock the DN row first, so they are serialized with creating / cancelling.
- Commodity weight: goods with a unit weight → unit weight × packed quantity; goods without one share the rest of
  the total gross weight (or, if nothing is left, the gross weight by quantity).
- **Three steps; no DN row lock and no open database transaction while waiting for FedEx**:
  1. lock the DN, check the preconditions, issue the CI / PL if needed, insert a `pending` `dn_carrier_shipments`
     record and commit (a partial unique index allows at most one `pending` / `active` / `unknown` / `cancelling`
     record per DN);
  2. (with ETD, upload the CI first) create the shipment in FedEx. The whole creation is limited to
     `FEDEX_CREATE_BUDGET_SECONDS` (default 90): the connect / read timeout of every request (OAuth / ETD upload /
     shipment) is shrunk to the time left, and with less than 5 seconds left the next request is not sent
     (→ 504 `16074` with `details.budget_exhausted: true`, record `failed`), so the worker is not killed mid-way;
  3. on success the tracking number is committed on the record first, then in one transaction: record → `active`,
     tracking number → delivery task (existing tracking logic), CI / PL re-issued with the AWB, the label archive
     stored as a document with `doc_type: shipping_label`.
  The archive holds **every** document of the response (package labels → auxiliary documents → others; for
  international Express the "FEDEX AWB COPY" pages come inside the first package's label, nothing extra has to be
  requested): PDF / PNG are merged into one PDF, ZPLII / EPL2 are concatenated raw printer commands (`.zpl` / `.epl`,
  `application/octet-stream`). `label_parts` lists each document (source, package, content type, doc type, pages,
  archived).
- **FedEx errors** (`details` always carries `maybe_processed` and `unresolved`):
  - explicit rejection (4xx) → 502 `16073` (`details.errors`, `transaction_id`), record `failed`
    (`reason: rejected`); request not sent (connect timeout, DNS / connection refused, OAuth token failure) → record
    `failed` (`not_sent`), 504 `16074` for a connect timeout; ETD upload failure → `failed` (`etd_upload_failed`).
    No shipment exists in these cases; just retry.
  - unclear result: read timeout → 504 `16074`; connection dropped after connecting, 5xx, 200 without JSON / without a
    parsable shipment → 502 `16073`. The record becomes `unknown` (`reason`: `timeout` / `connection_error` /
    `server_error` / `bad_response`), `maybe_processed: true`, `details.unresolved` is the record. The shipment may
    exist on the FedEx side: check FedEx Ship Manager, cancel it there if it exists, then dismiss the record (below).
  - an unparsable response that still names a tracking number is handled like a save failure (the shipment is
    cancelled).
- **Saving fails after FedEx created the shipment** (502 `16095`, also for business errors such as the DN being
  shipped meanwhile; `details {tracking_number, transaction_id, error: {code, message}, compensation: {attempted,
  cancelled, cancel_transaction_id, error}, unresolved, maybe_processed}`): WMS first re-reads the record in a new
  transaction. If it is already `active` with the same tracking number (the commit went through but the caller saw an
  error) the shipment is kept and the request succeeds (201). If the record cannot be read, the shipment is **not**
  cancelled and the record becomes `unknown` (`reason: save_unconfirmed`, with `tracking_number`). Otherwise WMS
  cancels the shipment in FedEx (compensation) and commits the outcome on the record separately (kept although the
  main transaction rolled back): cancellation confirmed (or FedEx answers "already cancelled / not found") →
  `cancelled` (`reason: compensated`, `details.unresolved` null); cancellation failed / not confirmed → `unknown`
  (`reason: compensation_failed`, with `tracking_number`) — cancel it again (`POST …/cancel`) or dismiss it.
- CI / PL issued in step 1 are committed with the `pending` record and stay valid when FedEx fails.
- The label is downloaded with `GET /warehouse/dn/<id>/customs-documents/<label_document_id>/file` (PDF inline,
  ZPL / EPL as attachment). Labels
  do not count for the "current CI and PL" shipping gate.

### Shipment records and unclear results

Every shipment request leaves a record in `dn_carrier_shipments`:

| `status` | Meaning |
|----------|---------|
| `pending` | FedEx request in progress |
| `active` | Active shipment |
| `cancelling` | The active shipment is being cancelled (`reason: cancel_in_progress`) or the result of cancelling it is unclear (`reason`: `timeout` / `connection_error` / `server_error` / `bad_response` / `save_failed` / `interrupted`; `stale` = `cancel_in_progress` older than `FEDEX_PENDING_STALE_MINUTES`). Tracking number and label are kept; the DN cannot be shipped |
| `cancelled` | Cancelled (by the user; automatically after a save failure, `reason: compensated`; FedEx answered already cancelled, `reason: already_cancelled`) |
| `failed` | Rejected by FedEx / not sent — no shipment exists (`reason`: `rejected` / `not_sent` / `etd_upload_failed` / `budget_exhausted` / `interrupted`) |
| `unknown` | Unclear result — the shipment may exist in FedEx (`reason`: `timeout` / `connection_error` / `server_error` / `bad_response` / `compensation_failed` / `save_unconfirmed` / `interrupted`) |
| `dismissed` | The operator confirmed that FedEx has no such shipment (or cancelled it there); records with a tracking number only after FedEx confirmed it is cancelled / not found |

- A `pending` record older than `FEDEX_PENDING_STALE_MINUTES` (default 10; never shorter than the creation budget +
  75 s) is reported as `unknown` with `reason: stale` (e.g. the worker was killed). When the worker is killed by a
  timeout (gunicorn raises `SystemExit`) WMS also tries to record `unknown` / `failed` with `reason: interrupted`.
- While `unresolved` (`pending` / `unknown` / `cancelling`) exists: no new shipment (409 `16079`), no tracking number
  can be saved (409 `16079`), the DN cannot be shipped (409 `16079`), customs data / packages / documents with
  different data are locked (409 `16076`), delivery tasks cannot be added / deleted (409 `16078`).
- An `unknown` record **with** a tracking number (e.g. `compensation_failed`) can be cancelled with
  `POST …/cancel` (same rules as *Cancelling*; on success it becomes `cancelled`).
- **Dismiss**: `POST /warehouse/dn/<id>/carrier-shipment/dismiss` with `{"confirm": true, "unresolved_id": <id>}`
  (`confirm` missing or not `true` → 400, `field: confirm`; `unresolved_id` optional, not a positive integer → 400).
  Only when `can_dismiss`: `unresolved.status == "unknown"` (including a stale `pending`), or `cancelling` while no
  cancellation is in progress.
  - Without a tracking number: FedEx is not called → `dismissed` with `dismissed_by` / `dismissed_at`.
  - With a tracking number the shipment may really exist, so FedEx is asked to cancel it first (three steps, no DN
    lock while calling FedEx). Only when FedEx cancels it or answers already cancelled / not found: `unknown` →
    `dismissed` (`cancel_transaction_id` recorded), `cancelling` → `cancelled` (label voided, tracking number
    cleared, CI / PL re-issued). Otherwise → 409 `16094` `details {unresolved, carrier, tracking_number, result:
    refused | unclear, http_status, transaction_id, errors, alerts, maybe_processed}`; cancel it in FedEx Ship
    Manager first. FedEx not configured → 409 `16072`.
  - `unresolved_id` given but not the current unresolved record → 409 `16093` `details {expected, actual}`
    (`expected` = the id in the request, `actual` = the current record's id).
  - The response has the same shape as `GET`. Nothing to dismiss (or the request / cancellation is still in progress)
    → 409 `16075` (`details.unresolved`). Afterwards a shipment can be created again or a tracking number saved by
    hand.

### Cancelling

`POST /warehouse/dn/<id>/carrier-shipment/cancel`, optional body `{"tracking_number": "..."}` — the shipment the
operator means (`shipment.tracking_number` / `unresolved.tracking_number`); compared ignoring spaces / hyphens, a
different current shipment → 409 `16093` `details {expected, actual}` (`expected` = the number in the request,
`actual` = the current one); not a string → 400. Without it the call works as before.

Allowed until the DN is shipped (409 `16065` afterwards; 409 `16075` with nothing to cancel, `details.unresolved`)
for an active shipment, a `cancelling` one (retry) or an `unknown` record with a tracking number. FedEx
`PUT /ship/v1/shipments/cancel` (`DELETE_ALL_PACKAGES`; the ship-from country is the `sender_country` stored when the
shipment was created — records from before the migration derive it the same way as creation). Like creating, in
**three steps, without the DN row lock or an open transaction while waiting for FedEx** (limited to
`FEDEX_CREATE_BUDGET_SECONDS`):

1. lock the DN, mark the shipment `cancelling` (`reason: cancel_in_progress`) and commit — from now on the DN cannot be
   shipped and the tracking number cannot be changed (409 `16079`); another cancel request → 409 `16079`;
2. call FedEx;
3. lock the DN and save the result:
   - cancelled (`cancelledShipment: true`, or FedEx answers already cancelled / not found) → `cancelled`, the tracking
     number on the delivery task is cleared (compared ignoring spaces / hyphens), the label is voided
     (`void_reason: shipment_cancelled`) and the CI / PL are re-issued without the AWB (voided if they can no longer be
     issued). An `unknown` record becomes `cancelled` as well;
   - FedEx refused (4xx, `cancelledShipment: false`) or the request was not sent → back to `active` (an `unknown`
     record stays `unknown`), 502 `16073` / 504 `16074` with the reason;
   - unclear (read timeout, connection lost, 5xx, broken response, or HTTP 200 **without** `cancelledShipment`) → the
     record stays `cancelling` with the reason (`timeout` / `server_error` / `bad_response` / `connection_error`),
     502 `16073` / 504 `16074` with `details.maybe_processed: true` and `details.unresolved`. Shipping stays blocked;
     cancel again (or dismiss after checking FedEx Ship Manager) until FedEx gives a clear answer.

If saving step 3 fails or the worker is killed, the record stays `cancelling` (`reason: save_failed` /
`interrupted`; a `cancel_in_progress` older than `FEDEX_PENDING_STALE_MINUTES` is reported as `stale`) and can be
cancelled again right away. Only the FedEx credentials are required: a shipment can still be cancelled after its
company was removed from `FEDEX_ALLOWED_COMPANY_IDS`.

If FedEx answers that the shipment is already cancelled / not found, it is treated as cancelled
(`reason: already_cancelled`) — e.g. the previous cancellation succeeded in FedEx but saving it in WMS failed; cancelling
again settles it. Basis: the public FedEx Ship API documentation lists no dedicated error codes for these cases and this
project has not verified them in the sandbox, so only the error **code** is checked (not the localized message): a code
containing `ALREADY` together with `CANCEL` / `DELETE`, or `TRACKING` / `SHIPMENT` together with `NOT FOUND` /
`NOT EXIST`, plus the legacy Web Services code `8159` ("Shipment Delete was requested for a tracking number already in a
deleted state"). Matches are logged with the raw code; add codes seen in production to
`carrier_services.ALREADY_CANCELLED_CODES`.

### Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `FEDEX_API_BASE` | `https://apis-sandbox.fedex.com` | `https://apis.fedex.com` for production |
| `FEDEX_API_KEY` / `FEDEX_SECRET_KEY` / `FEDEX_ACCOUNT_NUMBER` | (unset) | Credentials of the FedEx developer project; any one missing = feature disabled |
| `FEDEX_ALLOWED_COMPANY_IDS` | (empty) | Company IDs allowed to create shipments (comma-separated, e.g. `1,3`); **unset = no company is allowed** (all charges go to one FedEx account) |
| `FEDEX_SERVICE_TYPE` | `INTERNATIONAL_ECONOMY` | FedEx service type |
| `FEDEX_PICKUP_TYPE` | `USE_SCHEDULED_PICKUP` | Pickup type |
| `FEDEX_DEFAULT_LABEL_FORMAT` | `A4` | Label format when the request does not give one (`A4` / `THERMAL`) |
| `FEDEX_LABEL_A4_IMAGE_TYPE` / `FEDEX_LABEL_A4_STOCK_TYPE` | `PDF` / `PAPER_85X11_TOP_HALF_LABEL` | Override for `A4` |
| `FEDEX_LABEL_THERMAL_IMAGE_TYPE` / `FEDEX_LABEL_THERMAL_STOCK_TYPE` | `PDF` / `STOCK_4X6` | Override for `THERMAL` (e.g. `ZPLII` for Zebra printers) |
| `FEDEX_LABEL_THERMAL_PAGE_SIZE` | `100x150` | Page size (mm, WIDTHxHEIGHT) of the `THERMAL` PDF archive; labels are scaled down and centered. `101.6x152.4` for real 4 × 6 in stock; invalid → `FEDEX_CONFIG_INVALID` |
| `FEDEX_ETD_ENABLED` | `False` | Electronic trade documents (upload the CI) |
| `FEDEX_DUTIES_PAYMENT_TYPE` | `RECIPIENT` | `RECIPIENT` or `SENDER` (the account) |
| `FEDEX_DOCUMENT_API_BASE` | (derived) | Trade Documents Upload host; derived from `FEDEX_API_BASE` (sandbox / production) |
| `FEDEX_CONNECT_TIMEOUT_SECONDS` / `FEDEX_TIMEOUT_SECONDS` | `5` / `30` | Connect / read timeout of one request (seconds); shrunk to the remaining budget when creating |
| `FEDEX_CREATE_BUDGET_SECONDS` | `90` | Time limit of the whole creation (OAuth + ETD upload + shipment); keep it below the gunicorn / nginx timeout (e.g. 120 s). Also used for cancelling |
| `FEDEX_PENDING_STALE_MINUTES` | `10` | A `pending` record older than this is reported as unclear (`stale`); never shorter than the creation budget + 75 s |

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

### Automatic production deploy (GitHub Actions)

Pushing to `main` runs `.github/workflows/deploy.yml` (one at a time, `concurrency: deploy-backend`):

1. A dirty server work tree (`git status --porcelain` not empty) aborts the deploy without touching anything; the current service is probed first and the deploy also aborts if it gives no HTTP response at all (a manual run can tick `skip_precheck`)
2. The commit before the deploy is recorded; `fetch` + `reset --keep` → `pip install` → `flask db upgrade` (`lock_timeout=15s`). Any failure returns to that commit without restarting. The whole migration chain runs in one transaction, so a failure on PostgreSQL rolls it back
3. After the restart `GET /health` (no auth, no database; returns `{status, commit}`) must report the new short hash; otherwise the code is rolled back and the old code restarted (**the database is not rolled back**, so migrations must keep the previous code working: add first, remove later)

The probe URL defaults to `http://127.0.0.1:5002/health`; override it with the repository variable `BACKEND_HEALTH_URL`. The pm2 process must be able to run `git` (`/health` reads the commit), otherwise it reports `unknown` and the deploy is treated as failed.

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

Business codes of the export-document features: `14019` export profile text too long, `14020` invalid country code, `16063` customs structure invalid, `16064` customs line goods code not in the DN / duplicated, `16065` shipped — customs data / packages / documents locked (409), `16066` invalid packages, `16067` packages cannot be edited in the current DN status (409), `16068` documents cannot be issued yet (409, `details.problems`), `16069` documents missing or out of date before shipping (409, `details {missing_documents, outdated}`), `16070` not an export DN (409), `16071` document not found (404), `16117` documents cannot be laid out — a text block (usually the consignee) is too long for one page; shorten it (409, `details {document, reason: LAYOUT_OVERFLOW}`). Carrier shipments (FedEx): `16072` preconditions not met (409, `details.blockers`), `16073` FedEx returned an error (502, `details.errors` / `transaction_id`), `16074` FedEx timed out (504), `16075` no carrier shipment to cancel / dismiss (409, `details.unresolved`), `16076` packages / customs data cannot be changed, or documents with different data cannot be issued, while a carrier shipment is pending, active, unclear or being cancelled (409, `details {reason: CARRIER_SHIPMENT_OPEN | DOCUMENT_DATA_CHANGED, status, carrier, carrier_shipment {id, status, tracking_number}}`), `16077` invalid `label_format` (400), `16078` tracking number / carrier differs from the active carrier shipment, or a delivery task is added / deleted while a carrier shipment exists (409), `16079` a carrier shipment request is pending or its result is unclear, or the shipment is being cancelled / the cancellation is unclear — no new shipment, tracking number or shipping (409, `details.unresolved`), `16093` the shipment / record named in a cancel / dismiss request is not the current one (409, `details {expected, actual}`), `16094` dismissing a record with a tracking number: FedEx did not confirm it is cancelled / not found (409), `16095` FedEx created the shipment but saving it in WMS failed; compensated (502, `details {error, compensation, unresolved, …}`). Delivery completion: `16080` tracking number differs from the AWB printed on the current CI (409, `details {document_tracking_number, tracking_number}`), `16092` carrier differs from the one printed on the current CI (409, `details {document_carrier, document_carrier_id, carrier_id}`). Delivery tasks: `16090` the DN already has an unfinished delivery task (409, `details {delivery_task_id, status}`), `16091` export DN — not the current delivery task (409, `details {delivery_task_id, current_status}`). Errors may carry a structured `details` object.

Process rules (see "Process rules" above): `16110` DN has an unfinished carrier shipment — cancel it first (409), `16111` adjustment cancelled (409), `16112` adjustment cannot be cancelled in its status (409), `16113` adjustment line cannot be applied — the location no longer holds the goods / no location (409), `16114` cycle count has uncounted details (409, `details.detail_ids`), `16115` invalid numeric field (400, `field`), `16116` the ASN of a sorting task cannot be changed (409), `16120` transfer: the goods is not in the source location (404), `16121` the same `client_batch_id` was already submitted with different details (409, `details {client_batch_id, batch_id}`), `16122` the user who changed the adjustment details cannot approve it (409), `16123` invalid `client_batch_id` (400), `44009` location has history records — deactivate it instead (409). Tasks: `16088` the task is deactivated (its DN / ASN was cancelled, 409), `16089` a picking task with nothing picked cannot be completed (409).

Master data and permissions: `16100` invalid goods value (400), `16101` duplicate goods code in the CSV (400, with row numbers), `16102` invalid data value (400), `16103` inactive roles cannot be assigned (400), `16104` only company administrators can create / delete warehouses or change the manager (403), `16105` without company_all_access only API keys bound to yourself can be managed (403), `44004` master data referenced by documents or stock cannot be deleted — deactivate it instead (409, `details {entity, id, references}`), `44005` conflicts with existing data (409).

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
