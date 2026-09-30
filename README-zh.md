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
- 发货（含运单号；也可在 FedEx 自动建运单）
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

## 承运商对接（FedEx）

海外 DN 的发货任务承运商 code 为 `fedex` 时，WMS 可以直接在 FedEx 建运单（Ship API），替代「在 FedEx 网站手工建运单、
再把运单号存进 WMS」这一步；其余流程不变：运单号走现有的保存运单号逻辑存到发货任务、CI / PL 带 AWB 重新签发、完成发货、
`dn.delivered` 照旧。手工保存运单号（`PUT /warehouse/delivery/<task_id>/tracking`）保留，作为 FedEx 不可用时的退路。

| 方法 | 路径 | 权限 |
|------|------|------|
| `GET` | `/warehouse/dn/<id>/carrier-shipment` | `dn_read` / `packing_read` |
| `POST` | `/warehouse/dn/<id>/carrier-shipment` | `packing_edit` / `delivery_edit` |
| `POST` | `/warehouse/dn/<id>/carrier-shipment/cancel` | `packing_edit` / `delivery_edit` |
| `POST` | `/warehouse/dn/<id>/carrier-shipment/dismiss` | `packing_edit` / `delivery_edit` |

`GET` 返回 `{enabled, carrier: "fedex", can_create, blockers[{code, message, goods_code?, field?}], unresolved,
can_dismiss, etd_enabled, default_label_format, label_formats{A4|THERMAL: {image_type, stock_type}},
declared_value_carriage, delivery_task_id, shipment, warnings[]}`：

- `enabled`：FedEx 已配置**且** DN 所属公司在 `FEDEX_ALLOWED_COMPANY_IDS` 里。
- `can_create`：没有 blockers **且** `unresolved` 为 `null`。
- `unresolved`：结果还没定的建单请求，没有为 `null`：`{id, status: "pending" | "unknown", reason, tracking_number,
  transaction_id, created_at, updated_at}`（见下文「建单记录与结果不明」）。
- `can_dismiss`：`unresolved.status` 为 `unknown` 时 `true`。
- `shipment`：有效运单，没有则最近一张已取消的运单，都没有为 `null`；含 `status`（`active` / `cancelled`）、`reason`、
  `sender_country`、`label_format`、`image_type`、`label_stock_type`、`label_file_name`、`label_content_type`、
  `label_parts[]`（面单存档里各文档的来源、箱号、类型、页数、是否存入）、`updated_at` 等。

`POST` 可带请求体 `{"label_format": "A4" | "THERMAL"}`（面单打印方式；不带用 `FEDEX_DEFAULT_LABEL_FORMAT`，其它值 400 `16077`）：

| `label_format` | 打印机 | 默认 imageType / 纸张 | 页面 |
|----------------|--------|------------------------|------|
| `A4` | 激光打印机、A4 / Letter 普通纸 | `PDF` / `PAPER_85X11_TOP_HALF_LABEL` | Letter 页，上半页面单、下半页折叠说明 |
| `THERMAL` | 4 英寸面单机（走驱动当普通打印机用） | `PDF` / `STOCK_4X6` | 4 × 6 英寸（约 100 × 150 mm） |

面单机不要用 `PAPER_4X6`：FedEx 回的是 Letter 页、面单在左上角。各格式的 imageType（`PDF` / `PNG` / `ZPLII` / `EPL2`）与纸张可用配置覆盖。

### 建单

