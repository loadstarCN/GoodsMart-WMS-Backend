# GoodsMart WMS API 服务

GoodsMart WMS（仓库管理系统）API 是一个基于 Flask 构建的多租户仓库管理后端服务。提供完整的 RESTful API 接口，支持入库/出库管理、库存控制、仓库运营及 RBAC 权限管理。

> **客户端支持**:
> - Web 端: https://github.com/loadstarCN/GoodsMart-WMS-Web
> - 开发者可基于 API 自行开发定制客户端

## 许可证

本项目采用 **GNU Affero General Public License v3.0 (AGPL-3.0)** 许可证。

- 可自由使用、修改和分发
- 衍生作品须以相同许可证开源
- 商业使用需单独授权，请联系作者获取商业许可

## 技术栈

- **后端框架**: Flask 3.1.x
- **数据库**: PostgreSQL 13+
- **ORM**: SQLAlchemy + Flask-SQLAlchemy
- **认证**: JWT (Flask-JWT-Extended)
- **授权**: RBAC（基于角色的访问控制）
- **API 文档**: OpenAPI / Swagger (Flask-RESTx)
- **缓存**: Redis
- **对象存储**: 阿里云 OSS
- **定时任务**: Flask CLI + cron

## 前置要求

- Python 3.10+
- PostgreSQL 13+
- Redis 6.x+
- pip

## 快速开始

### 1. 克隆项目

```bash
git clone https://github.com/loadstarCN/GoodsMart-WMS-Backend.git
cd GoodsMart-WMS-Backend
```

### 2. 创建虚拟环境

```bash
python -m venv venv
source venv/bin/activate  # Linux/Mac
# 或
venv\Scripts\activate     # Windows

pip install -r requirements.txt
```

### 3. 配置环境变量

```bash
cp .env.example .env
```

编辑 `.env` 文件：

```env
FLASK_ENV=development
FLASK_DEBUG=True

# 数据库
SQLALCHEMY_DATABASE_URI=postgresql://user:password@localhost:5432/warehouse
SQLALCHEMY_DATABASE_URI_DEV=postgresql://user:password@localhost:5432/warehouse

# JWT
JWT_SECRET_KEY=your_secure_random_key

# Redis
REDIS_URL=redis://localhost:6379/0

# 阿里云 OSS
OSS_ACCESS_KEY_ID=your_key
OSS_ACCESS_KEY_SECRET=your_secret
OSS_ENDPOINT=oss-your-region.aliyuncs.com
OSS_BUCKET_NAME=your-bucket
OSS_HOST=https://your-bucket.oss-your-region.aliyuncs.com
```

### 4. 数据库初始化

```bash
flask db init
flask db migrate -m "Initial migration"
flask db upgrade
```

### 5. 初始化权限数据（首次部署）

```bash
python seed_permissions.py
```

### 6. 启动服务

```bash
python app.py
```

API 服务运行在 http://localhost:5000。

## 项目结构

