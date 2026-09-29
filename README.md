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
- Delivery with tracking number
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
