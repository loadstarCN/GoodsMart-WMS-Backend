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
`CHECK_BLACKLIST`) are parsed the same way: case-insensitive, `1` / `true` / `yes` / `on` mean
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
├── timezone_conversion.py     # timestamp → timestamptz conversion (flask wms convert-utc, see Time Zones)
├── extensions/                # Flask extensions
│   ├── db.py                  # SQLAlchemy, UTCDateTime (time columns, UTC)
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
│   ├── warehouse/             # Warehouse management (incl. warehouse time zone)
│   ├── common/                # Shared helpers (clock.py: current time, warehouse time zones, local dates)
│   ├── location/              # Storage location management
│   ├── goods/                 # Product management
│   ├── inventory/             # Inventory management
│   ├── inventory_snapshot/    # Inventory snapshots
│   ├── supplier/              # Supplier management
│   ├── carrier/               # Carrier management, integration plugins (integrations/), file presets (presets/)
│   ├── recipient/             # Recipient management
│   ├── asn/                   # Inbound: ASN (Advanced Shipping Notice)
│   ├── sorting/               # Inbound: Sorting
│   ├── putaway/               # Inbound: Putaway
│   ├── dn/                    # Outbound: DN (Delivery Note)
│   ├── shipping/              # Outbound: shipping core, flow, stages, shipping options
│   ├── country_rules/         # Country rules (customs checks, address formats, phone numbers)
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
- Delivery with tracking number (or create the shipment automatically through a carrier integration; FedEx built in)
- Shipping flow per DN (`GET /warehouse/dn/<id>/shipping-flow`): domestic / international, carrier methods
  (API / file / manual), manual waybills and pickups, CSV export / import for carriers without an API (Yamato B2 Cloud,
  Sagawa e-Hiden III, Japan Post Yu-Pack Print R presets), structured international addresses, country rules,
  shipping service options, automatic signing — see *Shipping Flow*
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
  the issued export documents are voided (`void_reason: dn_cancelled`); an unfinished automatic carrier shipment
  (`source: api`; pending / active / unknown / cancelling) → 409 `16110` (cancel it on the documents card first), a
  manual waybill is cancelled with the DN (`reason: dn_cancelled`); `delivered` / `completed` /
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
| `dn.in_progress` | DN processing started (includes the start time `started_at`) |
| `dn.picked` | DN picking completed (includes the planned / picked quantity per goods and `picked_at`) |
| `dn.packed` | DN packing completed (includes the planned / picked / packed quantity per goods and `packed_at`) |
| `dn.delivered` | DN delivered (includes tracking number and the shipping time `shipped_at`) |
| `dn.tracking_updated` | The carrier tracking of an automatic shipment changed (status / scans; the full latest result, see *Tracking*) |
| `dn.completed` | DN completed (includes tracking number, `shipped_at`, the signed time `signed_at` and `signed_source`: `warehouse` / `carrier` / `rule`) |
| `dn.cancelled` | A DN was cancelled after processing started (`in_progress` / `picked` / `packed`; includes `previous_status` and the picked / packed quantity per goods) |
| `goods.spec_updated` | Goods weight / dimensions / country of origin changed (company-wide, subscription required — see below) |

`started_at` / `picked_at` / `packed_at` / `shipped_at` / `signed_at` are when the step actually happened (ISO 8601 with offset, e.g.
`2026-10-23T13:03:00+09:00`), not when the webhook is sent: the time of the warehouse action for manual steps (the
entered signed time when one is given), the carrier's pickup time for automatic shipping and the carrier's delivery time
for automatic signing. Webhooks can arrive late (scheduled pushes, retries), so receivers should record these fields.

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
  view, on the CI and in the carrier shipment request; a unit value that rounds to 0 is reported as `UNIT_VALUE_MISSING`.
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
The DN must be `picked` or `packed` (otherwise 409 `16067`). Packages belong to shipping: domestic DNs (no customs
snapshot) record them too.

```json
{ "packages": [ { "package_no": 1, "gross_weight_kg": 3.25, "length_mm": 400, "width_mm": 300, "height_mm": 250, "remark": null } ] }
→ 200 { "packages": [...], "voided_documents": [12, 13] }
```

1–99 packages, numbered consecutively from 1 (auto-numbered when omitted); gross weight 0.01–999.999 kg
(3 decimals); dimensions 1–3000 mm (integers). Invalid data → 400 `16066`. Changing the packages voids
the issued documents (`void_reason: packages_changed`) when the DN has a customs snapshot. While an automatic carrier
shipment (`source: api`) is unfinished → 409 `16076`; a manual tracking number does not block it.

### Documents

| Method | Path | Permission |
|--------|------|------------|
| `POST` | `/warehouse/dn/<id>/customs-documents/issue` | `packing_edit` |
| `GET` | `/warehouse/dn/<id>/customs-documents/?status=issued` | `dn_read` / `packing_read` |
| `GET` | `/warehouse/dn/<id>/customs-documents/<doc_id>/file` | `dn_read` / `packing_read` |

- The documents contain only the goods, the two parties and the packages — **no tracking number (AWB) and no
  carrier**. They can be issued as soon as the DN is packed (on the packing task page), before any shipment exists;
  saving or changing the tracking number or carrier later does not affect them and needs no re-issue.
- `issue` requires: DN `packed`, customs snapshot, at least one package, complete exporter profile and no
  error-level problems (otherwise 409 `16068` with `details.problems`; not an export DN → 409 `16070`).
- A text block that does not fit on one page (in practice: every consignee field very long, e.g. ~240 CJK
  characters each) → 409 `16117` `details {document: "CI" | "PL", reason: "LAYOUT_OVERFLOW"}`; nothing is issued
  or voided. Shorten the consignee / recipient information and issue again.
- The CI and PL are issued together and stored in the database (`dn_documents`), final once generated.
  Issuing again with unchanged data returns the current version (200); if the data changed the old version is voided
  and a new version is issued (201). `documents_outdated: true` in the customs view means the current documents no
  longer match the data and must be issued again.
- Documents issued before this change print the AWB / carrier (and their data fingerprint includes both). If what
  they print matches the tracking number / carrier now on the delivery task (or on the unfinished carrier shipment)
  and everything else is unchanged, they still count as current — not outdated, shipping is not blocked; issuing
  again produces a version without those two lines (201).
- While a carrier shipment is pending / active / unclear / being cancelled, the current documents are what was
  submitted with it: issuing documents whose data differs in any way (e.g. the goods' country of origin or the
  exporter profile changed) → 409 `16076` `details {reason: "DOCUMENT_DATA_CHANGED", status, carrier,
  carrier_shipment: {id, status, tracking_number}}`. Cancel the shipment first.
- `file` returns `application/pdf` inline with the header `X-Content-SHA256`.
- The tracking number / carrier are saved with `PUT /warehouse/delivery/<task_id>/tracking`
  `{ "tracking_number": "...", "carrier_id": 1 }` (permission `delivery_edit`; 409 `16065` after shipping), or by
  creating the shipment through a carrier integration (see *Carrier Integrations*).

### Shipping gate and lock

- Completing a delivery task (`PUT /warehouse/delivery/<task_id>/complete/`) with `tracking_number` empty, `null`
  or omitted keeps the saved tracking number (domestic DNs too). Completion locks the DN row first, so it is
  serialised with package / customs changes and document issuing.
- Completion only records that the goods have left; customs documents are not checked: a missing CI / PL or
  out-of-date documents (packages / customs data / exporter profile changed without re-issuing) do not block it
  (`16069` is no longer returned). Out-of-date documents are only flagged on the customs card / export shipping
  steps (`documents_outdated` of `GET /dn/<id>/customs`).
- While the DN (export or domestic) has an automatic carrier shipment request in progress or with an unclear result,
  or a shipment being cancelled / whose cancellation is unclear (`pending` / `unknown` / `cancelling`), completion → 409
  `16079` (`details.unresolved`) with or without a tracking number: resolve it first (cancel / dismiss).
- Completion accepts `shipped_at` (when it was actually handed over, ISO 8601, default now; in the future → 400 `16136`)
  and records `completed_source: warehouse` (see *Shipping Flow*).
- The documents print no tracking number / carrier, so the tracking number and carrier given on completion are no
  longer compared with the CI (the former `16080` / `16092` are no longer used). While an automatic shipment is active
  the tracking number / carrier are locked as described in *Carrier Integrations* (`16078`).
- Delivery tasks are created only when packing completes; there is no endpoint to create one by hand. A DN has at most
  one unfinished (`pending` / `in_progress`) active delivery task: if one already exists (e.g. a legacy hand-made task),
  it is reused instead of creating a second one.
- DNs with a customs snapshot or with waybill records (any source): documents and waybills follow the current
  delivery task (the newest active one); completing, saving the tracking number of, or updating another task → 409
  `16091` `details {delivery_task_id, current_status}`.
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
| `DOCUMENT_TIMEZONE` | (unset) | Old name of `WMS_DEFAULT_TIMEZONE` (read when that is unset). The invoice date is the local date of the DN warehouse, see *Time Zones* |
| `CUSTOMS_SIGNATURE_COMPANY_ID`, `CUSTOMS_SIGNATURE_FROM_DN_ID`, `CUSTOMS_SIGNATURE_SIGNATORY_NAME`, `CUSTOMS_SIGNATURE_SIGNATORY_TITLE`, `CUSTOMS_SIGNATURE_PATH` | (unset) | Optional company and first eligible DN ID, signer name/title, and absolute path to its authorized CI signature PNG. Keep the image outside the source checkout, readable only by the backend process. All five values are required. Earlier DNs keep the company's existing signer and unsigned CI. On eligible DNs, changing the image marks previously issued documents outdated. |
| `CUSTOMS_PDF_FONT_PATH` | (unset) | TrueType font for the documents (`.ttf`, or the first font of a `.ttc`; CFF-outline OTF such as Noto Sans CJK is not supported). When set, all text is printed with it and the used glyphs are embedded in the PDF; to embed e.g. Japanese consignee text, pick a font with those glyphs (such as IPAexGothic). Characters the font lacks, and characters Helvetica cannot print when unset, fall back to reportlab's built-in Japanese CID font (not embedded; relies on the viewer's fonts) |

## Shipping Flow (domestic and international)

One DN, one flow: ordered → picking → packing → (when customs is required) export documents → waybill → pickup →
handover to the carrier → in transit → delivered. The design is in `doc/shipping-flow.md`; phase 1 covers
carrier methods, domestic / international routing, manual waybills and pickups, sources and actual times, automatic
signing by rule and the flow endpoint; phase 2 adds the file mode (CSV export / import), structured international
addresses, country rules and shipping service options. Nothing assumes a country, a carrier or an API contract: a carrier without an API
can run the whole flow by hand.

### Domestic / international (route)

`warehouse/shipping/route.py` decides per DN:

- `origin_country` = the warehouse `country_code`, else the company's.
- `destination_country` = the customs snapshot's recipient country (`recipient_country`, else `consignee.country`) →
  the DN's `ship_to.country` → the recipient's `country`.
- `international` = origin ≠ destination (unknown on either side counts as domestic).
- `requires_customs` = international and not inside one customs union (`warehouse/common/customs_unions.py`, EU
  registered by default); a DN **with a customs snapshot always requires customs** (the source system sent customs data).
- Export documents (CI / PL), commodities / invoice for the carrier, trade documents / ETD and re-issuing after creating
  a shipment are a customs *extension* (`warehouse/dn/customs_extension.py`) that only takes part when
  `requires_customs` and the DN has a customs snapshot. Domestic shipments never touch customs.
- Packages (`PUT /warehouse/dn/<id>/packages`) belong to shipping: they do not need a customs snapshot (domestic DNs
  record packages too); changing them voids the CI / PL only when the DN has a customs snapshot.

### Carrier methods

Each carrier chooses, per capability, how it is connected (`carriers` columns, migration `55e9fa27fb9e`):

| Field | Values (default) | Meaning |
|-------|------------------|---------|
| `waybill_method` | `api` / `file` / `manual` (`manual`) | `api`: the integration creates shipments; `file`: CSV exported in the carrier software's format, tracking numbers imported back (see *File mode*); `manual`: tracking number entered / scanned, label printed on the carrier's website |
| `pickup_method` | `api` / `manual` / `scheduled` / `dropoff` / `none` (`none`) | `api`: booked through the integration; `manual`: booked by phone / website and recorded in WMS; `scheduled`: regular pickup; `dropoff`: the warehouse drops parcels off; `none`: not managed |
| `pickup_schedule` | json or `null` | for `scheduled`: `{weekdays: [1..7] (ISO, 1 = Monday), ready_time: "HH:MM", close_time: "HH:MM"}` |
| `pickup_contact` | text ≤ 255 | phone / URL / note for booking pickups (shown for `manual`) |
| `tracking_method` | `api` / `link` / `none` (`link`) | `api`: tracked by the integration (shipped on pickup, signed on delivery); `link`: public tracking page only |
| `tracking_url_template` | URL ≤ 500 with `{tracking_number}` | public tracking page; empty = the plugin's built-in one (FedEx: `https://www.fedex.com/fedextrack/?trknbr={tracking_number}`) |
| `auto_sign_days` | 1–90 or `null` (off) | sign automatically this many days after handover when the shipment is not tracked through the API |
| `file_preset` / `file_template` / `file_account` | text / json / json | file mode settings (see *File mode*, migration `a7c4e2f9b3d1`) |