- 有结果不明 / 进行中的建单记录时 `POST` 直接 409 `16079`（`details.unresolved`），不调 FedEx。
- **前置条件**（不满足的原因一次列全；`POST` 返回 409 `16072`，`details.blockers`）：FedEx 未配置
  （`FEDEX_NOT_CONFIGURED`）/ 选项不合法（`FEDEX_CONFIG_INVALID`）、DN 所属公司不在 `FEDEX_ALLOWED_COMPANY_IDS` 里
  （`FEDEX_COMPANY_NOT_ALLOWED`；不设 = 所有公司都不允许，运费记在同一个 FedEx 账号上）、贸易条件为 DDP 而
  `FEDEX_DUTIES_PAYMENT_TYPE` 不是 `SENDER`（`INCOTERM_DUTIES_MISMATCH`，`field: incoterm`）、非海外 DN、DN 不是 `packed` 或已发货、没有发货任务 / 承运商 code 不是 `fedex` / 任务已完成、已有有效运单
  （`SHIPMENT_EXISTS`）或已手工存过运单号（`TRACKING_NUMBER_EXISTS`）、没有箱子 / 超过 30 箱、报关视图有错误级问题
  （码同报关视图）、发件 / 收件地址放不进 FedEx 格式（`SHIPPER_ADDRESS_INVALID` / `RECIPIENT_ADDRESS_INVALID`）、
  收件人姓名或电话缺失。
- **运送申告价额**高于已打包货值时自动压到已打包货值（FedEx 拒收申告价额高于报关货值的运单；部分打包时只保实际发出的货）。
  实际提交的值记在 `shipment.declared_value`；`warnings` 里给 `{code: "DECLARED_VALUE_CAPPED", message,
  requested, applied}`（`GET` 在没有有效运单时作预告，`POST` 响应里是这次实际做的）。
- 没有当前有效的 CI / PL（或已过期）时先按现有逻辑签发。
- `FEDEX_ETD_ENABLED` 开启时，先把当前 CI PDF 上传给 FedEx（Trade Documents Upload API，建单前上传），建单时以
  `ELECTRONIC_TRADE_DOCUMENTS` 引用；关闭时仓库照旧打印 CI 随货。
- 请求内容：发件人 = 公司出口资料（英文名、电话（仓库优先）、联系人、税号）+ 仓库英文地址（仓库没有则用公司的）；
  收件人与税号来自报关快照（税号类型映射到 FedEx `tinType`：EORI → `BUSINESS_UNION`，PCCC / CPF → `PERSONAL_NATIONAL`，
  其余 → `BUSINESS_NATIONAL`）；商品来自发票明细（**已打包数量**、HS、原产国（商品主数据）、单价、金额、重量）；
  每个 WMS 箱子一行包裹（毛重 kg、尺寸 cm，面单印 DN 订单号与发票号）；运送申告价额按箱均分；运费 / 保险费按快照；运费记账到账号，
  关税付款方按 `FEDEX_DUTIES_PAYMENT_TYPE`。
- 地址规则：FedEx 街道最多 3 行 × 35 字符、城市 ≤ 35 字符。英文地址按逗号 / 换行切段，去掉国家名和邮编后的最后一段
  为城市，其余各段一段一行（超过 3 行时连起来重新折行）。例：`4-5-6 Sample-cho, Chuo-ku, Osaka 600-0000, JAPAN` →
  街道 `4-5-6 Sample-cho` / `Chuo-ku`、城市 `Osaka`、邮编 `6000000`。邮编取仓库 / 公司的 `zip_code`
  （日本地址也能从地址里认出，统一发 7 位数字）。收件人没有邮编时带空的 `postalCode`；美国 / 加拿大 / 波多黎各必须有两位州代码。
- 有有效运单时发货任务的运单号锁定为该运单号：完成发货、保存运单号、修改发货任务传了别的号码 → 409 `16078`
  （要换先取消运单；完成发货 / 修改任务时传空值视为不改）。有结果不明 / 进行中的建单记录时，保存运单号或修改发货任务
  带了号码 → 409 `16079`（防止操作员又在 FedEx 网站手工建一张；清空照常）。
- 有进行中 / 有效 / 结果不明的自动运单时：新建或删除该 DN 的发货任务 → 409 `16078`（新任务会成为当前发货任务、
  绕过运单号锁定；新建时带的运单号也按上面的规则校验）；改报关数据（`PUT /warehouse/dn/<id>/customs`）或箱子
  → 409 `16076`（数据已随运单提交；报关的 `details: {reason: "CARRIER_SHIPMENT_OPEN", carrier, carrier_shipment:
  {id, status, tracking_number}}`）。先取消运单，结果不明的先确认作废。上述检查前都先锁 DN 行，与建单 / 取消串行。