```
GoodsMart-WMS-Backend/
├── app.py                     # 应用入口
├── config.py                  # 多环境配置
├── seed_permissions.py        # 权限数据初始化脚本
├── extensions/                # Flask 扩展
│   ├── db.py                  # SQLAlchemy 数据库
│   ├── jwt.py                 # JWT 认证
│   ├── redis.py               # Redis 客户端
│   ├── cache.py               # 缓存层
│   ├── oss.py                 # 阿里云 OSS
│   ├── limiter.py             # 限流
│   ├── error.py               # 自定义异常
│   └── transaction.py         # 事务装饰器
├── system/                    # 系统模块
│   ├── user/                  # 用户、角色、权限（RBAC）
│   ├── third_party/           # API Key 管理
│   ├── webhook/               # Webhook 事件队列与推送
│   ├── logs/                  # 请求日志
│   ├── limiter/               # IP 黑白名单
│   └── common/                # 公共工具（分页等）
├── warehouse/                 # 业务模块
│   ├── company/               # 租户/公司管理
│   ├── staff/                 # 员工管理
│   ├── department/            # 部门管理
│   ├── warehouse/             # 仓库管理
│   ├── location/              # 库位管理
│   ├── goods/                 # 商品管理
│   ├── inventory/             # 库存管理
│   ├── inventory_snapshot/    # 库存快照
│   ├── supplier/              # 供应商管理
│   ├── carrier/               # 承运商管理
│   ├── recipient/             # 收货人管理
│   ├── asn/                   # 入库：ASN（预到货通知）
│   ├── sorting/               # 入库：分拣
│   ├── putaway/               # 入库：上架
│   ├── dn/                    # 出库：DN（发货通知）
│   ├── picking/               # 出库：拣货
│   ├── packing/               # 出库：打包
│   ├── delivery/              # 出库：发货
│   ├── payment/               # 出库：代收付款
│   ├── adjustment/            # 库存：库存调整
│   ├── cyclecount/            # 库存：盘点
│   ├── transfer/              # 库存：库存调拨
│   └── removal/               # 库存：库存移除
├── tasks/                     # 定时任务
│   ├── commands.py            # Flask CLI 命令
│   ├── snapshot.py            # 库存快照逻辑
│   └── views.py               # 任务触发 API
├── migrations/                # Alembic 数据库迁移
├── tests/                     # 测试用例
└── doc/                       # SQL 迁移脚本
```

## API 功能模块

### 系统管理
- **用户管理**: 注册、登录、JWT 令牌管理
- **角色与权限**: RBAC 细粒度权限控制（84 项权限）
- **API Key**: 第三方系统集成，支持公司级数据隔离
- **日志**: 请求日志与审计追踪
- **IP 管控**: 黑白名单管理

### 基础数据
- **公司**: 多租户公司管理
- **员工**: 绑定公司的员工管理
- **仓库**: 仓库与库位管理
- **商品**: 商品信息（分类、定价、图片）
- **供应商 / 承运商 / 收货人**: 交易伙伴管理

### 入库（ASN）
- ASN 创建与管理
- 收货
- 质量分拣（实际数量、损坏追踪）
- 上架至库位

### 出库（DN）
- 发货单创建与管理
- 拣货
- 打包
- 发货（含运单号）
- 代收付款管理

### 库存运营
- 实时库存追踪（多阶段：ASN → 已收货 → 已分拣 → 在库 → DN → 已拣货 → 已打包 → 已发货）
- 库存调整（增减及原因追踪）
- 盘点（带审批流程）
- 库位间调拨
- 库存移除
- 库存快照（每日定时）

## Webhook 集成

WMS 通过 Webhook 向外部系统推送事件通知，使用 HMAC-SHA256 签名验证。

### 支持事件

| 事件 | 触发时机 |
|------|----------|
| `asn.received` | ASN 标记为已收货 |
| `asn.completed` | ASN 完成（含实际数量） |
| `dn.in_progress` | DN 开始处理 |
| `dn.delivered` | DN 已发货（含运单号） |
| `dn.completed` | DN 完成 |
| `goods.spec_updated` | 商品重量 / 尺寸 / 原产国变更（按公司广播，需订阅，见下文） |

单据类事件（`asn.*`、`dn.*`）只推给创建该单据的 API Key。`asn.completed` 明细另带商品主数据：
`goods_weight_kg`、`goods_length_mm`、`goods_width_mm`、`goods_height_mm` 与
`goods_origin_country`（ISO 3166-1 alpha-2，`null` = 未录入）。

### 配置

1. 为 API Key 配置 webhook 地址和密钥：

```sql
UPDATE api_keys
SET webhook_url    = 'https://your-system.example.com/webhook',
    webhook_secret = 'your-hmac-secret'
WHERE key = 'your-api-key';
```

2. 配置定时推送（每分钟）：

```bash
* * * * * cd /path/to/project && flask webhook push >> /var/log/wms-webhook.log 2>&1
```

### 推送格式

事件以 JSON 格式 POST 发送，携带以下请求头：
- `X-Webhook-Event`: 事件类型（如 `dn.delivered`）
- `X-Webhook-Signature`: `sha256=<HMAC-SHA256 十六进制摘要>`