- `GET /warehouse/carrier/` (list) and `GET / PUT /warehouse/carrier/<id>` return these fields plus the read-only
  `available_methods: {waybill: [...], pickup: [...], tracking: [...]}`. `api` is only offered when the carrier's
  online integration is turned on and its plugin declares the capability (`supports_waybill` / `supports_pickup` /
  `supports_tracking`); `file` once a file preset / template is configured (it may be set in the same request).
- `POST` / `PUT` validate them: an invalid value, a method that is not available, a bad `pickup_schedule` /
  `tracking_url_template` (must be `http(s)://…{tracking_number}…`) / `auto_sign_days` → 400 `16135` with `field`
  (`waybill_method`, `pickup_schedule.weekdays`, …). A method that is not changed is not checked again (saving other
  carrier data after the integration was turned off still works).
- Setting up an integration follows along: when an integration is created, turned on again or switched to another
  plugin, methods still at their default become `api` where the plugin supports it; removing the integration (or
  switching to a plugin without the capability) turns `api` back to the default.
- Migration backfill: carriers with an enabled FedEx integration get `api` for all three methods (the previous
  behaviour); a turned-off FedEx integration keeps `tracking_method: api` (existing shipments are still tracked, as
  before). Third-party plugins are not backfilled — choose the methods on the carrier page.
- An `api` pickup booking requires `pickup_method: api` (blocker `PICKUP_METHOD_NOT_API`, 409 `16130`). Creating a
  shipment through the integration only requires the integration to be ready (the waybill method decides what the flow
  offers first). Tracking through the API requires `tracking_method: api`.

### Waybills: API, manual, file

`dn_carrier_shipments` now holds every waybill of a DN with a `source`: `api` (created by the plugin), `manual`
(entered / scanned), `file` (exported / imported files, see *File mode*). A DN still has at most one unfinished
waybill record (`pending` / `active` / `unknown` / `cancelling` / `exported`, partial unique index regardless of
source). `carrier` (plugin code)
is nullable now; `carrier_data` (json) is reserved for plugins / file templates.

- Saving a tracking number (`PUT /warehouse/delivery/<task_id>/tracking`, `PUT /warehouse/delivery/<task_id>`, or
  completing the delivery with `tracking_number`) also writes a `manual` record: `status: active`, `carrier_id` = the
  task's carrier, `carrier` = the plugin code of that carrier's integration (`null` without one), `created_by` = the
  user. A new number or another carrier → the previous record `cancelled` (`reason: replaced`) and a new `active` one;
  clearing the number → `cancelled` (`reason: cleared`); the same number in another spelling (spaces / hyphens) only
  updates the record. Deleting a delivery task with a tracking number → `cleared`; cancelling the DN →
  `dn_cancelled`. Nothing is sent to the carrier.
- **The lock rules only apply to `api` waybills** (they exist on the carrier side; changing the number needs a
  cancellation first): `16078` / `16079`, `16076` (packages / customs data), `16110` (cancelling the DN), `16125`
  (removing the integration) and the carrier-shipment endpoints only look at `source = api`. While an `api` waybill is
  unfinished no `manual` record is written (the tracking number is the API one).
- With a manual tracking number on the task, creating a shipment through the integration is still blocked
  (`TRACKING_NUMBER_EXISTS`); clear it first.
- Guards that used to apply to export DNs only are shipment-based now: completing the delivery is blocked while an
  `api` waybill is pending / unclear / being cancelled (409 `16079`) for any DN; only the current delivery task can be
  completed / updated / get a tracking number (409 `16091`) on any DN that has a customs snapshot **or** waybill
  records.

### Pickups: API and manual

- The pickup page (`GET /warehouse/dn/carrier-pickups`) lists the carriers whose `pickup_method` is `api` (those with a
  turned-on integration first) or `manual`, and for the selected one its active waybills of any source (`api` / `manual`
  / `file`) whose DN is not shipped. New fields: `method` (`api` / `manual`), `can_record`, `carrier.pickup_method`,
  `carrier.pickup_contact`, `shipments[].source`, `pickups[].source`.
- `POST /warehouse/dn/carrier-pickups/manual` (`packing_edit` / `delivery_edit`, header `X-WAREHOUSE-ID`) records a
  pickup booked by phone / website:

```json
{ "warehouse_id": 1, "carrier_id": 4, "shipment_ids": [201, 202], "pickup_date": "2026-10-27",
  "ready_time": "13:00", "close_time": "17:00", "confirmation_code": "TEL-0425", "remarks": "Booked by phone" }
→ 201 { "pickup": { "id": 9, "source": "manual", "status": "scheduled", "carrier": null, "carrier_id": 4, "...": "..." },
        "alerts": [], "message": null }
```

  `carrier_id` is required (pickup method `manual` or `api` — an `api` carrier can fall back to recording by hand);
  `warehouse_id` is optional and must be the current warehouse; `confirmation_code` ≤ 40, `remarks` ≤ 100; dates /
  times as for booking (today to 30 days ahead, close after ready). Invalid → 400 `16131` (`field`); shipments that
  cannot be included → 409 `16132` (same reasons as booking). The carrier is not called; the record holds the shipments
  like a booked pickup. `POST …/carrier-pickups/<id>/cancel` of a manual pickup only updates WMS.

### Tracking any number

The tracking job (see *Tracking*) follows every `active` waybill whose carrier has `tracking_method: api` and whose
plugin `supports_tracking`, when the waybill was created by the plugin (`source = api`) or the plugin
`tracks_any_number` (FedEx: numbers created on FedEx Ship Manager and typed into WMS are tracked too). The "older
parcel with a reused number" check uses the shipment creation time for `api` waybills and the DN creation time for
`manual` / `file` ones. `POST /warehouse/dn/<id>/carrier-shipment/track` works for such manual waybills as well.

### Actual times and sources

- `PUT /warehouse/delivery/<task_id>/complete/` accepts an optional `shipped_at` (ISO 8601, with or without offset;
  when the goods were actually handed over, default now): the completion time, the actual shipping date, the DN's
  `delivered_at` and `shipped_at` of `dn.delivered` use it. Invalid → 400 `16050`, in the future (beyond a 5-minute
  clock skew) → 400 `16136` (`field: shipped_at`).
- Delivery tasks record who did it: `completed_source` / `signed_source` = `warehouse` (by hand) / `carrier` (carrier
  tracking) / `rule` (automatic signing); `null` = data from before (treat as `warehouse`). Both are returned by the
  delivery task endpoints; `dn.completed` carries `signed_source`.

### Automatic signing by rule

With `carriers.auto_sign_days` set, a job (every `CARRIER_AUTO_SIGN_INTERVAL_MINUTES`, default 60, `0` = off) signs
delivery tasks whose DN is `delivered`, whose current delivery task is `completed`, whose active waybill is **not**
tracked through the API (as defined above) and whose completion time + N days has passed: `signed_at` = completion time
+ N days, `signed_source: rule`, operator = the waybill creator (else the DN creator); it goes through the normal
signing logic (DN `completed`, `dn.completed` pushed). One failure does not stop the others. Signing by hand (with the
actual time) is always possible.

### Flow endpoint

`GET /warehouse/dn/<id>/shipping-flow` (`dn_read` / `packing_read` / `delivery_read`) is what the admin UI and the PDA
render — they do not derive states themselves:

```json
{
  "dn_id": 163, "dn_status": "delivered", "order_number": "SHP-261015-37210", "warehouse_id": 2,
  "route": {"origin_country": "JP", "destination_country": "AU", "international": true, "requires_customs": true},
  "carrier": {"id": 4, "name": "FedEx", "code": "fedex", "integration": "fedex",
              "methods": {"waybill": "api", "pickup": "api", "tracking": "api"},
              "auto_sign_days": null, "tracking_url": "https://www.fedex.com/fedextrack/?trknbr=878978126644"},
  "stage": "in_transit",
  "steps": [
    {"key": "ordered", "state": "done", "at": "2026-10-15T16:56:51+09:00", "source": "api", "summary": {...}, "actions": []},
    {"key": "waybill", "state": "done", "at": "...", "source": "api",
     "summary": {"tracking_number": "878978126644", "source": "api", "service": "...", "label_document_id": 9, "shipment": {...}},
     "actions": [{"key": "print_label", "enabled": true, "document_id": 9}, {"key": "cancel_waybill", "enabled": false, "reason": "...", "reason_code": "CANNOT_CANCEL"}]},
    "..."
  ],
  "problems": [{"code": "...", "level": "error", "message": "...", "step": "waybill"}],
  "tasks": {"picking": [301], "packing": [302], "delivery": 126}
}
```

- `warehouse_id`: the DN's warehouse (pickups are booked per warehouse).
- `carrier`: the carrier of the current delivery task (else of the DN), `null` when neither has one; `integration` =
  plugin code; `tracking_url` = the public tracking page of the current tracking number (`null` without a number or
  with `tracking_method: none`).
- `steps[].key`, always in this order: `ordered`, `picking`, `packing`, `documents`, `waybill`, `pickup`, `handover`,
  `transit`, `delivered`.
- `steps[].state`: `done` / `current` (the flow is at this step) / `todo` (not reached yet) / `warning` (needs attention:
  unclear waybill, outdated documents, tracking exception, automatic step blocked, unclear pickup, pickup date passed …)
  / `blocked` (reached but blocked by an earlier problem, e.g. customs data missing) / `skipped` (not applicable:
  `documents` without customs, `pickup` with `pickup_method: none`, `transit` without tracking, steps not done when the
  DN was cancelled).
- `steps[].at`: when the step actually happened (ISO 8601 with offset); `source`: `warehouse` / `carrier` / `rule` /
  `api` (the order came from the source system; for `waybill` / `pickup`: created / booked through the carrier plugin) /
  `system`.
- `summary` per step: `ordered` {order_number, dn_type, expected_shipping_date}; `picking` {task_ids, picked, planned};
  `packing` {task_ids, packed, planned, package_count, gross_weight_kg, packages[], packages_required} (packages are
  required — `PACKAGES_MISSING` when packed without any — for customs shipments, `api` waybills and file templates with a
  `{package.*}` column without `|default`; not for domestic manual waybills); `documents` {required, customs,
  version, outdated, missing[], ready, documents[] (current document metas)}; `waybill` {method, tracking_number,
  source, service, service_name, label_document_id, label_download_path, shipment (same shape as
  `carrier-shipment.shipment`, or the latest cancelled API shipment), unresolved, can_create, blockers[]}; `pickup`
  {method, pickup (pickup payload or null), contact, schedule}; `handover` {delivery_task_id, actual_shipping_date};
  `transit` {tracking (shipment tracking payload or null), tracking_url, tracked_by_api}; `delivered`
  {estimated_delivery, auto_sign_days, auto_sign_at}. Phase 2: `waybill` also has `export` (only while a file waybill
  is exported and waiting for its number: {exported_at (with offset), batch_id, file_name, export_count, exported_by:
  {id, user_name}}; otherwise null; `shipment.file` keeps {preset, exported_at, export_count, import_batch_id,
  imported_at} of a file waybill) and
  `options` ({values, schema, defaults, locked, locked_reason, locked_message}, see *Shipping service options*);
  `service_name` is the service's display name (file waybills: the label of the template's `service_option`); after
  handover a still-`scheduled` pickup is `done` with `summary.pickup.done: true` and `summary.handed_over: true` (no
  "book again" warning).
- `actions[].key`: `create_waybill`, `enter_tracking`, `cancel_waybill`, `dismiss_waybill`, `print_label`,
  `export_file`, `import_numbers`, `discard_export`, `book_pickup`, `record_pickup`, `cancel_pickup`,
  `complete_shipping`, `refresh_tracking`, `open_tracking`, `sign`, `issue_documents`, `edit_packages`. With
  `waybill_method: file` the waybill step offers `export_file` (single-DN export; `re_export: true` when exported
  already), `import_numbers` (batch import) and, when exported, `discard_export` (`shipment_id`); all carry
  `carrier_id`; new reason codes `FILE_NOT_CONFIGURED` (no usable template / account fields missing) and
  `WAYBILL_EXISTS`. `enabled: false` comes with a
  `reason` (English) and a `reason_code` for translation (`DN_SHIPPED`, `NOT_PICKED`, `NOT_PACKED`, `NO_WAYBILL`,
  `PICKUP_EXISTS`, `NO_OPEN_TASK`, `WAYBILL_UNRESOLVED`, … — `REASON_CODES` in `warehouse/shipping/flow.py` — or a
  carrier-shipment / pickup blocker code or customs problem code); some actions carry the id they act on (`delivery_task_id`, `pickup_id`, `document_id`, `url`). Each key maps
  to an existing endpoint.
- `problems[]`: `{code, level: error | warning, message, step, …}` — customs problems, waybill blockers of the
  configured `api` method, unclear waybill / pickup, tracking exceptions, blocked automatic steps.

### Shipping stage in lists