- 商品重量：有单件重量的 = 单件重量 × 已打包数量；没有的分摊「总毛重 − 已知重量」（不为正时按数量占比分摊总毛重）。
- **分三段，调 FedEx 时不占 DN 行锁、也不开着数据库事务**：
  1. 锁 DN、检查前置条件、必要时签发 CI / PL，插一条 `pending` 的 `dn_carrier_shipments` 记录并提交
     （部分唯一索引保证同一 DN 至多一条 `pending` / `active` / `unknown`）；
  2. （ETD 时先上传 CI）调 FedEx 建单。整个建单受 `FEDEX_CREATE_BUDGET_SECONDS`（默认 90 秒）总时限约束：
     每次请求（OAuth / ETD 上传 / 建单）的连接 / 读取超时按剩余时间收缩，剩余不足 5 秒就不发下一个请求
     （→ 504 `16074`，`details.budget_exhausted: true`，记录 `failed`），避免 worker 超时被杀；
  3. 成功后先把运单号单独提交到记录上，再在一个事务里：记录置 `active`、运单号 → 发货任务（现有保存运单号逻辑）、
     CI / PL 带 AWB 升版本、面单存档存为 `doc_type: shipping_label` 的单证。
  存档包含响应里的**所有**文档（每箱面单 → 辅助运单 → 其他；国际件的「FEDEX AWB COPY」在第一箱面单 PDF 的后几页，
  不用另外要）：PDF / PNG 合成一个 PDF，ZPLII / EPL2 把原始打印指令按同样顺序拼成一个文件（`.zpl` / `.epl`，
  `application/octet-stream`）。
- **FedEx 出错**（`details` 都带 `maybe_processed` 与 `unresolved`）：
  - 明确拒绝（4xx）→ 502 `16073`（`details.errors`、`transaction_id`），记录 `failed`（`reason: rejected`）；
    请求没发出去（连接超时、DNS / 拒绝连接、OAuth token 失败）→ 记录 `failed`（`not_sent`），连接超时为 504 `16074`；
    ETD 上传失败 → `failed`（`etd_upload_failed`）。这些情况确定没有运单，可以直接重试。
  - 结果不明：读超时 → 504 `16074`；连接建立后中断、5xx、200 但不是 JSON / 解析不出运单 → 502 `16073`。
    记录 `unknown`（`reason`: `timeout` / `connection_error` / `server_error` / `bad_response`），`maybe_processed: true`，
    `details.unresolved` 为该记录。FedEx 侧可能已建单：先去 FedEx Ship Manager 确认，有就在那边取消，再确认作废（见下）。
  - 响应解析不了但能找到运单号：按写库失败处理（取消该运单）。
- **FedEx 建单成功但写库失败**（500）：WMS 调 FedEx 取消该运单（补偿），结果单独提交到记录（原事务回滚后仍留痕）：
  FedEx 确认取消（或回「已取消 / 查无此运单」）→ `cancelled`（`reason: compensated`）；取消失败 / 未确认
  → `unknown`（`reason: compensation_failed`，带 `tracking_number`），要人工在 FedEx 取消后确认作废。
- 已签发的 CI / PL 随第 1 段提交：FedEx 失败时照常有效（不再回滚）。
- 面单用 `GET /warehouse/dn/<id>/customs-documents/<label_document_id>/file` 下载（PDF inline，ZPL / EPL 作为附件）；面单不参与「必须有有效 CI + PL」
  的发货拦截。

### 建单记录与结果不明

每次建单请求在 `dn_carrier_shipments` 留一条记录：

| `status` | 含义 |
|----------|------|
| `pending` | 正在请求 FedEx |
| `active` | 有效运单 |
| `cancelled` | 已取消（手工取消；或写库失败后自动取消，`reason: compensated`；FedEx 回已取消，`reason: already_cancelled`） |
| `failed` | FedEx 明确拒绝 / 请求没发出去，确定没有运单（`reason`: `rejected` / `not_sent` / `etd_upload_failed` / `budget_exhausted` / `interrupted`） |
| `unknown` | 结果不明，FedEx 侧可能有运单（`reason`: `timeout` / `connection_error` / `server_error` / `bad_response` / `compensation_failed` / `interrupted`） |
| `dismissed` | 操作员确认 FedEx 上没有这张运单（或已手工取消） |