推送失败每 30 分钟重试一次，最多 10 次（见 `system/webhook/services.py`）。

### `goods.spec_updated`（按公司订阅）

推给商品所属公司下**所有**「启用、配置了 `webhook_url`、且 `webhook_subscriptions` 含该事件」的 API Key；
未绑定公司的 Key 收不到。通过 API Key 接口订阅（目前只接受 `goods.spec_updated`，其他值 → 400 `14018`）：

```http
PUT /system/third-party/api-keys/<id>
{ "webhook_subscriptions": ["goods.spec_updated"] }
```

新建 / 修改商品时，重量、长、宽、高、原产国任一发生变化，就在同一事务里记录该事件。
payload 是当前值的完整快照（`null` = 未录入）：

```json
{ "goods_code": "4900000000000", "goods_id": 1,
  "goods_weight_kg": 0.235, "goods_length_mm": 120, "goods_width_mm": 80, "goods_height_mm": 45,
  "goods_origin_country": "CN",
  "changed_fields": ["goods_weight_kg", "goods_origin_country"],
  "source": "station", "changed_at": "2026-01-01T10:00:00+09:00" }
```

- `source`：取商品新建 / 修改请求体里的 `spec_source`（`station` / `manual` / `import` / `api`）；
  没传时 API Key 调用为 `api`、登录用户为 `manual`；CSV 导入为 `import`。
- 同一商品对同一 Key 还有待发送的事件时，新的变更覆盖它的 payload，不再新增
  （`dedupe_key = goods:<id>`；`changed_fields` 取并集）。
- 请求头、签名、重试与其他事件相同。

商品 `origin_country` 只接受 ISO 3166-1 alpha-2 代码（自动大写；空串 = 清空；其他值 → 400 `10014`），
没有默认值。`GET /warehouse/goods/?origin_missing=true` 列出未录入原产国的商品。
仓库作业账号可用 `PUT /warehouse/goods/<id>/origin-country`（`{"origin_country": "CN" | "" | null, "spec_source"?: ...}`）
只改原产国，需要 `goods_edit`、`sorting_edit`、`packing_edit` 任一权限，同样触发 `goods.spec_updated`。
CSV 导入支持可选的 `origin_country` 列（已有商品：`append` 只补空白，`override` 只用非空值覆盖）。

## 库存快照

每日库存快照用于历史分析：

```bash
# 手动执行
flask snapshot run

# 定时任务（每天凌晨 2 点）
0 2 * * * cd /path/to/project && flask snapshot run >> /var/log/wms-snapshot.log 2>&1
```

或通过 API 触发：
```http
POST /tasks/task/inventory_snapshot
Authorization: Bearer <token>
```

## 海外件：报关快照、装箱与出口单证