`stage` (and the DN list's `shipping_stage`) is evaluated in SQL (`warehouse/shipping/stage.py`), the same definition
for the flow and the lists: `closed` (DN closed / cancelled), `delivered` (signed), `preparing` (pending / in progress /
picked), `exception` (unclear API waybill, tracking exception or blocked automatic step, unclear pickup), `in_transit`
(handed over, not signed), and for packed DNs `awaiting_waybill` (no waybill / tracking number yet), `awaiting_pickup`
(the carrier books pickups per shipment — `api` / `manual` — and no pickup holds the waybill) or `awaiting_handover`;
`awaiting_number` = a file waybill has been exported and its tracking number not imported yet (no number on the task).

- `GET /warehouse/dn/` items carry `shipping_stage` and `shipping: {carrier_id, carrier_name, tracking_number}` (computed for the
  whole page in one query); `?shipping_stage=<stage>` filters by it (400 for an unknown stage).
- `GET /warehouse/delivery/` items carry `shipping_stage` (the stage of their DN).

### File mode (no API contract)

Carriers without an API contract work through their own shipping software: WMS exports a CSV in the software's import
format, the labels are printed there, and the CSV with the tracking numbers is imported back (migration
`a7c4e2f9b3d1`).

**Presets.** `GET /warehouse/carrier/file-presets` (`carrier_read`) lists the presets in
`warehouse/carrier/presets/<country>/<code>.json` (contributions welcome — same structure, with `version` and
`sources`): `[{code, name: {en-US, zh-CN, ja-JP}, country, version, notes, verified, sources[], tracking_url_template,
tracking_url_fallback, tracking_url_notes, service_option, service_options[], account_fields[], columns: [{header,
required, max_length}], export: {encoding, delimiter, quoting, header, line_ending, length_unit, charset}, import:
{encoding, encoding_fallbacks, header_rows, reference_column, tracking_column, tracking_normalize} | null}]`.
The first three are **not tested in the carrier software yet** (`verified: false`):

| Code | Software | Columns | Notes |
|------|----------|---------|-------|
| `jp.yamato_b2cloud` | Yamato B2 Cloud, basic layout | 95 | Shift_JIS, lengths in Shift_JIS bytes, JIS level 1/2 characters only (`charset: jis_x0208`); import = "発行済データの検索 → 外部ファイル出力" (column 1 reference, column 4 tracking number); account fields `billing_customer_code` / `billing_class_code` / `freight_mgmt_no`; tracking page `https://member.kms.kuronekoyamato.co.jp/parcel/detail?pno={tracking_number}` |
| `jp.sagawa_ehiden3` | Sagawa e-Hiden III, 標準_飛脚宅配便_CSV_ヘッダ有 | 74 | address / name fields full width (`zenkaku` filter); import = 出荷履歴 (UTF-8, falls back to Shift_JIS; rows with 削除区分 = 2 skipped); the old tracking URL is kept, `tracking_url_fallback` = `https://e-customer.sagawa-exp.co.jp/k2k` (no deep link while Sagawa's lookup service is restricted) |
| `jp.japanpost_yupri_r` | Japan Post Yu-Pack Print R, 標準フォーマットv3 (明細数 = 1) | 87 | no header, every field quoted; import = 新ＤＥＮＦＤ確定 (column 8 reference, column 7 tracking number); tracking page `https://trackings.post.japanpost.jp/services/srv/search/direct?reqCodeNo1={tracking_number}` |

**Carrier settings** (`GET / PUT /warehouse/carrier/<id>`): `file_preset` (preset code or `null`), `file_template`
(changes merged over the preset — objects key by key, lists replaced, `null` removes a key; the whole template
without a preset; `null` = the preset as is), `file_account` (`{key: text}` for the template's `account_fields`), and
the read-only `file_mode: {configured, ready, preset: {code, name, verified, exists} | null, error, service_options,
account_fields, missing_account_fields, can_import, tracking_url_template, tracking_url_fallback, encoding}`.
The read-only `service_options` lists the shipping service options DNs of this carrier are checked against (the file
template's for `waybill_method: file`, else the integration plugin's; `[]` when none).
`available_methods.waybill` offers `file` once a preset / template is configured; `file_preset` and
`waybill_method: file` can be set in the same request. Invalid values → 400 `16135` with `field` (`file_preset`,
`file_template.export.columns[3].value`, `file_account.<key>`; removing the template while the method is `file` →
`file_preset`). With no `tracking_url_template` of its own and no plugin one, the carrier's tracking page comes from the
file template.

**Template** (same structure as a preset; `shift_jis` is always read / written as cp932):

```
{code, name{locale}, country, version, sources[], notes, verified, tracking_url_template, tracking_url_fallback?,
 service_option?   (the option that names the service: stored as the waybill's service_type / service_name),
 export: {format: 'csv', encoding: 'cp932' | 'utf-8' | 'utf-8-sig', delimiter, quoting: 'minimal' | 'all',
          header: bool, line_ending: '\r\n' | '\n', length_unit: 'chars' | 'sjis_bytes', row_unit: 'dn',
          charset?: 'jis_x0208', columns: [{header, value, required?, max_length?, notes?}]},
 import: {encoding, encoding_fallbacks[], delimiter?, header_rows, reference_column (1-based), reference_value: 'DN{dn.id}',
          reference_parse: '^DN(\\d+)$', tracking_column, tracking_normalize: 'digits' | null,
          skip_rows_when?: {column, equals}},
 service_options: [{key, kind: 'select' | 'date' | 'money' | 'text', label{locale}, values?[{value, label}], default?, help?}],
 account_fields: [{key, label{locale}, required, help?}]}
```

`value` is text with `{variable|filter:arg|…}` placeholders (`{{` / `}}` for literal braces). Variables: `dn.id`,
`dn.order_number`, `dn.remark`, `dn.expected_shipping_date`, `dn.special_handling`, `dn.packaging_info`, `ship_date`
(today in the warehouse time zone), `ship_to.*` / `shipper.*` (structured address fields — see below — plus `contact`,
`address_text`, `country_name`, `latin.<field>`), `goods.summary` (names joined with " / "), `goods.first_name`,
`goods.quantity`, `goods.count`, `package.count` (empty without packages), `package.total_weight_kg`,
`options.<key>` (the DN's shipping options, defaults filled in), `carrier.account.<key>`, `carrier.name`,
`carrier.code`. Filters: `digits`, `truncate:n`, `truncate_bytes:n` (cp932 bytes, never splits a double-byte
character), `slice:a:b`, `date:%Y/%m/%d` (empty stays empty), `default:x`, `map:k=v,k=v`, `upper`, `zenkaku`
(half width → full width), `nospace` (removes half / full width spaces), `jp_phone` (`+81 …` → domestic `0…`,
full-width digits → half width), `int`. Unknown variables / filters are rejected when the template is saved. The
reference column is always `DN{dn.id}` (order numbers may be empty, duplicated or too long); the order number goes into
a remark column printed on the label.

**Export** — `POST /warehouse/dn/carrier-files/export` (`packing_edit` / `delivery_edit`), JSON
`{carrier_id, dn_ids: [...] (1–500), warehouse_id?}` (`X-WAREHOUSE-ID` / body `warehouse_id` is the warehouse; without
one the first DN's):

- 200: the file — `Content-Disposition: attachment; filename="<carrier-code>_<YYYYmmdd-HHMMSS>.csv"`, bytes in the
  template encoding (`text/csv; charset=Shift_JIS` for cp932), headers `X-Batch-Id` / `X-Row-Count`. Every DN gets a
  `file` waybill record with `status: exported` (a new *open* status: at most one open record per DN still holds) and
  `file_batch_id`; exporting an exported DN again keeps the record and updates the batch. An export batch is recorded
  (`carrier_file_batches`).
- Each row is checked first; **any problem blocks the whole batch** (no file, no records): 409 `16137`
  `details {rows: [{dn_id, order_number, problems: [{column, column_index?, code, message, …}]}], total}`. DN codes:
  `DN_NOT_FOUND`, `OTHER_WAREHOUSE`, `NOT_PACKED`, `DN_SHIPPED`, `DN_CLOSED`, `NO_OPEN_TASK`, `CARRIER_NOT_SET`,
  `OTHER_CARRIER` (the delivery task's carrier — else the DN's — must be this carrier), `WAYBILL_EXISTS` (another open
  waybill), `TRACKING_NUMBER_EXISTS`, `OPTION_INVALID` (`column: options.<key>`); column codes (`column` = header text):
  `REQUIRED`, `TOO_LONG` (`length`, `max_length`, `length_unit` — names / addresses are never truncated silently),
  `UNENCODABLE` (`characters`), `CHARACTER_NOT_ALLOWED` (outside JIS level 1/2 with `charset: jis_x0208`). Line breaks
  inside a value become spaces.
- 400 `16138`: invalid `carrier_id` / `dn_ids` / `warehouse_id` (`field`); 409 `16138`: the carrier's
  `waybill_method` is not `file`, its template is unusable, or a required account field is empty
  (`field: file_account.<key>`, `details.missing_account_fields`).

**Import** — `POST /warehouse/dn/carrier-files/import` (`packing_edit` / `delivery_edit`), multipart `file`
(≤ 5 MB, ≤ 5000 rows), `carrier_id`, `dry_run` (`true` default / `false`), `warehouse_id?`:

```json
{ "dry_run": true, "file_name": "b2.csv", "encoding": "cp932", "batch_id": null,
  "rows": [{"line": 2, "reference": "DN163", "dn_id": 163, "order_number": "SHP-1", "tracking_numbers": ["401234567890"],
            "result": "matched", "reason_code": null, "message": null, "primary": true}, "..."],
  "summary": {"matched": 2, "not_found": 1, "already_has_number": 0, "same_number": 0, "invalid": 0, "skipped": 0,
              "dns": {"matched": 1, "not_found": 1, "already_has_number": 0, "same_number": 0, "invalid": 0}} }
```

- The file is decoded with `import.encoding` and `encoding_fallbacks` (a BOM or valid UTF-8 wins when UTF-8 is a
  candidate); `header_rows` are skipped unless they already look like data; `skip_rows_when` rows are counted in
  `summary.skipped`; the reference is parsed with `reference_parse`, the tracking number normalized
  (`tracking_normalize: digits` drops hyphens).
- Several rows of one DN (several packages) = several numbers: the first is the main tracking number, the others go to
  `package_tracking_numbers`.
- `result`: `matched` (will be / was written), `not_found` (no such DN in this company / warehouse, or not a
  `DN<id>` reference), `already_has_number` (the DN has a different number — decide by hand), `same_number` (already
  imported; ignored), `invalid` (no tracking number, DN closed, another carrier, an unresolved API waybill, the number
  belongs to another DN, no open delivery task, shipped without an exported waybill).
- Every row other than `matched` has a `reason_code` and an English `message` (fallback); the UI renders localized text
  from `reason_code`: `not_found` → `DN_NOT_FOUND` / `NOT_WMS_REFERENCE` / `NO_REFERENCE`; `invalid` →
  `NO_TRACKING_NUMBER` / `DN_CLOSED` / `OTHER_CARRIER` / `NUMBER_OWNED_BY_OTHER_DN` / `API_SHIPMENT_UNRESOLVED` /
  `EXPORTED_FOR_OTHER_CARRIER` / `SHIPPED_WITHOUT_FILE_WAYBILL` / `NO_OPEN_TASK`; `same_number` → `ALREADY_IMPORTED` /
  `ALREADY_SAVED`; `already_has_number` → `HAS_OTHER_NUMBER`. Fields for the text (only when present):
  `existing_tracking_number` (the DN's current number / the number owned by another DN), `other_carrier` /
  `other_carrier_id`, `other_dn_id`, `shipment_status`.
- `dry_run: false` re-checks each DN under its lock and writes the `matched` ones in one transaction: an `exported`
  record becomes `active` (a DN that was never exported gets a new `file` waybill), the delivery task gets the main
  number (and the carrier if it had none) — the same as saving a tracking number, so tracking (`tracks_any_number`),
  pickups and the flow work as for any waybill. An import batch is recorded (`batch_id`).
- 400 `16138`: no / empty / too large / unreadable file (`field: file`), invalid `dry_run` / `carrier_id`; 409 `16138`:
  not in file mode / the template cannot import.

**Discarding an export** — `POST /warehouse/dn/<id>/carrier-shipment/discard-export` → the exported record becomes
`cancelled` (`reason: discarded`; WMS only — delete it in the carrier software too if it was printed); 200 = the
`GET …/carrier-shipment` body plus `waybill` (current waybill of any source or `null`); nothing to discard → 409
`16075`. Entering a tracking number by hand while a DN is exported replaces the export (`reason: replaced`); `file`
waybills are replaced / cleared like manual ones.

**Batches** — `GET /warehouse/dn/carrier-files/batches?carrier_id=&warehouse_id=&limit=` (`dn_read` / …): newest
first, `[{id, carrier_id, warehouse_id, kind: export | import, file_name, row_count, summary, created_at, created_by,
created_by_user}]` (the file contents are not stored).

### Structured addresses

One set of field names for recipients, warehouses, the DN's ship-to snapshot and the `ship_to` / `shipper` handed to
plugins and file templates: `country` (ISO 3166-1 alpha-2), `postal_code`, `admin_area` (state / province /
prefecture), `locality` (city), `dependent_locality`, `address_line1`, `address_line2`, `organization`, `name`,
`phone`, `email`, and the Latin transliteration `latin: {name, organization, address_line1, address_line2, locality,
admin_area}` (international labels; without it the local text or the customs consignee is used).

- **Recipients** (`/warehouse/recipient/`): new `admin_area`, `locality`, `dependent_locality`, `address_line1`,
  `address_line2`, `organization`, `latin`; `zip_code` *is* `postal_code` (both accepted, both returned; different
  values → 400 `14019`). `null` clears a field. When only the structured fields are given (no `address`), the one-line
  `address` is generated in the country format (and regenerated when they change).
- **Warehouses** (`/warehouse/warehouse/`): `admin_area`, `locality`, `address_line1`, `address_line2`, `latin`
  (`postal_code` ≡ `zip_code`); `address` is generated the same way; `address_en` stays the one-line Latin address and
  is generated from `latin` when it is empty.
- **DN**: `ship_to` (json snapshot) and `shipping_options` (json). `POST /warehouse/dn/` accepts `ship_to`; without it
  the recipient's structured fields are copied (legacy recipients without them → `null`). `shipping_address` is still
  accepted; with only `ship_to` it is generated in the country format (neither → 400 `40000`). `PUT` (pending DNs)
  replaces `ship_to` / `shipping_options`. After packing, `GET / PUT /warehouse/dn/<id>/ship-to` (`{ship_to}` → `{ship_to,
  ship_to_effective, shipping_address, locked, locked_reason, locked_message}`) changes the snapshot (e.g. to shorten an
  address a carrier file rejected as too long); `shipping_address` is regenerated from it, the customs snapshot is not
  touched. Locked like the shipping options: an `api` waybill or a `file` waybill (exported / active) → 409 `16076`
  (`details.reason: SHIP_TO_LOCKED`), shipped → 409 `16065`, closed → 400 `16001`; a manual tracking number does not
  lock it. The DN detail returns `ship_to`, `shipping_options` and
  `ship_to_effective` (the address used for labels / files: `ship_to`, else the recipient's structured fields, else
  `address_line1` = `shipping_address` plus the recipient's name / phone / postal code). The destination country of the
  route is the customs snapshot's → `ship_to.country` → the recipient's.
- Invalid fields → 400 `14019` (not a string / too long, `field` such as `ship_to.latin.name`) / `14020` (country).
  Unknown keys are ignored; empty values may be sent as `null`.
- Formatting: `warehouse/common/address_format.py` — `format_lines(address, style='local' | 'latin')` /
  `format_single_line(...)`, using the country rules (Japan: 〒postal code / prefecture + city / block + building;
  US: street / `City, ST ZIP`; others: street / `city state postal code` / country).
- Plugins: `shipper` / `recipient` keep their previous keys (FedEx is unchanged) and add `local` (structured address)
  and `latin`; domestic recipients take address lines / city / state from `ship_to`.

### Country rules

`warehouse/country_rules/`: `base.py` (`CountryRules`: `customs_line_problems(line)`, `customs_problems(ctx)`,
`exporter_tax_label`, `address_format` (local / latin line templates), `phone_normalize`), `registry.py`
(`rules_for(country)`; unregistered countries get the default rules; third-party packages register through the entry
point group `goodsmart_wms.country_rules`), `jp.py`, `us.py`. The core only calls the registry. The Japanese customs
checks (9-digit export statistics code `JP_EXPORT_CODE_MISMATCH` / `JP_EXPORT_CODE_MISSING` /
`JP_EXPORT_VALUE_UNVERIFIED`) moved into `jp.py` with the same messages; they apply when the export country (warehouse,
else company) is Japan. For Japanese exporters without a `tax_id_label`, documents now print `Corporate No.` (default
rules: `Tax ID`); documents issued before with `Tax ID` are not reported as outdated.

### Shipping service options

Plugins (`CarrierAdapter.service_options`, empty for FedEx) and file templates (`service_options`) declare options such
as `delivery_date`, `delivery_time_slot`, `invoice_type`, `cod_amount`, `cool_type`. The DN's carrier decides which
apply (the file template with `waybill_method: file`, else the integration plugin).

- `GET /warehouse/dn/<id>/shipping-options` → `{options, values, schema, defaults, locked, locked_reason,
  locked_message}`; `PUT` with `{options: {...}}` (`packing_edit` / `delivery_edit`) replaces them → 200 same body.
  Unknown key / select value not listed / invalid date (`YYYY-MM-DD`) / money (≥ 0, 2 decimals) / text → 400 `16139`
  (`field: options.<key>`; missing `options` → `field: options`).
- Locked once an `api` waybill or a `file` waybill (exported / active) exists → 409 `16076`
  (`details.reason: SHIPPING_OPTIONS_LOCKED`, `carrier_shipment`); shipped → 409 `16065`. A manual tracking number
  does not lock them.
- `POST /warehouse/dn/` accepts `shipping_options`: declared keys are validated (400 `16139`,
  `field: shipping_options.<key>`), other keys are kept (the carrier may still change); keys the carrier does not
  declare are ignored when exporting / creating.
- Creating a shipment passes `shipment['options']` (declared keys only); exporting uses `{options.<key>}`; an invalid
  stored value is an `OPTION_INVALID` export problem.
- File waybills show the service name: `shipment.service_type` = the value of the template's `service_option`,
  `service_name` = its label (`service_label` has all locales).

## Carrier Integrations (automatic shipments)

When the carrier of a DN has an online integration, WMS can create the shipment in the carrier's system
instead of creating it by hand on the carrier's website and typing the tracking number. Everything else stays the
same: the tracking number is saved on the delivery task by the existing tracking logic, shipping completes and
`dn.delivered` is sent as before (the CI / PL print no tracking number, so they are not re-issued). Integrations are carrier-agnostic: each carrier is a
plugin (`warehouse/carrier/integrations/`); FedEx is built in (see *FedEx plugin*) and you can write your own (see
*Writing a carrier plugin*). Carriers without an integration keep the manual path — saving the tracking number by hand
(`PUT /warehouse/delivery/<task_id>/tracking`, recorded as a `manual` waybill) — which is also the fallback when an
integration is unavailable. Domestic shipments go through the same path without customs (see *Shipping Flow*); the
customs extension (CI / PL, commodities, ETD, trade documents) only takes part when the route requires customs.

### Setting up an integration

Integrations are set up per carrier, under "Online integration" on the carrier edit page of the admin UI: at most one
integration per carrier (`carrier_integrations`) — pick the plugin and fill in account, keys, service, labels, etc.
The settings belong to the carrier record, so each company uses its own carrier records and its own carrier account
(charges go to that account).

| Method | Path | Permission |
|--------|------|------------|
| `GET` | `/warehouse/carrier/integrations` | `carrier_read` |
| `GET` | `/warehouse/carrier/<id>/integration` | `carrier_read` |
| `PUT` | `/warehouse/carrier/<id>/integration` | `carrier_edit` |
| `DELETE` | `/warehouse/carrier/<id>/integration` | `carrier_edit` |
| `POST` | `/warehouse/carrier/<id>/integration/test` | `carrier_edit` |

- `GET …/integrations`: installed plugins `[{code, name, portal_name, portal_url, label_formats, fields[]}]`; `fields`
  is the form definition (see *Writing a carrier plugin*).
- `GET …/<id>/integration`: `{adapter, name, enabled, settings, secrets: {key: is set}, ready, problems[{code, message,
  field?, fields?}], updated_at, updated_by}`, or `null` without an integration. `settings` includes the defaults; keys
  are only reported as set / not set and are never returned. `ready` = plugin installed, settings complete and valid
  (regardless of `enabled`). Carrier list / detail responses also carry `integration: {adapter, name, enabled} | null`.
- `PUT` with `{adapter, enabled, settings, secrets}`, returns the same shape as `GET`:
  - `settings` replaces the saved settings: unknown keys are dropped, omitted / empty values use the default. Required
    settings may be left empty for now (`ready: false`, listed in `problems`).
  - `secrets` omitted or empty = keep the saved value (dropped when the plugin changes). The keys are stored as JSON
    encrypted with `SETTINGS_ENCRYPTION_KEY` (Fernet); if that key changes and they can no longer be decrypted, the
    integration reports `CARRIER_NOT_CONFIGURED` (`field: secrets`) — enter them again.
  - Invalid input → 400 `16124` (`field`: `adapter` / `enabled` / `settings.<key>` / `secrets.<key>`).
  - An empty `adapter` removes the integration (like `DELETE`, which answers 204).
- `POST …/test`: connects to the carrier once (authentication only, no shipment, 20-second limit) with the settings in
  the body (same as `PUT`; keys left out use the saved ones; a body without `adapter` = the saved settings), without saving. A failed
  connection is also 200: `{ok: false, message, errors?, status_code?, permission_denied?, missing?}`.
- Turned off (`enabled: false`): no new shipments (blocker `CARRIER_INTEGRATION_DISABLED`); existing shipments can
  still be cancelled / dismissed.
- While the carrier has unfinished automatic shipments (`pending` / `active` / `unknown` / `cancelling`) its
  integration cannot be removed or switched to another plugin (cancelling needs it) → 409 `16125`
  `details.shipments[{id, dn_id, status, tracking_number}]`. Changing settings or keys and turning it off still work.

#### Upgrading from the `FEDEX_*` environment variables

WMS no longer reads any `FEDEX_*` variable (and the `FEDEX_ALLOWED_COMPANY_IDS` allow-list is gone: any carrier with an
integration can use it). Migration `c7e1a9d3f5b2` creates `carrier_integrations` and backfills `carrier_id` on existing
`dn_carrier_shipments` (the carrier of the DN's current active delivery task, else the DN's carrier). After deploying,
while the old variables are still in `.env`, run once:

```bash
flask carrier import-fedex-env [--carrier-id <id>] [--overwrite] [--dry-run]
```

- Target: the carrier given by `--carrier-id`; otherwise every carrier with code `fedex` (case-insensitive) in the
  companies of `FEDEX_ALLOWED_COMPANY_IDS` (all get the same credentials). Carriers that already have an integration
  are skipped unless `--overwrite`; `--dry-run` only lists what would be imported.
- Imported: `FEDEX_API_KEY` / `FEDEX_SECRET_KEY` / `FEDEX_ACCOUNT_NUMBER` (all three required); `FEDEX_API_BASE` unset
  or containing `sandbox` → `environment: sandbox`, otherwise `production`; `FEDEX_SERVICE_TYPE`, `FEDEX_PICKUP_TYPE`,
  `FEDEX_DUTIES_PAYMENT_TYPE`, `FEDEX_DEFAULT_LABEL_FORMAT`, `FEDEX_LABEL_THERMAL_IMAGE_TYPE`,
  `FEDEX_LABEL_THERMAL_PAGE_SIZE`, `FEDEX_ETD_ENABLED`. Values the plugin does not support (e.g. a service type that
  is not offered, `PNG`) are reported and replaced by the default. `FEDEX_LABEL_A4_*`, `FEDEX_LABEL_THERMAL_STOCK_TYPE`
  and `FEDEX_DOCUMENT_API_BASE` are no longer configurable (see *FedEx plugin*).
- The timeouts / limits are renamed to `CARRIER_*` (see *Configuration*) and are not migrated automatically. Afterwards
  the `FEDEX_*` variables can be removed from `.env`.
- Until the import, the FedEx carrier has no integration: no automatic shipments (`CARRIER_NOT_INTEGRATED`), and
  existing shipments cannot be cancelled from WMS (409 `16072`).

### Shipment API

| Method | Path | Permission |
|--------|------|------------|
| `GET` | `/warehouse/dn/<id>/carrier-shipment` | `dn_read` / `packing_read` / `delivery_read` |
| `POST` | `/warehouse/dn/<id>/carrier-shipment` | `packing_edit` / `delivery_edit` |
| `POST` | `/warehouse/dn/<id>/carrier-shipment/cancel` | `packing_edit` / `delivery_edit` |
| `POST` | `/warehouse/dn/<id>/carrier-shipment/dismiss` | `packing_edit` / `delivery_edit` |

`GET` returns `{enabled, carrier, integration, can_create, blockers[{code, message, goods_code?, field?}], unresolved,
can_dismiss, can_cancel, etd_enabled, default_label_format, label_formats{A4|THERMAL: {image_type, stock_type}},
route, declared_value_carriage, delivery_task_id, tracking_number, shipment, warnings[]}` (only automatic shipments,
`source: api`; `route` = `{origin_country, destination_country, international, requires_customs}`):

- `carrier`: the carrier used for the shipment, `{id, name, code}` — the carrier of the current active delivery task,
  else of the DN (the same one the CI prints); `null` if neither has one.
- `integration`: that carrier's integration, `{code, name, portal_name, portal_url, enabled, ready}`; `null` = the
  carrier has none (or does not belong to the DN's company) — create the shipment on the carrier's website and save
  the tracking number. `enabled` is the integration's on/off switch; `ready` = plugin installed, turned on, settings
  complete and valid. `portal_name` / `portal_url` name the carrier's shipping portal (where to check / cancel an
  unclear shipment; FedEx Ship Manager for FedEx).
- `enabled`: same as `integration.ready` (whether a shipment can be created is `can_create`).
- `etd_enabled`, `default_label_format` and `label_formats` (only the formats the plugin supports) come from the plugin
  and the integration settings; `false` / `null` / `{}` without a usable plugin.
- `tracking_number`: the tracking number currently saved on the delivery task (automatic or entered by hand); `null`
  without a delivery task.
- `can_create`: no blockers **and** `unresolved` is `null`.
- `unresolved`: the record whose result is not settled yet, or `null`: `{id, status: "pending" | "unknown" |
  "cancelling", reason, tracking_number, transaction_id, created_at, updated_at}` (see *Shipment records and unclear
  results*).
- `can_dismiss`: `true` when `unresolved.status` is `unknown`, or `cancelling` while no cancellation is in progress.
- `can_cancel`: `true` for an active shipment, a `cancelling` one while no cancellation is in progress, or an `unknown`
  record with a tracking number; `false` after the DN is shipped.
- `shipment`: the active (or `cancelling`) shipment, otherwise the latest cancelled one, otherwise `null`:
  `{id, source (api), carrier (plugin code), carrier_id (carrier that created it), tracking_number, package_tracking_numbers,
  status: active|cancelling|cancelled, reason, service_type, service_name, ship_date, package_count, net_charge,
  currency, declared_value, label_format, image_type, label_stock_type, label_document_id, label_download_path,
  label_file_name, label_content_type, label_parts[], etd_document_id, transaction_id, sender_country, created_at,
  created_by, created_by_user, updated_at, cancelled_at, cancelled_by, dismissed_at, dismissed_by, tracking, pickup,
  tracking_url}` (`tracking_url` = public tracking page of the carrier's `tracking_url_template`, else the plugin's;
  `null` with `tracking_method: none`).

`POST` takes an optional body `{"label_format": "A4" | "THERMAL"}` (how the label will be printed: `A4` = plain-paper
printer, `THERMAL` = label printer; omitted = the integration's default; anything else, or a format the carrier's
plugin does not support → 400 `16077`). The plugin decides the file format / stock of each format (FedEx: see *FedEx
plugin*).

### Creating a shipment

- With a pending or unclear request on record, `POST` answers 409 `16079` (`details.unresolved`) without calling the
  carrier.
- **Blockers** (all listed at once; `POST` answers 409 `16072` with `details.blockers`):
  - carrier and integration: no active delivery task (`DELIVERY_TASK_MISSING`) / task completed
    (`DELIVERY_TASK_COMPLETED`), neither the task nor the DN has a carrier (`CARRIER_NOT_SET`), the carrier has no
    integration or does not belong to the DN's company (`CARRIER_NOT_INTEGRATED`, with `carrier_id`), plugin not
    installed (`CARRIER_INTEGRATION_UNAVAILABLE`), integration turned off (`CARRIER_INTEGRATION_DISABLED`), required
    settings / keys missing (`CARRIER_NOT_CONFIGURED`, `fields` lists them), invalid settings (`CARRIER_CONFIG_INVALID`);
  - route (replaces the former `NOT_EXPORT`): the plugin's `scopes` do not include the shipment's scope (domestic /
    international; `SCOPE_NOT_SUPPORTED`, with `scope` — FedEx carries international shipments only), customs is
    required but the DN has no customs snapshot (`CUSTOMS_DATA_MISSING`, `field: customs`), customs is required but the
    plugin cannot submit customs data (`CUSTOMS_NOT_SUPPORTED`), the plugin cannot create waybills
    (`WAYBILL_NOT_SUPPORTED`). With any of these the plugin's own checks are skipped;
  - the DN: not `packed` (`NOT_PACKED`) or already shipped
    (`DN_SHIPPED`), an active shipment already exists (`SHIPMENT_EXISTS`) or a tracking number was saved by hand
    (`TRACKING_NUMBER_EXISTS`), no packages (`PACKAGES_MISSING`) / more packages than the plugin allows
    (`TOO_MANY_PACKAGES`, with `limit`) / a package without weight or dimensions (`PACKAGE_INCOMPLETE`); with customs:
    error-level customs problems (same codes as the customs view), consignee name and company both missing
    (`RECIPIENT_NAME_MISSING`) or phone missing (`RECIPIENT_PHONE_MISSING`); domestic: recipient name / phone missing
    (`RECIPIENT_NAME_MISSING` / `RECIPIENT_PHONE_MISSING`, `field: recipient.*`), no DN shipping address
    (`RECIPIENT_ADDRESS_MISSING`), neither warehouse nor company address (`SHIPPER_ADDRESS_MISSING`);
  - the plugin's own checks (address fits, incoterm vs. duties payer, …; FedEx: see *FedEx plugin*).
- **Declared value for carriage** above the customs value of the packed goods is lowered to that value (carriers
  reject a declared value above the customs value — verified with FedEx — and after partial packing only the goods
  actually shipped are covered). The value actually submitted is stored as `shipment.declared_value`; `warnings`
  carries `{code: "DECLARED_VALUE_CAPPED", message, requested, applied}` (in `GET` as a preview while no shipment is
  active, in the `POST` response for what was done).
- With customs: if there is no current CI / PL (or they are outdated) they are issued first with the normal logic.
  Domestic shipments issue no documents.
- If the plugin has electronic documents enabled (FedEx: `etd_enabled`), the current CI PDF is uploaded to the carrier
  first and referenced by the shipment; otherwise the warehouse prints the CI and packs it with the goods as before.
- What WMS hands to the plugin (carrier-agnostic, shape documented on `CarrierAdapter`; every shipment carries
  `route`, and `commodities` / `invoice` / `documents` are `null` unless customs is required). Domestic: shipper =
  company name (local, else English), warehouse address (local, else English; the company's if the warehouse has
  none), warehouse phone (else the company's); recipient = the DN recipient (name, contact, phone, email, postal code)
  with the DN shipping address as one string (`address_text` = `address_line1`). With customs: shipper = company export
  profile (English name, phone — warehouse first, contact, tax ID) with the warehouse English address (company address
  if the warehouse has none; one English string, the plugin decides how to split it); consignee and recipient tax ID /
  tax ID type from the customs snapshot; commodities from the invoice lines (**packed quantity**, HS code, country of
  origin from the goods master data, unit value, amount, weight); one package per WMS package (gross weight kg,
  dimensions mm); the declared value for carriage split evenly across the packages; freight / insurance as in the
  snapshot; the DN order number as reference.
- Commodity weight: goods with a unit weight → unit weight × packed quantity; goods without one share the rest of
  the total gross weight (or, if nothing is left, the gross weight by quantity).
- While a shipment is active the delivery task keeps its tracking number: completing the delivery, saving the
  tracking number or updating the task with a different number → 409 `16078` (cancel the shipment first; an empty
  value on completion / update means "unchanged"). Numbers are compared ignoring spaces / hyphens and a matching one
  is stored as the shipment's own spelling. A `carrier_id` on completion / update / saving the tracking number that is
  not the carrier that created the shipment → 409 `16078` as well, with the requested `carrier_id` added to `details`:
  carriers are compared by record id, so another carrier record with the same code is rejected too (records from
  before the migration without `carrier_id` are compared by code / integration plugin); `carrier_id: null` means
  "unchanged". The tracking number of a cancelled (or compensation-cancelled) shipment of the DN cannot be saved or
  shipped with any more → 409 `16078`
  `details {tracking_number: null, cancelled_tracking_number, carrier, status: "cancelled"}`.
  While a request is pending or its result is unclear, saving a
  tracking number or updating the task with one → 409 `16079` (so that nobody books a second shipment by hand on the
  carrier's website; clearing is allowed). While a shipment is `cancelling`, saving any tracking number (the same one
  included) or clearing it → 409 `16079` (an empty value on update means "unchanged").
- While a shipment is pending, active, unclear or being cancelled: adding or deleting a delivery task of the DN → 409
  `16078` (a new task would become the current one and bypass the tracking lock; a tracking number given on creation
  is checked as above); changing the customs data (`PUT /warehouse/dn/<id>/customs`) or the packages → 409 `16076`
  (the data was submitted with the shipment; `details: {reason: "CARRIER_SHIPMENT_OPEN", status, carrier,
  carrier_shipment: {id, status, tracking_number}}`); issuing documents with different data → 409 `16076`
  (`reason: "DOCUMENT_DATA_CHANGED"`, see *Documents*). Cancel the shipment first, or resolve an unclear one. All these
  checks lock the DN row first, so they are serialized with creating / cancelling.
- **Three steps; no DN row lock and no open database transaction while waiting for the carrier**:
  1. lock the DN, check the preconditions, issue the CI / PL if needed, insert a `pending` `dn_carrier_shipments`
     record (`carrier` = plugin code, `carrier_id` = the carrier) and commit (a partial unique index allows at most one
     `pending` / `active` / `unknown` / `cancelling` record per DN);
  2. (with electronic documents, upload the CI first) create the shipment through the plugin. The whole creation is
     limited to `CARRIER_CREATE_BUDGET_SECONDS` (default 90): the connect / read timeout of every request (token /
     document upload / shipment) is shrunk to the time left, and with less than 5 seconds left the next request is not
     sent (→ 504 `16074` with `details.budget_exhausted: true`, record `failed`), so the worker is not killed mid-way;
  3. on success the tracking number is committed on the record first, then in one transaction: record → `active`,
     tracking number → delivery task (existing tracking logic; a task without a carrier also gets the shipment's
     carrier), the CI / PL issuing logic runs once more (no tracking number on them, so unchanged data gives no new
     version; if the document data was changed during the request → `16076`, handled like a save failure), the label
     archive built by the plugin stored as a document with `doc_type: shipping_label`.
- **Carrier errors** (`details` always carries `maybe_processed` and `unresolved`; plugins classify their errors this
  way, see *Writing a carrier plugin*):
  - explicit rejection (4xx) → 502 `16073` (`details.errors`, `transaction_id`), record `failed`
    (`reason: rejected`); request not sent (connection not established, access token failure, …) → record `failed`
    (`not_sent`), 504 `16074` for a connect timeout; document upload failure → `failed` (`etd_upload_failed`).
    No shipment exists in these cases; just retry. 401 / 403 also set `details.permission_denied: true` (wrong keys,
    or the API is not enabled for the carrier account).
  - unclear result: read timeout → 504 `16074`; connection dropped after connecting, 5xx, a 200 response that cannot be
    understood → 502 `16073`. The record becomes `unknown` (`reason`: `timeout` / `connection_error` / `server_error` /
    `bad_response`), `maybe_processed: true`, `details.unresolved` is the record. The shipment may exist on the
    carrier side: check the carrier's portal (`integration.portal_name`), cancel it there if it exists, then dismiss
    the record (below).
  - an unparsable response that still names a tracking number is handled like a save failure (the shipment is
    cancelled).
- **Saving fails after the carrier created the shipment** (502 `16095`, also for business errors such as the DN being
  shipped or the document data being changed meanwhile; `details {carrier, tracking_number, transaction_id, error: {code, message}, compensation:
  {attempted, cancelled, cancel_transaction_id, error}, unresolved, maybe_processed}`): WMS first re-reads the record
  in a new transaction. If it is already `active` with the same tracking number (the commit went through but the caller
  saw an error) the shipment is kept and the request succeeds (201). If the record cannot be read, the shipment is
  **not** cancelled and the record becomes `unknown` (`reason: save_unconfirmed`, with `tracking_number`). Otherwise
  WMS cancels the shipment with the carrier (compensation) and commits the outcome on the record separately (kept
  although the main transaction rolled back): cancellation confirmed (or the carrier answers "already cancelled / not
  found") → `cancelled` (`reason: compensated`, `details.unresolved` null); cancellation failed / not confirmed →
  `unknown` (`reason: compensation_failed`, with `tracking_number`) — cancel it again (`POST …/cancel`) or dismiss it.
- CI / PL issued in step 1 are committed with the `pending` record and stay valid when the carrier fails.
- The label is downloaded with `GET /warehouse/dn/<id>/customs-documents/<label_document_id>/file` (PDF inline,
  other formats such as ZPL / EPL as attachment). Labels do not count for the "current CI and PL" shipping gate.

### Shipment records and unclear results

Every shipment request leaves a record in `dn_carrier_shipments`:

| `status` | Meaning |
|----------|---------|
| `pending` | Carrier request in progress |
| `active` | Active shipment |
| `cancelling` | The active shipment is being cancelled (`reason: cancel_in_progress`) or the result of cancelling it is unclear (`reason`: `timeout` / `connection_error` / `server_error` / `bad_response` / `save_failed` / `interrupted`; `stale` = `cancel_in_progress` older than `CARRIER_PENDING_STALE_MINUTES`). Tracking number and label are kept; the DN cannot be shipped |
| `cancelled` | Cancelled (by the user; automatically after a save failure, `reason: compensated`; the carrier answered already cancelled, `reason: already_cancelled`) |
| `failed` | Rejected by the carrier / not sent — no shipment exists (`reason`: `rejected` / `not_sent` / `etd_upload_failed` / `budget_exhausted` / `interrupted`) |
| `unknown` | Unclear result — the shipment may exist on the carrier side (`reason`: `timeout` / `connection_error` / `server_error` / `bad_response` / `compensation_failed` / `save_unconfirmed` / `interrupted`) |
| `dismissed` | The operator confirmed that the carrier has no such shipment (or cancelled it there); records with a tracking number only after the carrier confirmed it is cancelled / not found |

- A `pending` record older than `CARRIER_PENDING_STALE_MINUTES` (default 10; never shorter than the creation budget +
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
  - Without a tracking number: the carrier is not called → `dismissed` with `dismissed_by` / `dismissed_at`.
  - With a tracking number the shipment may really exist, so the carrier is asked to cancel it first (three steps, no
    DN lock while calling the carrier). Only when the carrier cancels it or answers already cancelled / not found:
    `unknown` → `dismissed` (`cancel_transaction_id` recorded), `cancelling` → `cancelled` (label voided, tracking
    number cleared). Otherwise → 409 `16094` `details {unresolved, carrier, tracking_number, result:
    refused | unclear, http_status, transaction_id, errors, alerts, maybe_processed}`; cancel it in the carrier's
    portal first. The integration of the carrier that created it is not usable → 409 `16072` (`details.blockers`).
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
for an active shipment, a `cancelling` one (retry) or an `unknown` record with a tracking number. The plugin cancels
the whole shipment; the ship-from country is the `sender_country` stored when the shipment was created (records from
before that column existed derive it the same way as creation). It uses the integration of **the carrier that created
the shipment** (the record's `carrier_id`): the plugin must still be installed and its settings complete, but the
integration may be turned off; otherwise → 409 `16072` (`details.blockers`). Like creating, in **three steps,
without the DN row lock or an open transaction while waiting for the carrier** (limited to
`CARRIER_CREATE_BUDGET_SECONDS`):

1. lock the DN, mark the shipment `cancelling` (`reason: cancel_in_progress`) and commit — from now on the DN cannot be
   shipped and the tracking number cannot be changed (409 `16079`); another cancel request → 409 `16079`;
2. call the carrier;
3. lock the DN and save the result:
   - cancelled (the carrier confirms, or answers already cancelled / not found) → `cancelled`, the tracking number on
     the delivery task is cleared (compared ignoring spaces / hyphens), the label is voided
     (`void_reason: shipment_cancelled`); the CI / PL print no tracking number and are left as they are. An `unknown`
     record becomes `cancelled` as well;
   - the carrier refused (4xx, or an explicit "not cancelled") or the request was not sent → back to `active` (an
     `unknown` record stays `unknown`), 502 `16073` / 504 `16074` with the reason;
   - unclear (read timeout, connection lost, 5xx, or a response that does not say whether it was cancelled) → the
     record stays `cancelling` with the reason (`timeout` / `server_error` / `bad_response` / `connection_error`),
     502 `16073` / 504 `16074` with `details.maybe_processed: true` and `details.unresolved`. Shipping stays blocked;
     cancel again (or dismiss after checking the carrier's portal) until the carrier gives a clear answer.

If saving step 3 fails or the worker is killed, the record stays `cancelling` (`reason: save_failed` /
`interrupted`; a `cancel_in_progress` older than `CARRIER_PENDING_STALE_MINUTES` is reported as `stale`) and can be
cancelled again right away.

If the carrier answers that the shipment is already cancelled / not found, it is treated as cancelled
(`reason: already_cancelled`) — e.g. the previous cancellation succeeded at the carrier but saving it in WMS failed;
cancelling again settles it. Recognising that answer is up to the plugin (FedEx: see *FedEx plugin*).

### Writing a carrier plugin

A carrier is a subclass of `CarrierAdapter` (`warehouse/carrier/integrations/base.py`), placed in
`warehouse/carrier/integrations/<code>/` or in a separate Python package; the built-in FedEx plugin
(`integrations/fedex/`) is a working example. Division of work:

- **WMS** (`warehouse/dn/carrier_services.py`, carrier-agnostic): generic preconditions, the record state machine,
  unclear results / compensation / dismissal, time budget, issuing the CI / PL before creating, writing the tracking
  number to the delivery task, storing the label, and saving / encrypting / validating the integration settings.
- **The plugin**: declares its settings; checks whether a shipment can be handed to this carrier; builds requests,
  calls the API (including authentication), parses responses; classifies failures as `CarrierError`; builds the label
  file; cancels; tests the connection.

Class attributes:

| Attribute | Meaning |
|-----------|---------|
| `code` | Plugin code (required, lower case, ≤ 30 characters), stored in `carrier_integrations.adapter` and in the record's `carrier` |
| `name` | Display name (required) |
| `portal_name` / `portal_url` | The carrier's shipping portal, where operators check / cancel unclear shipments |
| `fields` | Settings, a tuple of `setting(...)` |
| `label_formats` | Supported label formats, a subset of `('A4', 'THERMAL')`; both by default |
| `max_packages` | Most packages per shipment (more → `TOO_MANY_PACKAGES`); `None` = no limit |
| `scopes` | Shipments it carries: `('domestic', 'international')` by default; FedEx `('international',)` (otherwise `SCOPE_NOT_SUPPORTED`) |
| `supports_waybill` | Can create / cancel shipments and labels (default `True`; a plugin only for pickups / tracking sets `False`) |
| `supports_customs` | Can submit customs data (`commodities` / `invoice` / `documents`), required for shipments that need customs (default `False`; FedEx `True`) |
| `supports_pickup` / `supports_tracking` | Pickup booking / tracking (see *Courier pickups* / *Tracking*) |
| `tracks_any_number` | Can track numbers it did not create (manual / imported waybills are tracked too; FedEx `True`) |
| `tracking_url_template` | Public tracking page with `{tracking_number}`, used when the carrier has no `tracking_url_template` of its own |
| `service_options` | Shipping service options the DN may carry (`({key, kind: select / date / money / text, label, values?, default?, help?}, …)`, empty by default); WMS validates them and passes `shipment['options']` |

Settings — `setting(key, kind, *, label, required=False, default=None, options=None, help=None, group='account',
placeholder=None)`:

- `kind`: `text` / `secret` (stored encrypted, never returned) / `select` / `boolean`.
- `label` / `help` are given per locale, `{'en-US': …, 'zh-CN': …, 'ja-JP': …}`; the UI uses the current language and
  falls back to `en-US`. `select` options are built with `option(value, en, zh=None, ja=None)`; `group` is `account` /
  `shipping` / `label` (form sections).
- The instance has `self.settings` (non-secret, empty values replaced by `default`) and `self.secrets`. On save a
  `select` value must be one of its options and a `boolean` must be a boolean, otherwise 400 `16124`; missing required
  values → `CARRIER_NOT_CONFIGURED`; for further checks override `setting_problems()` (English sentences →
  `CARRIER_CONFIG_INVALID`).
- Conventional keys: `default_label_format` (label format when the request gives none) and `service_type` (stored on
  the record; `service_name()` gives the display name).

Methods (`shipment` is the data WMS hands over, plain data only: `reference`, `ship_date`, `route`, `shipper`,
`recipient`, `packages[]`, `commodities[]`, `invoice`, `documents`, `options` — the fields are listed in the
`CarrierAdapter` docstring; `commodities` / `invoice` / `documents` are `None` unless `route.requires_customs`;
`shipper` / `recipient` also carry `local` (structured address) and `latin`):

| Method | Contract |
|--------|----------|
| `check_shipment(shipment)` → `[{code, message, field?}]` | Problems with handing this shipment to the carrier (address does not fit, incoterm vs. duties payer, …), added to the blockers. Runs on every `GET` — no network calls. `shipper` / `recipient` may be `None` (another blocker already covers it); `ship_date` / `documents` are not set yet |
| `label_options(label_format)` → `{label_format, image_type, stock_type, page_size}` | Label format → file format (`PDF` / `PNG` / `ZPLII` / `EPL2`) and stock; stored on the record and passed back to `create_shipment` / `label_archive` |
| `documents_enabled()` → `bool` | When true, WMS puts the current CI into `shipment.documents = {commercial_invoice: {content, file_name}}` and calls `upload_documents(shipment, *, deadline)` → document id before creating; the id is passed to `create_shipment` as `document_ref` |
| `create_shipment(shipment, label, *, deadline, document_ref=None)` → `{tracking_number, package_tracking_numbers, service_type, ship_date, net_charge, currency, documents, transaction_id, alerts}` | Creates the shipment; `net_charge` is a `Decimal` or `None`; `documents` goes to `label_archive` unchanged |
| `label_archive(documents, label)` → `{content, extension, parts}` | One label file; `extension` `pdf` is downloaded inline, anything else as attachment; `parts` is stored as `shipment.label_parts`. An exception is handled like a save failure (compensation) |
| `cancel_shipment(tracking_number, *, sender_country, deadline)` → `{result, reason, problem, transaction_id, alerts}` | Cancels the whole shipment. `result`: `confirmed` (also for "already cancelled / not found", with `reason: already_cancelled`) / `refused` (explicitly not cancelled, `problem` says why) / `unclear` (answered, but it is not clear whether it was cancelled; `reason: bad_response`). A failed request raises `CarrierError` |
| `test_connection(*, deadline=None)` → `dict` | Authenticates once with the current settings (no shipment); the result is merged into `{ok: true, message}`; failure raises `CarrierError` |

Every failure raises `CarrierError(message, *, step, status_code, errors=[{code, message}], transaction_id, timeout,
maybe_processed, budget_exhausted)`, and WMS acts on the flags:

- `maybe_processed=True`: the carrier may have processed the request (read timeout, connection lost after connecting,
  5xx, unreadable response) → creating records `unknown`, cancelling stays `cancelling`. Otherwise the failure is
  definite: `step='create'` with a `status_code` (4xx) → `failed` (`rejected`), anything else (token failure,
  connection not established) → `failed` (`not_sent`); cancelling goes back to `active`.
- `timeout=True` → 504 `16074`, otherwise 502 `16073`; `status_code` 401 / 403 → `details.permission_denied`.
- HTTP 200 without a readable tracking number → raise `CarrierResponseError(message, tracking_number=…,
  transaction_id=…)`: with a tracking number WMS cancels that shipment (compensation), without one the record becomes
  `unknown` (`bad_response`).
- Never put credentials into exception messages or logs.

Before every HTTP request call `request_timeout(deadline, carrier=…, what=…, step=…)` for the `(connect, read)`
timeouts: it reads `CARRIER_CONNECT_TIMEOUT_SECONDS` / `CARRIER_TIMEOUT_SECONDS`, shrinks them to the time left before
`deadline`, and with less than 5 seconds left raises `CarrierError(budget_exhausted=True)` (the request is not sent).

Registration: a built-in plugin is added to `_BUILTIN` in `warehouse/carrier/integrations/__init__.py`
(`'warehouse.carrier.integrations.<code>:<ClassName>'`); a separate package declares an entry point in its
`pyproject.toml`, is installed into the WMS virtual environment, and WMS is restarted:

```toml
[project.entry-points."goodsmart_wms.carrier_integrations"]
dhl = "my_package.dhl:DhlAdapter"
```

The registry is loaded once per process, on first use. Anything that is not a `CarrierAdapter` subclass with `code` and
`name`, or that fails to load, is logged and skipped; on a duplicate `code` the built-in plugin wins.

### FedEx plugin

`warehouse/carrier/integrations/fedex/`: `adapter.py` (settings, checks, reading the cancel result), `client.py` (HTTP,
OAuth), `shipment.py` (request building / response parsing / label archive, pure functions). It uses the Ship API
(create / cancel), the Trade Documents Upload API (ETD) and OAuth (client credentials). The carrier code does not have
to be `fedex`: whichever carrier record has the FedEx integration is used.

| Setting | Kind | Default | Description |
|---------|------|---------|-------------|
| `environment` | select, required | `sandbox` | `sandbox` (`https://apis-sandbox.fedex.com`, nothing is shipped or billed) / `production` (`https://apis.fedex.com`); the Trade Documents Upload host follows it |
| `account_number` | text, required | | FedEx account; shipping charges are billed to it |
| `api_key` / `secret_key` | secret, required | | Credentials of the FedEx developer project |
| `service_type` | select | `INTERNATIONAL_ECONOMY` | Also `INTERNATIONAL_PRIORITY`, `FEDEX_INTERNATIONAL_PRIORITY`, `FEDEX_INTERNATIONAL_PRIORITY_EXPRESS`, `FEDEX_INTERNATIONAL_CONNECT_PLUS` |
| `pickup_type` | select | `CONTACT_FEDEX_TO_SCHEDULE` | Book each pickup in WMS (see “Courier pickups”); also `USE_SCHEDULED_PICKUP` (regular scheduled pickup), `DROPOFF_AT_FEDEX_LOCATION` |
| `duties_payment_type` | select | `RECIPIENT` | `SENDER` = duties billed to the account (required for incoterm DDP) |
| `etd_enabled` | boolean | `false` | Electronic trade documents (ETD): the CI is uploaded to FedEx before the shipment is created and referenced as `ELECTRONIC_TRADE_DOCUMENTS`; the account must have ETD enabled |
| `default_label_format` | select | `A4` | Label format when the request does not give one |
| `thermal_page_size` | select | `100x150` | Page of the `THERMAL` PDF archive (mm): `100x150`, or `101.6x152.4` for real 4 × 6 in stock (no scaling) |
| `thermal_image_type` | select | `PDF` | File format of `THERMAL` labels: `PDF` / `ZPLII` (e.g. Zebra) / `EPL2` |

| `label_format` | Printer | Image / stock | Pages |
|----------------|---------|---------------|-------|
| `A4` | Laser printer, plain A4 / Letter paper | `PDF` / `PAPER_85X11_TOP_HALF_LABEL` | Letter page, label on the top half, folding instructions below |
| `THERMAL` | 4-inch label printer used through its driver | `thermal_image_type` / `STOCK_4X6` | 100 × 150 mm pages (FedEx's 4 × 6 in pages scaled to 98.4%) |

Do not use `PAPER_4X6` for label printers: FedEx returns a Letter page with the label in the top-left corner (which is
why the stock is not a setting).

FedEx's 4 × 6 in (101.6 × 152.4 mm) labels run almost to the page edge (0–0.7 mm), so printing them at actual size on
100 × 150 mm label stock cuts the edges. The `THERMAL` PDF archive therefore scales every page down uniformly (never up)
and centers it on a `thermal_page_size` page (default `100x150`); print it at actual size on a paper whose printable
area is 100 × 150 mm. Pages that would need more than 10% shrinking (e.g. Letter-size documents) are kept as they are;
ZPLII / EPL2 are not touched. For real 4 × 6 in stock choose `101.6x152.4` (no scaling).

- Checks (blockers): at most 30 packages (`TOO_MANY_PACKAGES`); incoterm DDP while `duties_payment_type` is not
  `SENDER` (`INCOTERM_DUTIES_MISMATCH`, `field: incoterm`); ship-from or consignee address that does not fit the
  FedEx format (`SHIPPER_ADDRESS_INVALID`, `field: address_en` / `RECIPIENT_ADDRESS_INVALID`, `field: consignee`).
- Address rules: FedEx accepts at most 3 street lines of 35 characters and a city of 35 characters; an address that
  does not fit is blocked, not truncated. The English address string is split at commas / line breaks — the last part
  (after removing the country name and the postal code) is the city, the other parts become one street line each
  (joined and re-wrapped when there are more than 3 lines). E.g. `4-5-6 Sample-cho, Chuo-ku, Osaka 600-0000, JAPAN` →
  street lines `4-5-6 Sample-cho` / `Chuo-ku`, city `Osaka`, postal code `6000000`. The postal code is the warehouse /
  company `zip_code` (for Japan it is also recognised in the address and sent as 7 digits). A consignee without a
  postal code is sent with an empty `postalCode`; US / CA / PR require a 2-letter state code.
- Request: one package line per package (gross weight kg rounded up to 2 decimals, dimensions cm rounded up; order
  number and invoice number as label references); transportation billed to the account, duties per
  `duties_payment_type`; recipient tax ID types mapped to FedEx `tinType`: EORI → `BUSINESS_UNION`, PCCC / CPF →
  `PERSONAL_NATIONAL`, others → `BUSINESS_NATIONAL`; phone numbers reduced to digits.
- The label archive holds **every** document of the response (package labels → auxiliary documents → others; for
  international Express the "FEDEX AWB COPY" pages come inside the first package's label, nothing extra has to be
  requested): PDF / PNG are merged into one PDF ([pypdf](https://pypi.org/project/pypdf/), BSD), ZPLII / EPL2 are
  concatenated raw printer commands (`.zpl` / `.epl`, `application/octet-stream`). `label_parts` lists each document
  (source, package, content type, doc type, pages, archived).
- The OAuth token (`/oauth/token`, client credentials) is cached in the process per API host + API key until 60 s
  before it expires; a business request answered with 401 gets a fresh token and is retried once. Credentials are
  never logged or returned. *Test connection* forces a new token (the result includes `environment`).
- Cancelling: `PUT /ship/v1/shipments/cancel` (`DELETE_ALL_PACKAGES`). On HTTP 200, `cancelledShipment: true` →
  cancelled, `false` → refused, missing → unclear.
- "Already cancelled / not found": the public FedEx Ship API documentation lists no dedicated error codes for these
  cases and this project has not verified them in the sandbox, so only the error **code** is checked (not the
  localized message): a code containing `ALREADY` together with `CANCEL` / `DELETE`, or `TRACKING` / `SHIPMENT`
  together with `NOT FOUND` / `NOT EXIST`, plus the legacy Web Services code `8159` ("Shipment Delete was requested for
  a tracking number already in a deleted state"). Matches are logged with the raw code; add codes seen in production
  to `ALREADY_CANCELLED_CODES` in `fedex/adapter.py`.

#### Sandbox smoke test and label certification

`scripts/fedex_sandbox_smoke.py` drives `FedexAdapter` directly (the same code WMS uses, no database) and creates test
shipments in the FedEx **sandbox** for each label format and package count, saves the labels as
`<service>_<n>pkg_<format>.<ext>` and cancels the shipments (`--keep` leaves them). It only talks to the sandbox. Its
credentials are for the script only and come from the environment: `FEDEX_API_KEY` / `FEDEX_SECRET_KEY` /
`FEDEX_ACCOUNT_NUMBER`, optionally `FEDEX_SERVICE_TYPE` / `FEDEX_PICKUP_TYPE` / `FEDEX_DUTIES_PAYMENT_TYPE` /
`FEDEX_LABEL_THERMAL_IMAGE_TYPE` / `FEDEX_LABEL_THERMAL_PAGE_SIZE` (`--env-file` reads just these from a .env file).
Shipper and recipient are fictitious unless given as JSON files (same fields as the exporter profile / customs
consignee):

```bash
python scripts/fedex_sandbox_smoke.py [--label-format A4|THERMAL|both] [--packages 1,2] [--to US|DE|HK] \
    [--shipper-json shipper.json] [--recipient-json recipient.json] [--etd] [--out ./fedex-sandbox-labels] \
    [--env-file .env] [--keep]
```

Before production use, FedEx requires label certification: print the sandbox labels of the services you use,
scan them and send them with the label cover sheet to FedEx (see developer.fedex.com → Certification). After
approval set `environment` to `production` in the carrier's FedEx integration and enter the production credentials
(*Test connection* first).

### Courier pickups

Without a regular scheduled pickup, a courier pickup has to be booked for each batch of shipments (FedEx: shipments are
created with `pickupType` `CONTACT_FEDEX_TO_SCHEDULE`, the default of the `pickup_type` setting, and pickups use the
FedEx Pickup Request API — enable it in the FedEx developer project). Pickups are a plugin capability
(`CarrierAdapter.supports_pickup`; the built-in FedEx plugin has it) and use the integration of the carrier they are
booked for. They are booked per current warehouse (`X-WAREHOUSE-ID`, required), carrier and date; one pickup covers
several shipments of that carrier, and its package count / total weight are the totals of their packages
(`dn_packages` gross weight). Records live in `carrier_pickups` (`carrier` = plugin code, `carrier_id` = carrier);
`dn_carrier_shipments.pickup_id` points to the pickup a shipment belongs to (`shipments` keeps a snapshot).

| Endpoint | Description |
|----------|-------------|
| `GET /warehouse/dn/carrier-pickups?carrier_id=` | Carriers of the company whose `pickup_method` is `api` or `manual` (`carriers`, default: the first one), `method`, shipments awaiting pickup (that carrier, `active`, any source, DN not shipped; `can_schedule`), its pickups of the last 14 days (every unclear one too), `blockers`, pickup location, `can_create` (API booking) / `can_record` (manual record) |
| `GET /warehouse/dn/carrier-pickups/availability?carrier_id=` | Query only: pickup dates and times the carrier offers (`options[{pickup_date, cutoff_time, access_minutes, ready_times, latest_times, default_ready_time, default_latest_time}]`) |
| `POST /warehouse/dn/carrier-pickups` | `{carrier_id?, shipment_ids, pickup_date, ready_time, close_time, remarks?, notification_email?}` → 201 `{pickup, alerts, message}` |
| `POST /warehouse/dn/carrier-pickups/manual` | Record a pickup booked by phone / website (no carrier call), see *Shipping Flow* |
| `POST /warehouse/dn/carrier-pickups/<id>/cancel` | Cancel a scheduled pickup whose date has not passed (a manual one only in WMS) |
| `POST /warehouse/dn/carrier-pickups/<id>/dismiss` | `{confirm: true}`: dismiss a pickup with an unclear result after checking with the carrier |

- Permissions as for carrier shipments: read `dn_read` / `packing_read` / `delivery_read`, write `packing_edit` /
  `delivery_edit`.
- **Pickup location**: the warehouse English address (else the company's), the warehouse contact (else the company
  export contact), the warehouse phone (else the company's) and the company English name; the plugin converts it to the
  carrier's format (FedEx: same address rules as the ship-from address, phone 8-13 digits). Blockers (409 `16130`):
  `PICKUP_METHOD_NOT_API` (booking through the integration needs `pickup_method: api`) /
  `PICKUP_CARRIER_MISSING` / `CARRIER_NOT_INTEGRATED` / `PICKUP_NOT_SUPPORTED` / the integration problems of carrier
  shipments (`CARRIER_INTEGRATION_DISABLED`, `CARRIER_NOT_CONFIGURED`, …) / `PICKUP_ADDRESS_MISSING` /
  `PICKUP_ADDRESS_INVALID` / `PICKUP_PHONE_INVALID`.
- **Times** are local to the warehouse (its time zone, see *Time Zones*). FedEx: `readyDateTimestamp` carries
  the UTC offset (`2026-10-22T12:00:00+09:00`, as in FedEx's Japanese example) and `customerCloseTime` is local
  `HH:MM:SS`; `pickupDateType` is `SAME_DAY` for today, otherwise `FUTURE_DAY`. The carrier's cutoff time and access time
  limit the choices in the UI; the backend only checks the order of the times, the date range (today to 30 days ahead)
  and the remarks length of the plugin (`max_remarks`), the carrier checks the rest.
- **Held shipments** cannot be scheduled again (409 `16132`, `reason: ALREADY_SCHEDULED`): held by a `pending` / `unknown`
  pickup or a `scheduled` one whose date has not passed. Once the date has passed, shipments whose DN is still not shipped
  can be scheduled again (the courier did not come / the delivery was not completed). Shipments of another carrier:
  `reason: OTHER_CARRIER`.
- **Scheduling** runs in three steps like creating a shipment, without holding row locks while the carrier is called: lock
  the shipments → insert a `pending` record holding them and commit → call the carrier (within
  `CARRIER_CREATE_BUDGET_SECONDS`) → `scheduled` (`confirmation_code` / `location_code`); refused / not sent → `failed`,
  shipments released (502 `16073` / 504 `16074`, `details.pickup`); unclear (timeout, 5xx, no confirmation code) →
  `unknown`, shipments stay held until the operator checks the confirmation email / calls the carrier and dismisses it.
  `pending` for longer than `CARRIER_PENDING_STALE_MINUTES` counts as `unknown` (`stale`).
- **Cancelling**: `scheduled` and the date has not passed (otherwise 409 `16133`), with the integration of the carrier that
  booked it (also when it was turned off; removed → 409 `16130`). Cancelled or reported as already cancelled (FedEx: same
  rule as for shipments) → `cancelled`, shipments released; refused → stays `scheduled` (`reason: cancel_refused`);
  unclear → stays `scheduled` (`reason: cancel_unclear`), retry.
- The FedEx confirmation email (`notification_email`) uses `en_US`; `shipment.pickup` of the carrier-shipment status
  gives the pickup summary of that shipment.

### Tracking (shipped on pickup, signed on delivery)

WMS creates the shipments, so WMS tracks them (plugin capability `CarrierAdapter.supports_tracking` + `track()`; the
built-in FedEx plugin uses the Track API, which must be enabled in the FedEx developer project). The logic is
carrier-agnostic, in `warehouse/dn/tracking_services.py`:

- **What is tracked**: `active` waybills with a tracking number whose carrier has `tracking_method: api` — created by
  the plugin, or entered by hand / imported when the plugin `tracks_any_number` (FedEx) — created at most
  `CARRIER_TRACKING_MAX_DAYS` days ago, whose DN is `packed` / `delivered`, or completed (signed by hand) while tracking is not yet delivered (display
  only). The scheduler runs a round every `CARRIER_TRACKING_INTERVAL_MINUTES` minutes (least recently checked first;
  grouped by carrier and batched by the plugin's `track_batch_size` - a failed batch only affects that batch; the round is
  limited to `CARRIER_TRACKING_BUDGET_SECONDS`, the rest waits for the next round) without holding row locks while calling
  the carrier. A turned-off integration is still tracked; a shipment created by another plugin than the carrier's current
  one is not. Results whose pickup / delivery is more than 12 hours before the shipment was created (manual / imported
  waybills: before the DN was created) — a reused tracking number — are ignored and recorded as `error`
  (`TRACKING.OLD_SHIPMENT`). Automatic shipping / signing record `completed_source` / `signed_source: carrier`.
- **Automatic shipping**: once the carrier has the parcel (`picked_up_at`, or status `in_transit` / `out_for_delivery` /
  `delivered`) and the DN is still `packed`, the delivery is completed in the name of the user who created the shipment
  (a `pending` task is started first; the same checks as a manual *complete* - no document check, an expected shipping
  date after the pickup date still blocks) and `dn.delivered` is pushed. The shipping time is the carrier's pickup time
  (in the server's local time): the task's completion time, the actual shipping date, the DN's `delivered_at` and
  `shipped_at` of `dn.delivered` all use it, not the time the result was fetched.
- **Automatic signing**: tracking `delivered` and DN `delivered` → the delivery task is signed (signed time = the
  carrier's delivery time in the server's local time, like the other WMS timestamps, never before the shipping time), the
  DN completes (`completed_at` = the signed time) and `dn.completed` (with `tracking_number`, `shipped_at`,
  `signed_at`) is pushed.
- When a step cannot be done (e.g. 16061 expected shipping date later) the reason is kept in the
  shipment's `tracking_problem` (shown on the delivery page) and retried next round; it is cleared once done. Manual
  completion / signing still works; whichever comes first wins.
- **Results** are stored in `dn_carrier_shipments.tracking_*` and returned as `shipment.tracking`:
  `{status, code, status_text, picked_up_at, delivered_at, estimated_delivery, events[], error, synced_at, problem}`.
  `status` is normalized to `label_created` / `in_transit` / `out_for_delivery` / `delivered` / `exception` / `unknown`
  (`code` is the carrier's own code such as FedEx `OC` / `PU` / `DL`; unrecognized codes are `unknown` and never trigger
  automatic steps). A failed lookup only records `error` and keeps the last result.
- Changes of status / scans push `dn.tracking_updated` (`{dn_id, order_number, carrier, tracking_number, tracking{…}}`,
  `dedupe_key = dn:<id>`: an unsent older update is superseded, only the latest is delivered). Automatic steps run before
  the tracking update is pushed.
- When a manual refresh and the scheduler overlap, a result saved by someone else after this round started is not
  overwritten; a cancelled / renumbered shipment is not written.
- `POST /warehouse/dn/<id>/carrier-shipment/track` (`delivery_edit`: it may complete / sign the delivery) checks now
  (about 40 s limit) and applies the result the same way; it returns the shipment status plus
  `tracking_sync {tracked, updated, shipped, signed, problems, errors, skipped}`; no trackable shipment → 409 `16134`. `integration.tracks_shipments` tells whether the carrier can be tracked.

### Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `CARRIER_CONNECT_TIMEOUT_SECONDS` / `CARRIER_TIMEOUT_SECONDS` | `5` / `30` | Connect / read timeout of one request (seconds); shrunk to the remaining budget when creating / cancelling |
| `CARRIER_CREATE_BUDGET_SECONDS` | `90` | Time limit of the whole creation (token + document upload + shipment; at least 10); keep it below the gunicorn / nginx timeout (e.g. 120 s). Also used for cancelling |
| `CARRIER_PENDING_STALE_MINUTES` | `10` | A `pending` record older than this is reported as unclear (`stale`); never shorter than the creation budget + 75 s |
| `CARRIER_TRACKING_INTERVAL_MINUTES` | `30` | Interval of the tracking job (minutes); `0` = off |
| `CARRIER_TRACKING_MAX_DAYS` | `60` | Only shipments created at most this many days ago are tracked |
| `CARRIER_TRACKING_BUDGET_SECONDS` | `120` | Time limit of one tracking round (seconds) |
| `CARRIER_AUTO_SIGN_INTERVAL_MINUTES` | `60` | Interval of the automatic-signing job (`carriers.auto_sign_days`, see *Shipping Flow*); `0` = off |

Accounts, keys, services etc. are stored per carrier in the integration settings, not in environment variables; the
keys are encrypted with `SETTINGS_ENCRYPTION_KEY` (mandatory in production, see *Quick Start*).

## Time Zones

The WMS does not assume one time zone for all warehouses (design: `doc/timezone-utc.md`).

- **Storage**: points in time are stored as `timestamptz` (UTC). Models use `UTCDateTime` (`extensions/db.py`), so Python
  code always sees timezone-aware UTC `datetime`s; the current time is `warehouse/common/clock.now()`. Date-only fields
  (expected / actual shipping date, invoice date, pickup date, …) stay `date` and mean the **local date of the warehouse**.
- **Warehouse time zone**: `warehouses.timezone` — an IANA name such as `Asia/Tokyo` or `America/Los_Angeles`, optional,
  set on warehouse create / update (`"timezone": null` or `""` = use the default; invalid → 400 `14022`, `field:
  timezone`). Effective time zone = the warehouse's → `WMS_DEFAULT_TIMEZONE` → `WMS_TIMEZONE` → `UTC`. Business dates use
  it: actual shipping date, invoice date, carrier ship date and file export date, pickup "today", "expected arrival
  date has passed", automatic signing after N days (local calendar days). Input times without an offset (`shipped_at`,
  `signed_at`, operation times, `payment_time`, `scheduled_date`, list filters …) are read in the time zone of the
  document's warehouse; a date alone means local midnight (PDA signing).
- **API output**: every time field is ISO 8601 in UTC with its offset (`2026-10-25T01:02:03+00:00`); date fields stay
  `YYYY-MM-DD`. Warehouse objects — also when nested in documents, tasks and staff — carry `timezone` (effective); the
  warehouse list / detail also carry `timezone_configured` (the warehouse's own setting; `null`, and omitted in list
  rows, when it uses the default). DN and ASN detail and their flow endpoints (`GET /dn/<id>/shipping-flow`,
  `GET /asn/<id>/flow`) carry a top-level `timezone` (the document's warehouse); list rows keep `warehouse_id`.
  Courier pickups (`GET /dn/carrier-pickups`, availability) return `today` / `timezone` of the warehouse.
- **Webhooks**: times in `dn.*` payloads (`started_at`, `picked_at`, `packed_at`, `shipped_at`, `signed_at`) are the same
  instant written with the offset of the DN warehouse (`2026-10-23T13:03:00+09:00`); `goods.spec_updated` `changed_at`
  uses the default time zone. Receivers parse them as before.
- Monthly statistics take "this month" in the filtered warehouse's (else the default) time zone; the database groups
  by month in its session time zone (`WMS_LEGACY_TIMEZONE` on PostgreSQL, UTC on SQLite).

| Variable | Default | Description |
|----------|---------|-------------|
| `WMS_TIMEZONE` | (unset) | Wall clock of data written by older versions, and the default time zone. Any IANA name (an invalid name stops the start). Also sets the process `TZ` (log times, `SNAPSHOT_HOUR` of the inventory snapshot) |
| `WMS_DEFAULT_TIMEZONE` | `WMS_TIMEZONE`, else `UTC` | Time zone of warehouses without their own. The old `DOCUMENT_TIMEZONE` is still read when this is unset (its `Asia/Tokyo` default is gone) |
| `WMS_LEGACY_TIMEZONE` | `WMS_TIMEZONE`, else `UTC` | Wall clock of the values in `timestamp` columns that are not converted yet. PostgreSQL sessions use it as their time zone, so the code works before and after the conversion |

### Upgrading an existing installation

Older versions stored the server's wall clock in `timestamp without time zone` columns. The new code reads them in
`WMS_LEGACY_TIMEZONE`, so it works before and after the one-time conversion to `timestamptz`:

1. Before starting the new version set `WMS_LEGACY_TIMEZONE` (or `WMS_TIMEZONE`) to the time zone the old values were
   written in — the server / `TZ` of the old version, e.g. `Asia/Shanghai`. Optionally set `WMS_DEFAULT_TIMEZONE` and the
   time zone of each warehouse.
2. Deploy, `flask db upgrade` (adds `warehouses.timezone`), restart.
3. Back up the database (`pg_dump -Fc`), preferably stop the application, then convert:

   ```bash
   flask wms convert-utc --legacy-tz Asia/Shanghai           # dry run: columns and statements
   flask wms convert-utc --legacy-tz Asia/Shanghai --apply   # one transaction; asks for confirmation (--yes skips)
   ```

   Every `timestamp without time zone` column in `public` (except `alembic_version`) becomes `timestamptz` via
   `USING col AT TIME ZONE '<legacy>'`, one `ALTER TABLE` per table; a row in `wms_timezone_migrations` prevents a
   second run. Any error rolls the whole conversion back.
4. Start the application again.

New installations (tables created from the models) already use `timestamptz` and need no conversion; SQLite stores UTC.
New migrations must declare time columns as `sa.DateTime(timezone=True)` (autogenerate renders `UTCDateTime` that way).

Production runs the conversion with the manual workflow **Migrate WMS Time to UTC** (`.github/workflows/migrate-utc.yml`
→ `scripts/manual/migrate_utc.py`, same `deploy-backend` concurrency group as deploys). Without `apply` it only checks
(server commit, `/health`, migrations at the code head, the application's legacy time zone = `.env`, the column list) and
prints the statements; with `apply` it stops `wms-api`, writes a verified `pg_dump -Fc` backup to
`/var/www/wms-backups` (0600, SHA-256 in the log), converts in one transaction, restarts and probes `/health`. Before the
commit a failure changes nothing; after the commit it is never undone automatically (the code works on the converted
schema) — check the workflow log, `pm2 logs wms-api` and the backup.

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

Business codes of the export-document features: `14019` export profile text too long, `14020` invalid country code, `16063` customs structure invalid, `16064` customs line goods code not in the DN / duplicated, `16065` shipped — customs data / packages / documents locked (409), `16066` invalid packages, `16067` packages cannot be edited in the current DN status (409), `16068` documents cannot be issued yet (409, `details.problems`), `16070` not an export DN (409), `16071` document not found (404), `16117` documents cannot be laid out — a text block (usually the consignee) is too long for one page; shorten it (409, `details {document, reason: LAYOUT_OVERFLOW}`). Carrier shipments (automatic): `16072` preconditions for creating / cancelling not met (409, `details.blockers`), `16073` the carrier returned an error or an unclear result (502, `details.errors` / `transaction_id` / `maybe_processed`), `16074` the carrier timed out or the time budget ran out (504, `details.budget_exhausted`), `16075` no carrier shipment to cancel / dismiss (409, `details.unresolved`), `16076` packages / customs data cannot be changed, or documents with different data cannot be issued, while a carrier shipment is pending, active, unclear or being cancelled (409, `details {reason: CARRIER_SHIPMENT_OPEN | DOCUMENT_DATA_CHANGED, status, carrier, carrier_shipment {id, status, tracking_number}}`), `16077` invalid `label_format`, or not supported by the carrier (400), `16078` tracking number / carrier differs from the active carrier shipment, or a delivery task is added / deleted while a carrier shipment exists (409), `16079` a carrier shipment request is pending or its result is unclear, or the shipment is being cancelled / the cancellation is unclear — no new shipment, tracking number or shipping (409, `details.unresolved`), `16093` the shipment / record named in a cancel / dismiss request is not the current one (409, `details {expected, actual}`), `16094` dismissing a record with a tracking number: the carrier did not confirm it is cancelled / not found (409), `16095` the carrier created the shipment but saving it in WMS failed; compensated (502, `details {error, compensation, unresolved, …}`). Carrier integrations: `16124` invalid integration settings (400, `field`: `adapter` / `enabled` / `settings.<key>` / `secrets.<key>`), `16125` the carrier has unfinished automatic shipments — its integration cannot be removed or switched to another plugin (409, `details.shipments`). Courier pickups: `16130` blockers (409, `details.blockers`), `16131` invalid pickup request (400, `field`), `16132` selected shipments cannot be scheduled (409, `details.shipments[{shipment_id, tracking_number, reason}]`), `16133` the pickup cannot be cancelled / dismissed in its current state (409, `details {pickup_id, status, reason, pickup_date}`); tracking: `16134` no active waybill that can be tracked through the API (409); carrier errors / timeouts as for shipments (`16073` / `16074`, `details.action` `pickup` / `pickup_cancel` / `pickup_availability`). No longer used: `16069` (formerly: documents missing or out of date on completion; completion no longer checks documents); the documents no longer print the tracking number or carrier: `16080` (formerly: tracking number on completion differs from the AWB printed on the CI), `16092` (formerly: carrier on completion differs from the one printed on the CI). Delivery tasks: `16090` the DN already has an unfinished delivery task (409, `details {delivery_task_id, status}`), `16091` DN with customs data or waybill records — not the current delivery task (409, `details {delivery_task_id, current_status}`), `16136` `shipped_at` in the future (400, `field: shipped_at`; an invalid one is `16050`). Shipping flow: `16135` invalid carrier method / pickup schedule / tracking page / `auto_sign_days` / file settings (`file_preset`, `file_template.<path>`, `file_account.<key>`), or a method that is not available (400, `field`); file mode: `16137` rows with problems — nothing exported (409, `details.rows[{dn_id, order_number, problems[{column, code, message}]}]`), `16138` file mode not available: invalid request / import file (400) or the carrier is not in file mode / its template is incomplete (409), with `field`; `16139` invalid shipping options (400, `field: options.<key>`); `16076` also with `details.reason: SHIPPING_OPTIONS_LOCKED` / `SHIP_TO_LOCKED`, `16075` also when there is no exported file waybill to discard (`details.waybill`); structured addresses use `14019` (not a string / too long / `zip_code` ≠ `postal_code`) and `14020` (country); `16130` also carries `PICKUP_METHOD_NOT_API`; `16131` manual pickups also use `field` `warehouse_id` / `confirmation_code`; carrier-shipment blockers `SCOPE_NOT_SUPPORTED` / `CUSTOMS_DATA_MISSING` / `CUSTOMS_NOT_SUPPORTED` / `WAYBILL_NOT_SUPPORTED` replace `NOT_EXPORT`. Warehouses: `14022` invalid time zone (not an IANA name, 400, `field: timezone`). Errors may carry a structured `details` object.

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