- `pending` 超过 `FEDEX_PENDING_STALE_MINUTES`（默认 10 分钟；实际不短于建单总时限 + 75 秒）读取时按
  `unknown`（`reason: stale`）处理（例如 worker 被杀）。worker 超时被杀（gunicorn 抛 `SystemExit`）时也会尽力把记录落成
  `unknown` / `failed`（`reason: interrupted`）。
- 有 `unresolved`（`pending` / `unknown`）时：不能建单（409 `16079`）、不能存 / 改运单号（409 `16079`）、
  不能改报关数据 / 箱子（409 `16076`）、不能新建 / 删除发货任务（409 `16078`）。
- **确认作废**：`POST /warehouse/dn/<id>/carrier-shipment/dismiss`，请求体 `{"confirm": true}`（缺或不是 `true` → 400，
  `field: confirm`）。只处理 `unresolved.status == "unknown"`（含过期的 `pending`）的记录 → `dismissed`，记
  `dismissed_by` / `dismissed_at`；不调 FedEx；返回与 `GET` 同形。没有可确认作废的（或请求还在进行中）→ 409 `16075`
  （`details.unresolved`）。之后可以重新建单或手工存运单号。

### 取消

DN 发货前可取消（发货后 409 `16065`；没有有效运单 409 `16075`）。调 FedEx `PUT /ship/v1/shipments/cancel`
（`DELETE_ALL_PACKAGES`；发件国用建单时记下的 `sender_country`，迁移前的老记录按建单同一口径推），FedEx 拒绝 → 502 `16073`
带原因。成功后记录标 `cancelled`、发货任务上的运单号清空、面单作废（`void_reason: shipment_cancelled`）、CI / PL 去掉 AWB
重新签发（条件不满足时作废）。只要求 FedEx 凭证齐全：公司后来被移出 `FEDEX_ALLOWED_COMPANY_IDS` 也能取消已建的运单。

FedEx 回「已取消 / 查无此运单」视为取消成功（`reason: already_cancelled`），例如上次取消在 FedEx 成功了但本地写库失败，
再点一次取消就能收口。依据：FedEx Ship API 公开文档没有单列这两种情况的错误码，本仓库也未在 sandbox 实测，所以只按错误码
（不看随语言变化的 message）判断——码里同时有 `ALREADY` 与 `CANCEL` / `DELETE`，或同时有 `TRACKING` / `SHIPMENT` 与
`NOT FOUND` / `NOT EXIST`，以及旧版 Web Services 的 `8159`（「Shipment Delete was requested for a tracking number already in
a deleted state」）。命中时日志记原始错误码；正式环境遇到没覆盖的码，补到 `carrier_services.ALREADY_CANCELLED_CODES`。