跨境发货时，WMS 为每张 DN 保存报关快照、记录箱子，并生成 **商业发票（Commercial Invoice, CI）** 与
**装箱单（Packing List, PL）** PDF（A4 英文，使用 [reportlab](https://www.reportlab.com/) 生成，BSD 许可）。
带报关快照的 DN 即海外件（DN 列表 / 详情、打包 / 发货任务里嵌套的 DN 上 `is_export: true`）。

### 出口资料（公司 / 仓库）

发货人信息全部来自 WMS 的公司 / 仓库设置，代码里不写任何公司数据。

| 对象 | 字段 |
|------|------|
| 公司（`PUT /warehouse/company/<id>`） | `legal_name_en`、`address_en`、`country_code`（ISO 3166-1 alpha-2，默认 `JP`）、`tax_id_label`、`tax_id`、`export_contact_name`、`export_signatory_name`、`export_signatory_title` |
| 仓库（`PUT /warehouse/warehouse/<id>`） | `address_en`、`country_code`、`contact_name_en`（与公司地址不同时印为 *Ship From*） |

`country_code` 不合法 → 400 `14020`；文本超长 → 400 `14019`。

### 报关快照

`POST /warehouse/dn/` 顶层可带 `customs`（国内件不带）；`PUT /warehouse/dn/<id>/customs` 整体替换
（权限 `dn_edit`，发货后不可改）。结构同英文 README 示例：`invoice_number`、`currency`、`incoterm`、
`export_reason`、`recipient_country`、`recipient_tax_id`、`recipient_tax_id_type`、`freight_charge`、
`insurance_charge`、`declared_value_carriage`、`consignee{...}`、`lines[{goods_code, quantity, unit_value, total_value, description_en, hs_code, jp_export_code, origin_country, quantity_unit}]`。

- 结构错误拒绝：`customs` 非对象、`lines` 非数组、字段类型错误 → 400 `16063`（`details.field`）；
  行的 `goods_code` 不在 DN 明细或重复 → 400 `16064`。
- 内容不全照收，缺什么在 `problems` 里列出。
- `invoice_number` 缺省用 DN 的 `order_number`；发票数量一律取**已打包数量**（金额 = 单价 × 已打包数量，
  已打包 0 的行不上发票）；原产国以商品主数据 `goods.origin_country` 为准，行里的只做记录。
- `freight_charge` 在发票上单列为 *Freight*，并计入 *Total Invoice Value*。
- 运送保险（可选）：`insurance_charge`（非负整数或 `null`）大于 0 时在 *Freight* 下单列 *Insurance* 并计入
  *Total Invoice Value*；`declared_value_carriage`（非负整数或 `null`）是运送申告价额，仓库在承运商系统登记出货时填写，
  报关视图里显示、单证上不印。两个键都可不带（旧请求照旧）；不合法 400 `16063`。任一变化都会作废已签发的单证；
  没有这两个值的快照单证指纹不变。
- `jp_export_code`（可选，9 位日本出口统计品目番号，前 6 位应等于 HS）存入快照并在接口返回，CI / PL 不印（只印 HS）。
- 替换快照时，只有印在单证上的内容变了才作废现有单证。

`GET /warehouse/dn/<id>/customs`（`dn_read` 或 `packing_read`）返回
`{dn_id, is_export, locked, customs, lines[], packages[], totals, exporter, problems[], ready, current_documents[], documents_outdated}`。
`totals = {quantity, goods_value, freight, insurance, invoice_total, package_count, gross_weight_kg, net_weight_kg}`，
`invoice_total = goods_value + freight + insurance`（没投保 `insurance` 为 0）。

- 错误级问题：`NOT_PACKED`、`PACKAGES_MISSING`、`EXPORTER_PROFILE_INCOMPLETE`、`INCOTERM_MISSING`、
  `EXPORT_REASON_MISSING`、`CURRENCY_INVALID`、`RECIPIENT_COUNTRY_MISSING`、`LINE_MISSING`、`HS_CODE_MISSING`
  （去掉 `.`、空格、`-` 后须为 6–10 位数字）、`DESCRIPTION_MISSING`、`DESCRIPTION_NOT_ASCII`、`ORIGIN_MISSING`、`UNIT_VALUE_MISSING`。
- 警告（不拦截）：`RECIPIENT_TAX_ID_MISSING`、`NON_LATIN_TEXT`、`NET_WEIGHT_UNKNOWN`、`GROSS_LT_NET`、
  `JP_EXPORT_CODE_MISMATCH`（`jp_export_code` 非 9 位数字或前 6 位与 HS 不一致）、
  `JP_EXPORT_CODE_MISSING`（JPY 发票合计＝货值＋运费＋保险费超过 200,000 且有行缺 `jp_export_code`）。

### 箱子

`GET / PUT /warehouse/dn/<id>/packages`（读：`dn_read` 或 `packing_read`；写：`packing_edit`），
DN 须为 `picked` 或 `packed`（否则 409 `16067`）。请求体 `{"packages": [{package_no, gross_weight_kg, length_mm, width_mm, height_mm, remark}]}`，
返回 `{packages, voided_documents}`。1–99 箱，编号从 1 连续（缺省自动编）；毛重 0.01–999.999 kg（三位小数）；
长宽高 1–3000 mm 整数；不合法 400 `16066`。已出单证后改箱子 → 现有单证作废（`void_reason: packages_changed`）。

### 单证

| 方法 | 路径 | 权限 |
|------|------|------|
| `POST` | `/warehouse/dn/<id>/customs-documents/issue` | `packing_edit` |
| `GET` | `/warehouse/dn/<id>/customs-documents/?status=issued` | `dn_read` / `packing_read` |
| `GET` | `/warehouse/dn/<id>/customs-documents/<doc_id>/file` | `dn_read` / `packing_read` |

- 出单证条件：DN 为 `packed`、有报关快照、至少 1 箱、出口资料齐、没有错误级问题；否则 409 `16068`
  （`details.problems`）；不是海外单 409 `16070`。
- CI 与 PL 成对签发并存库（`dn_documents`），生成即定稿。数据未变再次签发 → 返回现有版本（200）；
  数据变了（含运单号 AWB）→ 旧版作废、版本 +1（201）。
- `file` 以 `application/pdf` inline 返回，响应头 `X-Content-SHA256`。
- 发货任务已有运单号时 CI 印 AWB No.。发货前用 `PUT /warehouse/delivery/<task_id>/tracking`
  `{"tracking_number": "...", "carrier_id": 1}` 保存（权限 `delivery_edit`，发货后 409 `16065`），再重新签发；
  报关视图里 `documents_outdated: true` 表示当前单证与数据不一致。

### 发货拦截与锁定

- 海外 DN 完成发货前必须有当前有效的 CI **和** PL，否则 409 `16069`。完成发货时请求里的运单号与已保存的不同，
  以请求为准，现有单证仍视为有效。
- DN 发货（`delivered` / `completed`）后，报关快照、箱子、单证全部锁定 → 409 `16065`。
- 海外 DN 的 `dn.delivered` 追加 `customs_documents`（含 `download_path`）、`packages`、
  `invoice_total{currency, goods_value, freight, insurance, total}`；国内件 payload 不变。

### 配置

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `DOCUMENT_TIMEZONE` | `Asia/Tokyo` | 单证日期所用时区 |
| `CUSTOMS_PDF_FONT_PATH` | （不设） | 单证可选 TTF 字体；默认 Helvetica。字体印不出的字符（如日文）退回 reportlab 内置 CID 字体 |

## 部署

### 生产环境（Gunicorn）

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

## 错误码

| 类别 | 范围 | HTTP | 说明 |
|------|------|------|------|
| 通用错误 | 10000-10999 | 400 | 业务逻辑错误 |
| 认证错误 | 11000-11999 | 401 | 身份验证失败 |
| 权限错误 | 12000-12999 | 403 | 权限不足 |
| 资源错误 | 13000-13999 | 404 | 资源不存在 |
| 验证错误 | 14000-14999 | 400 | 数据格式错误 |
| 库存错误 | 15000-15999 | 400 | 库存相关错误 |
| 状态错误 | 16000-16999 | 400 | 状态流转错误 |

出口单证相关业务码：`14019` 出口资料文本超长、`14020` 国家代码不合法、`16063` 报关结构不合法、`16064` 报关行商品编码不在 DN 明细或重复、`16065` 已发货不可改（409）、`16066` 箱子数据不合法、`16067` 当前状态不能改箱子（409）、`16068` 单证条件不全（409，`details.problems`）、`16069` 发货前必须先出单证（409）、`16070` 不是海外单（409）、`16071` 单证不存在（404）。错误响应可能带结构化的 `details`。

## 关联项目

- https://github.com/loadstarCN/GoodsMart-WMS - 系统文档
- https://github.com/loadstarCN/GoodsMart-WMS-Web - Web 前端

## 贡献

1. Fork 本项目
2. 创建特性分支 (`git checkout -b feature/AmazingFeature`)
3. 提交更改 (`git commit -m 'Add AmazingFeature'`)
4. 推送到分支 (`git push origin feature/AmazingFeature`)
5. 提交 Pull Request

---

**注意**: 本项目采用 AGPLv3 许可证，商业使用需单独授权。