### 配置

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `FEDEX_API_BASE` | `https://apis-sandbox.fedex.com` | 正式环境为 `https://apis.fedex.com` |
| `FEDEX_API_KEY` / `FEDEX_SECRET_KEY` / `FEDEX_ACCOUNT_NUMBER` | （不设） | FedEx 开发者项目凭证；缺任一项 = 功能关闭 |
| `FEDEX_ALLOWED_COMPANY_IDS` | （空） | 允许自动建运单的公司 ID（逗号分隔，如 `1,3`）；**不设 = 所有公司都不允许**（运费记在同一个 FedEx 账号上） |
| `FEDEX_SERVICE_TYPE` | `INTERNATIONAL_ECONOMY` | 服务类型 |
| `FEDEX_PICKUP_TYPE` | `USE_SCHEDULED_PICKUP` | 揽收方式 |
| `FEDEX_DEFAULT_LABEL_FORMAT` | `A4` | 建单请求不指定时的面单打印方式（`A4` / `THERMAL`） |
| `FEDEX_LABEL_A4_IMAGE_TYPE` / `FEDEX_LABEL_A4_STOCK_TYPE` | `PDF` / `PAPER_85X11_TOP_HALF_LABEL` | 覆盖 `A4` 的格式 / 纸张 |
| `FEDEX_LABEL_THERMAL_IMAGE_TYPE` / `FEDEX_LABEL_THERMAL_STOCK_TYPE` | `PDF` / `STOCK_4X6` | 覆盖 `THERMAL` 的格式 / 纸张（Zebra 等可设 `ZPLII`） |
| `FEDEX_ETD_ENABLED` | `False` | 电子贸易单证（上传 CI） |
| `FEDEX_DUTIES_PAYMENT_TYPE` | `RECIPIENT` | `RECIPIENT` 或 `SENDER`（记账到账号） |
| `FEDEX_DOCUMENT_API_BASE` | （自动） | Trade Documents Upload 的地址，默认按 `FEDEX_API_BASE` 选测试 / 正式 |
| `FEDEX_CONNECT_TIMEOUT_SECONDS` / `FEDEX_TIMEOUT_SECONDS` | `5` / `30` | 单次请求的连接 / 读取超时（秒）；建单时再按剩余总时限收缩 |
| `FEDEX_CREATE_BUDGET_SECONDS` | `90` | 建单总时限（OAuth + ETD 上传 + 建单）；要小于 gunicorn / nginx 超时（例如 120 秒）。取消也按这个时限 |
| `FEDEX_PENDING_STALE_MINUTES` | `10` | `pending` 超过多少分钟按结果不明（`stale`）处理；实际不短于建单总时限 + 75 秒 |

OAuth token（`/oauth/token`，client credentials）进程内缓存到过期前。凭证不写日志、不出现在响应里。
多箱 PDF 面单合成使用 [pypdf](https://pypi.org/project/pypdf/)（BSD）。

### 测试环境冒烟与面单认证

`scripts/fedex_sandbox_smoke.py` 按面单打印方式 × 箱数在 FedEx **测试环境**各建一票运单，面单按
`<服务>_<箱数>pkg_<打印方式>.<扩展名>` 保存后取消（`FEDEX_API_BASE` 不含 `sandbox` 时拒绝执行）。发件人 / 收件人默认虚构，
认证样张可用 JSON 文件给出（字段同出口资料 / 报关快照 consignee）：

```bash
python scripts/fedex_sandbox_smoke.py [--label-format A4|THERMAL|both] [--packages 1,2] [--to US|DE|HK] \
    [--shipper-json shipper.json] [--recipient-json recipient.json] [--etd] [--out ./fedex-sandbox-labels] [--env-file .env]
```

正式使用前 FedEx 要求面单认证：用测试环境打出所用服务的面单，扫描后连同 Label Cover Sheet 发给 FedEx
（见 developer.fedex.com → Certification），通过后把 `FEDEX_API_BASE` 与凭证换成正式环境。

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

出口单证相关业务码：`14019` 出口资料文本超长、`14020` 国家代码不合法、`16063` 报关结构不合法、`16064` 报关行商品编码不在 DN 明细或重复、`16065` 已发货不可改（409）、`16066` 箱子数据不合法、`16067` 当前状态不能改箱子（409）、`16068` 单证条件不全（409，`details.problems`）、`16069` 发货前必须先出单证（409）、`16070` 不是海外单（409）、`16071` 单证不存在（404）。承运商运单（FedEx）：`16072` 建单前置条件不满足（409，`details.blockers`）、`16073` FedEx 返回错误（502，`details.errors` / `transaction_id`）、`16074` FedEx 超时（504）、`16075` 没有有效运单（409）、`16076` 有进行中 / 有效 / 结果不明的运单时不能改箱子或报关数据（409）、`16077` `label_format` 不合法（400）、`16078` 运单号与有效的自动运单不一致，或有自动运单时新建 / 删除发货任务（409）、`16079` 有结果不明 / 进行中的建单记录（409，`details.unresolved`）。错误响应可能带结构化的 `details`。

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
