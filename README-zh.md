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
# 要跑测试（pytest、fakeredis 等）改装开发依赖：
pip install -r requirements-dev.txt
```

生产只装 `requirements.txt`；测试 / 开发用的包在 `requirements-dev.txt`（第一行 `-r requirements.txt`）。
用 `./run_tests.sh`（或 `python -m pytest tests/`）跑测试，缺开发依赖时脚本会提示。

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

布尔开关（`DEBUG`、`WEBHOOK_URL_STRICT`、`RATELIMIT_ENABLED`、`SCHEDULER_ENABLED`、`CHECK_WHITELIST`、`CHECK_BLACKLIST`）
统一解析：不分大小写，`1` / `true` / `yes` / `on` 为真，其余为假（认不出的值启动时打日志）。
`FLASK_ENV=production` 时，`JWT_SECRET_KEY` 过弱、数据库是 SQLite、`SETTINGS_ENCRYPTION_KEY` 缺失或不是合法的 Fernet 密钥
都会拒绝启动（要沿用以前存的密文，填 `system_settings` 表里 `_encryption_key` 的值）；`WEBHOOK_URL_STRICT` 默认开启。
不设 `FLASK_ENV` 按开发配置运行，此时数据库若不是 SQLite 会打一条醒目的 warning。

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
│   ├── carrier/               # 承运商管理、在线对接插件（integrations/）
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
- 发货（含运单号；承运商设了在线对接时可自动建运单，内置 FedEx）
- 代收付款管理

### 库存运营
- 实时库存追踪（多阶段：ASN → 已收货 → 已分拣 → 在库 → DN → 已拣货 → 已打包 → 已发货）
- 库存调整（增减及原因追踪）
- 盘点（带审批流程）
- 库位间调拨
- 库存移除
- 库存快照（每日定时）

### 作业流程规则
- 分拣 / 拣货 / 打包 / 发货任务只由单据流转自动生成（ASN 收货 → 分拣；DN 开始处理 → 拣货；拣货完成 → 打包；打包完成 → 发货），
  没有手工新建接口：`POST /warehouse/sorting/`、`/picking/`、`/packing/`、`/delivery/` → 405。
- 删除分拣 / 拣货 / 打包批次（任务 `in_progress` 时）连同批次下的明细一起删除。
- 追加批次（`POST /warehouse/sorting|picking|packing/<task_id>/batches/`）可带可选的 `client_batch_id`（1~64 个字符的字符串，
  前端每次提交一批生成一个，断网 / 超时重试时原样带上）：第一次提交正常创建（201）；同一任务下同一个 `client_batch_id`
  再次提交且明细相同（比商品与数量，拣货另比库位，与顺序无关；备注、操作时间不比）→ 不再新增，200 返回已存在的批次
  （结构与 201 相同）；明细不同 → 409 `16121` `details {client_batch_id, batch_id}`；格式不对 → 400 `16123`。
  不带 `client_batch_id` 时行为不变。比的是批次当前的明细：批次建好后明细被改过 / 删过，再用原内容重放也是 `16121`；
  批次被删掉后再重放会重新创建。
- 调整单职责分离：创建人不能审批（403 `16040`）；最后增 / 改 / 删明细的人（`details_modified_by`）也不能审批 → 409 `16122`。
- 移库：来源库位没有这个商品 → 404 `16120`（原来与「缺仓库 id」共用 `14003`）。
- 多商品 / 多库位的单据与批量操作统一按 goods_id（库位库存再按 location_id）升序加锁，先库存行、后库位库存行，
  两个请求商品顺序相反时不再死锁；同一新商品第一次被并发建 ASN、同一库位第一次被并发上架 / 移入时不再 500。
- DN 取消（`PUT /warehouse/dn/<id>/cancel/`）：`pending` 等同关闭；`in_progress` 须拣货尚未开始（否则 409 `16053`）；
  `picked` / `packed`（未发货）可取消，已拣 / 已打包的货退回待上架（`sorted_stock`，`total_stock` 不变），拣货 / 打包 / 发货任务停用，
  有效的出口单证作废（`void_reason: dn_cancelled`）；有未结束的承运商自动运单（进行中 / 有效 / 结果不明 / 取消中）→ 409 `16110`，
  先在单证卡片取消运单；`delivered` / `completed` / `closed` → 409 `16052`。`in_progress` / `picked` / `packed` 取消后推 `dn.cancelled`。
- 调整单取消（`PUT /warehouse/adjustment/<id>/cancel/`，需要 `adjustment_approve`）：`pending` / `approved` 可取消，库存不动；
  取消后 `is_active=false`、`status` 保持原值（`operator_id` 为取消人），之后的一切操作 → 409 `16111`；已完成 → 409 `16112`。
  由盘点单生成的调整单取消后，同一盘点单可以重新生成。完成调整时：正向调整的库位还没有该商品的记录 → 新建（库位须启用）；
  负向调整但库位上已没有该商品的记录 → 409 `16113`（请取消后按当前库存重新建单）。
- 盘点：实盘录入时（批量保存提交的行、单行完成、明细修改给了 `actual_quantity`）取当时的库位库存作系统数并计算差异，
  调整单按录入时的系统数生成；盘点完成前每条明细都必须已录入（盘到 0 件也要明确提交）→ 否则 409 `16114` `details {detail_ids}`。
- 库位有上架 / 下架 / 移库 / 拣货 / 盘点 / 调整 / 库存快照等历史记录时不能删除 → 409 `44009`
  `details {entity: "location", id, references}`，请改用停用；库位上还有货仍是 409 `16084`。
- 数值字段：ASN 明细 `weight` / `volume`、库位 `width` / `depth` / `height` / `capacity` 空串按 null 处理；
  非数字、负数（库位尺寸 ≤ 0）→ 400 `16115`。
- 盘点单 / 调整单列表支持 `keyword`：盘点单按单号、任务名、明细商品 / 库位编码；调整单按单号、调整原因、明细商品编码 / 名称、库位编码。

## Webhook 集成

WMS 通过 Webhook 向外部系统推送事件通知，使用 HMAC-SHA256 签名验证。

### 支持事件

| 事件 | 触发时机 |
|------|----------|
| `asn.received` | ASN 标记为已收货 |
| `asn.completed` | ASN 完成（含实际数量） |
| `asn.cancelled` | 已收货（未分拣）的 ASN 被取消 |
| `dn.in_progress` | DN 开始处理 |
| `dn.picked` | DN 拣货完成（含各商品计划 / 已拣量） |
| `dn.packed` | DN 打包完成（含各商品计划 / 已拣 / 已打包量） |
| `dn.delivered` | DN 已发货（含运单号） |
| `dn.tracking_updated` | 自动运单的承运商跟踪有变化（状态 / 扫描记录，完整的最新结果，见「跟踪」） |
| `dn.completed` | DN 完成 |
| `dn.cancelled` | 开始处理后（`in_progress` / `picked` / `packed`）的 DN 被取消（含 `previous_status` 与各商品已拣 / 已打包量） |
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

`WEBHOOK_URL_STRICT` 开启时（生产默认）：地址必须是 `https`，主机解析出的地址必须全是公网地址（内网、环回、链路本地、
`100.64.0.0/10` 共享地址——含云元数据地址 `100.100.100.200`——、保留段、组播一律拒绝）。发送时再解析一次、逐个校验，
只连校验过的 IP（不再做第二次 DNS 查询），TLS 的 SNI 与证书校验仍按原域名；webhook 不走环境变量里的代理。

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
  "source": "station", "changed_at": "2026-01-01T10:00:00+09:00", "changed_at_ms": 1767229200123 }
```

- **新旧判断**：`changed_at` 只到秒（格式不变）。`changed_at_ms`（整数，epoch 毫秒；同一 WMS 进程内严格递增，
  在商品行写入之后取值）用来判断新旧：接收方按 `goods_id` 记住已应用的最大 `changed_at_ms`，小于或等于它的 payload 忽略。
  事件可能乱序到达（重试、多个推送进程）。加这个字段之前排队的事件没有 `changed_at_ms`，按 `changed_at` 处理。

- `source`：取商品新建 / 修改请求体里的 `spec_source`（`station` / `manual` / `import` / `api`）；
  没传时 API Key 调用为 `api`、登录用户为 `manual`；CSV 导入为 `import`。
- 同一商品对同一 Key 还有没推送过的待发送事件时，新的变更覆盖它的 payload，不再新增
  （`dedupe_key = goods:<id>`；`changed_fields` 取并集）；已推送失败过的不覆盖，另建新事件（新的 `X-Webhook-Id`）接替、
  旧的标 `superseded`；发送时发现同一商品已有更新的事件，当前这条也作废。
- CSV 批量导入时订阅 Key 与待发送事件按公司各查一次，不随行数增长。
- 请求头、签名、重试与其他事件相同。

商品 `origin_country` 只接受 ISO 3166-1 alpha-2 代码（自动大写；空串 = 清空；其他值 → 400 `10014`），
没有默认值。`GET /warehouse/goods/?origin_missing=true` 列出未录入原产国的商品。
仓库作业账号可用 `PUT /warehouse/goods/<id>/origin-country`（`{"origin_country": "CN" | "" | null, "spec_source"?: ...}`）
只改原产国，需要 `goods_edit`、`sorting_edit`、`packing_edit` 任一权限，同样触发 `goods.spec_updated`。
它与 `GET /warehouse/goods/<id>` 一样受仓库范围限制（非公司管理员员工须带 `X-WAREHOUSE-ID`，且商品在该仓库有库存，
否则 404 `13003`），响应只有 `{id, code, origin_country}`。
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
| 公司（`PUT /warehouse/company/<id>`） | `legal_name_en`、`address_en`、`country_code`（ISO 3166-1 alpha-2，新建不传时为 `JP`）、`tax_id_label`、`tax_id`、`export_contact_name`、`export_signatory_name`、`export_signatory_title` |
| 仓库（`PUT /warehouse/warehouse/<id>`） | `address_en`、`country_code`、`contact_name_en`（与公司地址不同时印为 *Ship From*） |

`country_code` 不合法 → 400 `14020`；文本超长 → 400 `14019`。接口输出不补默认值：库里为空就返回 `null`
（单证上的出口国取仓库的、仓库为空取公司的，都为空报 `EXPORTER_PROFILE_INCOMPLETE`）。完整性检查与 CI 上的
*Country of Export*、*Shipper / Exporter* 栏印的国家都用这个出口国。

### 报关快照

`POST /warehouse/dn/` 顶层可带 `customs`（国内件不带）；`PUT /warehouse/dn/<id>/customs` 整体替换
（权限 `dn_edit`，发货后不可改）。结构同英文 README 示例：`invoice_number`、`currency`、`incoterm`、
`export_reason`、`recipient_country`、`recipient_tax_id`、`recipient_tax_id_type`、`freight_charge`、
`insurance_charge`、`declared_value_carriage`、`consignee{...}`、`lines[{goods_code, quantity, unit_value, total_value, description_en, hs_code, jp_export_code, origin_country, quantity_unit}]`。

- 结构错误拒绝：`customs` 非对象、`lines` 非数组、字段类型错误、数值超上限 → 400 `16063`（`details.field`）；
  行的 `goods_code` 不在 DN 明细或重复 → 400 `16064`。上限：`unit_value` / `total_value` 绝对值 ≤ 1,000,000,000,000；
  `freight_charge` / `insurance_charge` / `declared_value_carriage` / 行 `quantity` ≤ 2,147,483,647（数据库 INTEGER）。
  行的 `unit_value` 小数位超过币种小数位（JPY / KRW / VND / CLP / ISK / PYG / UGX / XAF / XOF 为 0 位，其它 2 位；
  币种缺失也按 2 位）→ 400 `16063` `details {field, currency, max_decimal_places}`，保证 CI 上「单价 × 数量 = 金额」。
  规则上线前存下的超精度单价，在报关视图、CI、承运商建单请求里一律先按币种小数位四舍五入再算金额；量化后为 0 的报
  `UNIT_VALUE_MISSING`。
- `freight_charge` / `insurance_charge` / `declared_value_carriage` 只收整数（列是 INTEGER），USD 等带分的币种也不收小数。
- 内容不全照收，缺什么在 `problems` 里列出。
- `invoice_number` 缺省用 DN 的 `order_number`；发票数量一律取**已打包数量**（金额 = 单价 × 已打包数量，
  已打包 0 的行不上发票）；原产国以商品主数据 `goods.origin_country` 为准，行里的只做记录。
- `freight_charge` 在发票上单列为 *Freight*，并计入 *Total Invoice Value*。
- 运送保险（可选）：`insurance_charge`（非负整数或 `null`）大于 0 时在 *Freight* 下单列 *Insurance* 并计入
  *Total Invoice Value*；`declared_value_carriage`（非负整数或 `null`）是运送申告价额，仓库在承运商系统登记出货时填写，
  报关视图里显示、单证上不印。两个键都可不带（旧请求照旧）；不合法 400 `16063`。任一变化都会作废已签发的单证；
  没有这两个值的快照单证指纹不变。
- `jp_export_code`（可选，9 位日本出口统计品目番号，前 6 位应等于 HS）存入快照并在接口返回，CI / PL 不印（只印 HS）。
- `hs_code` 接受 6–10 位；CI 上只印前 6 位（国际通用的 HS 部分），7–10 位只在报关视图（`hs_code_formatted`）里显示。
- 替换快照时，只有印在单证上的内容变了才作废现有单证。

`GET /warehouse/dn/<id>/customs`（`dn_read` 或 `packing_read`）返回
`{dn_id, is_export, locked, customs, lines[], packages[], totals, exporter, problems[], ready, current_documents[], documents_outdated}`。
`totals = {quantity, goods_value, freight, insurance, invoice_total, package_count, gross_weight_kg, net_weight_kg}`，
`invoice_total = goods_value + freight + insurance`（没投保 `insurance` 为 0）。

- 错误级问题：`NOT_PACKED`、`PACKAGES_MISSING`、`EXPORTER_PROFILE_INCOMPLETE`、
  `EXPORTER_NON_LATIN_TEXT`（发货人一侧印在单证上的文本含非拉丁字符：公司英文名 / 英文地址 / 联系人 / 签署人 / 税号 /
  电话 / 邮箱、Ship From 的仓库英文地址 / 联系人；`field` 指向要改的主数据字段，如 `company.export_signatory_name`、
  `warehouse.address_en`、`warehouse.phone`）、`INCOTERM_MISSING`、
  `EXPORT_REASON_MISSING`、`CURRENCY_INVALID`、`RECIPIENT_COUNTRY_MISSING`、`LINE_MISSING`、`HS_CODE_MISSING`
  （去掉 `.`、空格、`-` 后须为 6–10 位数字）、`DESCRIPTION_MISSING`、`DESCRIPTION_NOT_ASCII`、`ORIGIN_MISSING`、`UNIT_VALUE_MISSING`。
- 警告（不拦截）：`RECIPIENT_TAX_ID_MISSING`、`NON_LATIN_TEXT`（收件人含非拉丁字符）、`NET_WEIGHT_UNKNOWN`、`GROSS_LT_NET`、
  `JP_EXPORT_CODE_MISMATCH`（`jp_export_code` 非 9 位数字或前 6 位与 HS 不一致）、
  `JP_EXPORT_CODE_MISSING`（JPY 货值（FOB，不含运费 / 保险费）超过 200,000 且有行缺 `jp_export_code`）、
  `JP_EXPORT_VALUE_UNVERIFIED`（币种不是 JPY、出口国为 `JP` 且有已打包的行缺 `jp_export_code`：不引入汇率，无法自动判断
  是否超过 20 万円，请人工确认；每张 DN 一条，`field: jp_export_code`）。

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

- 单证只有货物、收发货双方和箱子，**不印运单号（AWB）和承运商**：DN 打包完（`packed`）就能在打包任务页出单证，
  不用等建运单；之后保存 / 修改运单号或承运商不影响单证，不用重签。
- 出单证条件：DN 为 `packed`、有报关快照、至少 1 箱、出口资料齐、没有错误级问题；否则 409 `16068`
  （`details.problems`）；不是海外单 409 `16070`。
- 某块文字一页排不下（实际上是收件人各字段都很长，如每个约 240 个 CJK 字符）→ 409 `16117`
  `details {document: "CI" | "PL", reason: "LAYOUT_OVERFLOW"}`，不签发也不作废旧单证；请缩短收件人信息后重新签发。
- CI 与 PL 成对签发并存库（`dn_documents`），生成即定稿。数据未变再次签发 → 返回现有版本（200）；
  数据变了 → 旧版作废、版本 +1（201）。报关视图里 `documents_outdated: true` 表示当前单证与数据不一致，要重新签发。
- 以前签发的单证印着 AWB / 承运商（数据指纹里也有这两项）：印的是现在发货任务（或未结束的自动运单）上的运单号 /
  承运商、其余内容相同时仍算当前有效，不算过期、不拦发货；再签发时出一版不印这两行的新版本（201）。
- 有进行中 / 有效 / 结果不明 / 取消中的自动运单时，当前单证就是随运单提交的那份：要签发的单证数据有任何变化
  （例如商品原产国、公司 / 仓库出口资料改了）→ 409 `16076` `details {reason: "DOCUMENT_DATA_CHANGED", status,
  carrier, carrier_shipment: {id, status, tracking_number}}`，先取消运单。
- `file` 以 `application/pdf` inline 返回，响应头 `X-Content-SHA256`。
- 运单号 / 承运商用 `PUT /warehouse/delivery/<task_id>/tracking` `{"tracking_number": "...", "carrier_id": 1}` 保存
  （权限 `delivery_edit`，发货后 409 `16065`），或在承运商对接里自动建运单（见「承运商对接」）。

### 发货拦截与锁定

- 完成发货（`PUT /warehouse/delivery/<task_id>/complete/`）请求里 `tracking_number` 为空串 / `null` / 不传 → 不改已存的运单号
  （国内件也一样）。完成发货先锁 DN 行，与改箱子 / 改快照 / 出单证串行。
- 海外 DN 按写入前的已存数据检查：没有当前有效的 CI 或 PL → 409 `16069`
  `details {missing_documents: [...], outdated: false}`；单证已过期（箱子 / 快照 / 出口资料改过没重出）→ 409 `16069`
  `details {missing_documents: [], outdated: true}`。
- DN 的自动运单进行中 / 结果不明，或正在取消 / 取消结果不明（`pending` / `unknown` / `cancelling`）时，带不带运单号都不能
  完成发货 → 409 `16079`（`details.unresolved`），先处理（取消 / 确认作废）。
- 单证不印运单号 / 承运商，完成发货时填的运单号、承运商不再与 CI 比对（原来的 `16080` / `16092` 已不再使用）；
  有有效自动运单时运单号 / 承运商的锁定见「承运商对接」（`16078`）。
- 发货任务只在打包完成时自动生成，没有手工新建接口。一张 DN 同时只允许一条未完成（`pending` / `in_progress`）的有效发货任务：
  已有（例如修复前手工建的存量任务）→ 复用那条，不再生成第二条。
- 海外 DN 的单证 / 自动运单只认当前发货任务（最新的有效任务）：对别的任务完成发货 / 保存运单号 / 修改 → 409 `16091`
  `details {delivery_task_id, current_status}`。
- `dn.delivered` 的 `tracking_number` 取被完成的那条任务的运单号。
- DN 发货（`delivered` / `completed`）后，报关快照、箱子、单证全部锁定 → 409 `16065`。
- 海外 DN 的 `dn.delivered` 追加 `customs_documents`（含 `download_path`）、`packages`、
  `invoice_total{currency, goods_value, freight, insurance, total}`；国内件 payload 不变。

### 配置

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `DOCUMENT_TIMEZONE` | `Asia/Tokyo` | 单证日期所用时区 |
| `CUSTOMS_PDF_FONT_PATH` | （不设） | 单证用的 TrueType 字体（`.ttf`，或 `.ttc` 的第 1 个字体；不支持 CFF 轮廓的 OTF，如 Noto Sans CJK）。设了就用它印全部文字，字体子集嵌入 PDF；要让收件人的日文等也嵌入，选带这些字形的字体（如 IPAexGothic）。字体里没有的字符、以及不设时 Helvetica 印不出的字符，退回 reportlab 内置的日文 CID 字体（不嵌入，依赖阅读器的字体） |

## 承运商对接（自动建运单）

海外 DN 的承运商设置了在线对接时，WMS 可以直接在承运商系统建运单，替代「在承运商网站手工建运单、再把运单号存进 WMS」
这一步；其余流程不变：运单号走现有的保存运单号逻辑存到发货任务、完成发货、`dn.delivered` 照旧（CI / PL 不印运单号，
不用重签）。
对接与具体承运商无关：每家承运商一个插件（`warehouse/carrier/integrations/`），内置 FedEx（见「FedEx 插件」），也可以自己写
（见「编写承运商插件」）。没设对接的承运商照旧手工保存运单号（`PUT /warehouse/delivery/<task_id>/tracking`），这也是对接
不可用时的退路。

### 设置对接

在管理端「承运商」编辑页的「在线对接」里按承运商设置：一个承运商至多一个对接（`carrier_integrations`），选插件、填账号 /
密钥 / 服务 / 面单等。设置跟着承运商记录走，每家公司用自己的承运商记录和账号（运费记在各自的账号上）。

| 方法 | 路径 | 权限 |
|------|------|------|
| `GET` | `/warehouse/carrier/integrations` | `carrier_read` |
| `GET` | `/warehouse/carrier/<id>/integration` | `carrier_read` |
| `PUT` | `/warehouse/carrier/<id>/integration` | `carrier_edit` |
| `DELETE` | `/warehouse/carrier/<id>/integration` | `carrier_edit` |
| `POST` | `/warehouse/carrier/<id>/integration/test` | `carrier_edit` |

- `GET …/integrations`：已安装的插件 `[{code, name, portal_name, portal_url, label_formats, fields[]}]`，`fields` 是表单定义
  （见「编写承运商插件」）。
- `GET …/<id>/integration`：`{adapter, name, enabled, settings, secrets: {key: 是否已设置}, ready, problems[{code, message,
  field?, fields?}], updated_at, updated_by}`，没有对接为 `null`。`settings` 已补上默认值；密钥只回「已设置 / 未设置」，明文从不返回。
  `ready` = 插件已安装、设置齐全且取值合法（不看启用）。承运商列表 / 详情另带 `integration: {adapter, name, enabled} | null`。
- `PUT` 请求体 `{adapter, enabled, settings, secrets}`，返回同 `GET`：
  - `settings` 整体替换：不认识的键丢掉，没传 / 空值用默认；必填项可以先空着保存（`ready: false`，`problems` 里列出）。
  - `secrets` 没传或空串 = 保持已存的值（换插件时清空）。密钥 JSON 用 `SETTINGS_ENCRYPTION_KEY`（Fernet）加密后入库；
    换了这把密钥、解不开时报 `CARRIER_NOT_CONFIGURED`（`field: secrets`），重新填写即可。
  - 不合法 → 400 `16124`（`field`：`adapter` / `enabled` / `settings.<key>` / `secrets.<key>`）。
  - `adapter` 为空 = 删除对接（同 `DELETE`，`DELETE` 返回 204）。
- `POST …/test`：用请求体里的设置（同 `PUT`；没带的密钥用已存的；请求体不带 `adapter` 时用已存的设置）连一次承运商（只认证，不建单，
  限时 20 秒），不保存。连不上也是 200：`{ok: false, message, errors?, status_code?, permission_denied?, missing?}`。
- 停用（`enabled: false`）：不能新建运单（blocker `CARRIER_INTEGRATION_DISABLED`），已建的运单仍可以取消 / 确认作废。
- 承运商还有未结束的自动运单（`pending` / `active` / `unknown` / `cancelling`）时不能删除对接或换插件（取消要用这套设置）
  → 409 `16125` `details.shipments[{id, dn_id, status, tracking_number}]`；改设置、换密钥、停用照常。

#### 从 `FEDEX_*` 环境变量升级

WMS 不再读任何 `FEDEX_*` 变量（原来的 `FEDEX_ALLOWED_COMPANY_IDS` 公司白名单也没有了：哪个承运商设了对接就能用）。迁移
`c7e1a9d3f5b2` 建 `carrier_integrations` 表，并给已有的 `dn_carrier_shipments` 回填 `carrier_id`（DN 当前有效发货任务的承运商，
没有则 DN 的）。部署后趁 `.env` 里的旧变量还在执行一次：

```bash
flask carrier import-fedex-env [--carrier-id <id>] [--overwrite] [--dry-run]
```

- 导入到：`--carrier-id` 指定的承运商；不指定则为 `FEDEX_ALLOWED_COMPANY_IDS` 各公司里 code 为 `fedex`（不分大小写）的承运商
  （都写入同一套凭证）。已有对接的跳过，`--overwrite` 覆盖；`--dry-run` 只列出、不保存。
- 导入的值：`FEDEX_API_KEY` / `FEDEX_SECRET_KEY` / `FEDEX_ACCOUNT_NUMBER`（缺一项就报错退出）；`FEDEX_API_BASE` 不设或含
  `sandbox` → `environment: sandbox`，否则 `production`；`FEDEX_SERVICE_TYPE`、`FEDEX_PICKUP_TYPE`、`FEDEX_DUTIES_PAYMENT_TYPE`、
  `FEDEX_DEFAULT_LABEL_FORMAT`、`FEDEX_LABEL_THERMAL_IMAGE_TYPE`、`FEDEX_LABEL_THERMAL_PAGE_SIZE`、`FEDEX_ETD_ENABLED`。
  插件不支持的取值（例如选项外的服务类型、`PNG`）打印提示并用默认值。`FEDEX_LABEL_A4_*`、`FEDEX_LABEL_THERMAL_STOCK_TYPE`、
  `FEDEX_DOCUMENT_API_BASE` 不再可配（见「FedEx 插件」）。
- 超时 / 时限改名为 `CARRIER_*`（见「配置」），不自动迁移；之后 `FEDEX_*` 可以从 `.env` 删掉。
- 导入之前 FedEx 承运商没有对接：不能自动建运单（`CARRIER_NOT_INTEGRATED`），已建的运单也不能从 WMS 取消（409 `16072`）。

### 运单接口

| 方法 | 路径 | 权限 |
|------|------|------|
| `GET` | `/warehouse/dn/<id>/carrier-shipment` | `dn_read` / `packing_read` / `delivery_read` |
| `POST` | `/warehouse/dn/<id>/carrier-shipment` | `packing_edit` / `delivery_edit` |
| `POST` | `/warehouse/dn/<id>/carrier-shipment/cancel` | `packing_edit` / `delivery_edit` |
| `POST` | `/warehouse/dn/<id>/carrier-shipment/dismiss` | `packing_edit` / `delivery_edit` |

`GET` 返回 `{enabled, carrier, integration, can_create, blockers[{code, message, goods_code?, field?}], unresolved,
can_dismiss, can_cancel, etd_enabled, default_label_format, label_formats{A4|THERMAL: {image_type, stock_type}},
declared_value_carriage, delivery_task_id, tracking_number, shipment, warnings[]}`：

- `carrier`：建单用的承运商 `{id, name, code}`——当前有效发货任务的承运商，没有则 DN 的（与 CI 上印的同一口径）；都没有为 `null`。
- `integration`：该承运商的对接 `{code, name, portal_name, portal_url, enabled, ready}`；`null` = 没设对接（或承运商不属于 DN
  所属公司），在承运商网站手工建运单再保存运单号。`enabled` 是对接的开关；`ready` = 插件已安装、已启用、设置齐全且合法。
  `portal_name` / `portal_url` 是承运商的发货后台（结果不明时去那里核对 / 取消；FedEx 为 FedEx Ship Manager）。
- `enabled`：同 `integration.ready`（能不能建单另看 `can_create`）。
- `etd_enabled`、`default_label_format`、`label_formats`（只列插件支持的打印方式）取自插件与对接设置；没有可用的插件时为
  `false` / `null` / `{}`。
- `tracking_number`：发货任务上现在的运单号（自动建的或手工填的），没有发货任务为 `null`。
- `can_create`：没有 blockers **且** `unresolved` 为 `null`。
- `unresolved`：结果还没定的记录，没有为 `null`：`{id, status: "pending" | "unknown" | "cancelling", reason,
  tracking_number, transaction_id, created_at, updated_at}`（见下文「建单记录与结果不明」）。
- `can_dismiss`：`unresolved.status` 为 `unknown`，或为 `cancelling` 且没有取消请求正在进行时 `true`。
- `can_cancel`：有效运单、没有取消请求正在进行的 `cancelling`、带运单号的 `unknown` 记录为 `true`；DN 已发货为 `false`。
- `shipment`：有效（或取消中）的运单，没有则最近一张已取消的运单，都没有为 `null`；含 `id`、`carrier`（插件代码）、
  `carrier_id`（建单的承运商）、`status`（`active` / `cancelling` / `cancelled`）、`reason`、`service_type` / `service_name`、
  `sender_country`、`label_format`、`image_type`、`label_stock_type`、`label_file_name`、`label_content_type`、
  `label_parts[]`（面单存档里各文档的来源、箱号、类型、页数、是否存入）、`updated_at` 等。

`POST` 可带请求体 `{"label_format": "A4" | "THERMAL"}`（面单打印方式：`A4` = 普通打印机打 A4 / Letter 纸，`THERMAL` = 热敏标签机；
不带用对接设置里的默认；其它值，或这家承运商的插件不支持 → 400 `16077`）。各打印方式用什么文件格式 / 纸张由插件决定
（FedEx 见「FedEx 插件」）。

### 建单

- 有结果不明 / 进行中的建单记录时 `POST` 直接 409 `16079`（`details.unresolved`），不调承运商。
- **前置条件**（不满足的原因一次列全；`POST` 返回 409 `16072`，`details.blockers`）：
  - 承运商与对接：没有发货任务（`DELIVERY_TASK_MISSING`）/ 任务已完成（`DELIVERY_TASK_COMPLETED`）、任务和 DN 都没设承运商
    （`CARRIER_NOT_SET`）、承运商没设对接或不属于 DN 所属公司（`CARRIER_NOT_INTEGRATED`，带 `carrier_id`）、插件没装
    （`CARRIER_INTEGRATION_UNAVAILABLE`）、对接已停用（`CARRIER_INTEGRATION_DISABLED`）、必填设置 / 密钥缺
    （`CARRIER_NOT_CONFIGURED`，`fields` 列出缺的键）、设置取值不合法（`CARRIER_CONFIG_INVALID`）；
  - DN：非海外 DN（`NOT_EXPORT`，只报这一条）、不是 `packed`（`NOT_PACKED`）或已发货（`DN_SHIPPED`）、已有有效运单
    （`SHIPMENT_EXISTS`）或已手工存过运单号（`TRACKING_NUMBER_EXISTS`）、没有箱子（`PACKAGES_MISSING`）/ 超过插件的箱数上限
    （`TOO_MANY_PACKAGES`，带 `limit`）/ 箱子缺重量或尺寸（`PACKAGE_INCOMPLETE`）、报关视图有错误级问题（码同报关视图）、
    收件人姓名和公司都缺（`RECIPIENT_NAME_MISSING`）或电话缺（`RECIPIENT_PHONE_MISSING`）；
  - 插件自己的检查（地址放不放得下、贸易条件与关税付款方是否矛盾……；FedEx 见「FedEx 插件」）。
- **运送申告价额**高于已打包货值时自动压到已打包货值（承运商拒收申告价额高于报关货值的运单，FedEx 已实测；部分打包时只保
  实际发出的货）。实际提交的值记在 `shipment.declared_value`；`warnings` 里给 `{code: "DECLARED_VALUE_CAPPED", message,
  requested, applied}`（`GET` 在没有有效运单时作预告，`POST` 响应里是这次实际做的）。
- 没有当前有效的 CI / PL（或已过期）时先按现有逻辑签发。
- 插件开了电子单证（FedEx 为 `etd_enabled`）时，先把当前 CI PDF 上传给承运商，建单时引用；没开时仓库照旧打印 CI 随货。
- 交给插件的数据（与承运商无关，形状见 `CarrierAdapter`）：发件人 = 公司出口资料（英文名、电话（仓库优先）、联系人、税号）
  + 仓库英文地址（仓库没有则用公司的；是一个英文字符串，怎么拆行由插件决定）；收件人与税号 / 税号类型来自报关快照；
  商品来自发票明细（**已打包数量**、HS、原产国（商品主数据）、单价、金额、重量）；每个 WMS 箱子一个包裹（毛重 kg、尺寸 mm）；
  运送申告价额按箱均分；运费 / 保险费按快照；DN 订单号作参考号。
- 商品重量：有单件重量的 = 单件重量 × 已打包数量；没有的分摊「总毛重 − 已知重量」（不为正时按数量占比分摊总毛重）。
- 有有效运单时发货任务的运单号锁定为该运单号：完成发货、保存运单号、修改发货任务传了别的号码 → 409 `16078`
  （要换先取消运单；完成发货 / 修改任务时传空值视为不改）。号码比对忽略空格 / 连字符，一致时入库存运单上的标准写法。
  完成发货 / 修改任务 / 保存运单号传的 `carrier_id` 不是建这张运单的承运商也 409 `16078`，`details` 另带请求里的
  `carrier_id`：按承运商记录的 id 比，同 code 的另一条承运商记录也不行（迁移前没有 `carrier_id` 的老记录按 code /
  对接插件比）；`carrier_id: null` 视为不改。
  本 DN 已取消（含补偿取消）的自动运单号不能再保存或用来完成发货 → 409 `16078`
  `details {tracking_number: null, cancelled_tracking_number, carrier, status: "cancelled"}`。有结果不明 / 进行中的建单记录时，保存运单号或修改发货任务
  带了号码 → 409 `16079`（防止操作员又在承运商网站手工建一张；清空照常）。运单取消中（`cancelling`）时保存运单号
  （含同一个号码）或清空都 409 `16079`（修改任务时传空值视为不改）。
- 有进行中 / 有效 / 结果不明 / 取消中的自动运单时：新建或删除该 DN 的发货任务 → 409 `16078`（新任务会成为当前发货任务、
  绕过运单号锁定；新建时带的运单号也按上面的规则校验）；改报关数据（`PUT /warehouse/dn/<id>/customs`）或箱子
  → 409 `16076`（数据已随运单提交；`details: {reason: "CARRIER_SHIPMENT_OPEN", status, carrier, carrier_shipment:
  {id, status, tracking_number}}`）；签发数据有变化的单证 → 409 `16076`（`reason: "DOCUMENT_DATA_CHANGED"`，见「出口单证」）。
  先取消运单，结果不明的先处理。上述检查前都先锁 DN 行，与建单 / 取消串行。
- **分三段，调承运商时不占 DN 行锁、也不开着数据库事务**：
  1. 锁 DN、检查前置条件、必要时签发 CI / PL，插一条 `pending` 的 `dn_carrier_shipments` 记录（`carrier` = 插件代码，
     `carrier_id` = 承运商）并提交（部分唯一索引保证同一 DN 至多一条 `pending` / `active` / `unknown` / `cancelling`）；
  2. （开了电子单证时先上传 CI）调插件建单。整个建单受 `CARRIER_CREATE_BUDGET_SECONDS`（默认 90 秒）总时限约束：
     每次请求（取令牌 / 上传单证 / 建单）的连接 / 读取超时按剩余时间收缩，剩余不足 5 秒就不发下一个请求
     （→ 504 `16074`，`details.budget_exhausted: true`，记录 `failed`），避免 worker 超时被杀；
  3. 成功后先把运单号单独提交到记录上，再在一个事务里：记录置 `active`、运单号 → 发货任务（现有保存运单号逻辑；任务没设
     承运商时一并写上建单的承运商）、再过一遍 CI / PL 签发（不印运单号，数据没变时不出新版本；建单期间单证数据被改了
     → `16076`，按写库失败处理）、插件生成的面单存档存为 `doc_type: shipping_label` 的单证。
- **承运商出错**（`details` 都带 `maybe_processed` 与 `unresolved`；插件按这个口径归类，见「编写承运商插件」）：
  - 明确拒绝（4xx）→ 502 `16073`（`details.errors`、`transaction_id`），记录 `failed`（`reason: rejected`）；
    请求没发出去（连接没建立、取访问令牌失败等）→ 记录 `failed`（`not_sent`），连接超时为 504 `16074`；
    电子单证上传失败 → `failed`（`etd_upload_failed`）。这些情况确定没有运单，可以直接重试。401 / 403 另带
    `details.permission_denied: true`（密钥不对，或承运商账号没开通这个接口）。
  - 结果不明：读超时 → 504 `16074`；连接建立后中断、5xx、200 但响应看不懂 → 502 `16073`。
    记录 `unknown`（`reason`: `timeout` / `connection_error` / `server_error` / `bad_response`），`maybe_processed: true`，
    `details.unresolved` 为该记录。承运商侧可能已建单：先去承运商的发货后台（`integration.portal_name`）确认，有就在那边
    取消，再确认作废（见下）。
  - 响应看不懂但能找到运单号：按写库失败处理（取消该运单）。
- **承运商建单成功但写库失败**（502 `16095`，期间 DN 被发货、单证数据被改等业务异常也一样；`details {carrier, tracking_number, transaction_id,
  error: {code, message}, compensation: {attempted, cancelled, cancel_transaction_id, error}, unresolved,
  maybe_processed}`）：WMS 先另开事务重读记录——已是 `active` 且运单号相同（提交其实成功、只是调用方收到了异常）→ 保留运单、
  按成功返回（201）；读不到记录 → **不取消**，记录落 `unknown`（`reason: save_unconfirmed`，带 `tracking_number`）；
  确实没写进去 → 向承运商取消该运单（补偿），结果单独提交到记录（原事务回滚后仍留痕）：承运商确认取消（或回「已取消 /
  查无此运单」）→ `cancelled`（`reason: compensated`，`details.unresolved` 为 null）；取消失败 / 未确认 → `unknown`
  （`reason: compensation_failed`，带 `tracking_number`），再点取消（`POST …/cancel`）或确认作废。
- 第 1 段签发的 CI / PL 随 `pending` 记录提交：承运商失败时照常有效（不再回滚）。
- 面单用 `GET /warehouse/dn/<id>/customs-documents/<label_document_id>/file` 下载（PDF inline，ZPL / EPL 等其它格式作为附件）；
  面单不参与「必须有有效 CI + PL」的发货拦截。

### 建单记录与结果不明

每次建单请求在 `dn_carrier_shipments` 留一条记录：

| `status` | 含义 |
|----------|------|
| `pending` | 正在请求承运商 |
| `active` | 有效运单 |
| `cancelling` | 有效运单正在取消（`reason: cancel_in_progress`），或取消结果不明（`reason`: `timeout` / `connection_error` / `server_error` / `bad_response` / `save_failed` / `interrupted`；`cancel_in_progress` 超过 `CARRIER_PENDING_STALE_MINUTES` 按 `stale`）。运单号与面单都还在，不能发货 |
| `cancelled` | 已取消（手工取消；或写库失败后自动取消，`reason: compensated`；承运商回已取消，`reason: already_cancelled`） |
| `failed` | 承运商明确拒绝 / 请求没发出去，确定没有运单（`reason`: `rejected` / `not_sent` / `etd_upload_failed` / `budget_exhausted` / `interrupted`） |
| `unknown` | 结果不明，承运商侧可能有运单（`reason`: `timeout` / `connection_error` / `server_error` / `bad_response` / `compensation_failed` / `save_unconfirmed` / `interrupted`） |
| `dismissed` | 操作员确认承运商那边没有这张运单（或已手工取消）；带运单号的须承运商确认已取消 / 查无此运单 |

- `pending` 超过 `CARRIER_PENDING_STALE_MINUTES`（默认 10 分钟；实际不短于建单总时限 + 75 秒）读取时按
  `unknown`（`reason: stale`）处理（例如 worker 被杀）。worker 超时被杀（gunicorn 抛 `SystemExit`）时也会尽力把记录落成
  `unknown` / `failed`（`reason: interrupted`）。
- 有 `unresolved`（`pending` / `unknown` / `cancelling`）时：不能建单（409 `16079`）、不能存 / 改运单号（409 `16079`）、
  不能完成发货（409 `16079`）、不能改报关数据 / 箱子、不能签发数据有变化的单证（409 `16076`）、不能新建 / 删除发货任务
  （409 `16078`）。
- 带运单号的 `unknown`（例如 `compensation_failed`）可以 `POST …/cancel` 重试取消（规则同「取消」，成功后为 `cancelled`）。
- **确认作废**：`POST /warehouse/dn/<id>/carrier-shipment/dismiss`，请求体 `{"confirm": true, "unresolved_id": <id>}`
  （`confirm` 缺或不是 `true` → 400，`field: confirm`；`unresolved_id` 可不带，不是正整数 → 400）。只在 `can_dismiss`
  时可用：`unresolved.status == "unknown"`（含过期的 `pending`），或 `cancelling` 且没有取消请求正在进行。
  - 没有运单号：不调承运商 → `dismissed`，记 `dismissed_by` / `dismissed_at`。
  - 带运单号（承运商那边可能真有这张运单）：先向承运商取消（三段，调承运商时不占 DN 行锁），承运商取消成功或回「已取消 /
    查无此运单」才作废：`unknown` → `dismissed`（记 `cancel_transaction_id`），`cancelling` → `cancelled`（面单作废、
    运单号清掉）；否则 409 `16094` `details {unresolved, carrier, tracking_number, result: refused |
    unclear, http_status, transaction_id, errors, alerts, maybe_processed}`，先在承运商的发货后台处理。
    建单承运商的对接不可用 → 409 `16072`（`details.blockers`）。
  - 带了 `unresolved_id` 但不是当前结果不明的记录 → 409 `16093` `details {expected, actual}`（`expected` 为请求里的 id，
    `actual` 为当前记录的 id）。
  - 返回与 `GET` 同形。没有可确认作废的（或请求 / 取消还在进行中）→ 409 `16075`（`details.unresolved`）。之后可以重新建单
    或手工存运单号。

### 取消

`POST /warehouse/dn/<id>/carrier-shipment/cancel`，请求体可带 `{"tracking_number": "..."}`（操作员看到的、要取消的运单号，
即 `shipment.tracking_number` / `unresolved.tracking_number`）：忽略空格 / 连字符后与当前运单不同 → 409 `16093`
`details {expected, actual}`（`expected` 为请求里的号码，`actual` 为当前的号码）；不是字符串 → 400；不带照旧。

DN 发货前可取消（发货后 409 `16065`；没有可取消的 409 `16075`，`details.unresolved`），对象是有效运单、`cancelling`
（重试）或带运单号的 `unknown` 记录。调插件取消整票运单，发件国用建单时记下的 `sender_country`（迁移前的老记录按建单同一口径推）。
用的是**建这张运单的承运商**（记录的 `carrier_id`）的对接设置：只要求插件还在、设置齐全，对接停用了也能取消；不可用
→ 409 `16072`（`details.blockers`）。与建单一样**分三段，调承运商时不占 DN 行锁、也不开着事务**
（受 `CARRIER_CREATE_BUDGET_SECONDS` 约束）：

1. 锁 DN，把运单标 `cancelling`（`reason: cancel_in_progress`）并提交——此后不能发货、不能改运单号（409 `16079`），
   再点取消 → 409 `16079`；
2. 调承运商；
3. 锁 DN 落结果：
   - 已取消（承运商确认取消，或回「已取消 / 查无此运单」）→ `cancelled`、发货任务上的运单号清空（按忽略
     空格 / 连字符后的号码比）、面单作废（`void_reason: shipment_cancelled`）；CI / PL 不印运单号，
     不动。`unknown` 记录同样落 `cancelled`；
   - 承运商明确拒绝（4xx，或明确回了没取消）或请求没发出去 → 回到 `active`（`unknown` 记录仍是 `unknown`），
     502 `16073` / 504 `16074` 带原因；
   - 结果不明（读超时、连接中断、5xx、响应看不出取消了没有）→ 记录停在 `cancelling` 并记原因
     （`timeout` / `server_error` / `bad_response` / `connection_error`），502 `16073` / 504 `16074`，`details.maybe_processed:
     true`、`details.unresolved`。发货一直拦着，重试取消（或在承运商的发货后台核对后确认作废）直到承运商给出明确结果。

第 3 段写库失败或 worker 被杀时，记录停在 `cancelling`（`reason: save_failed` / `interrupted`；`cancel_in_progress` 超过
`CARRIER_PENDING_STALE_MINUTES` 按 `stale`），可以马上再点取消。

承运商回「已取消 / 查无此运单」视为取消成功（`reason: already_cancelled`），例如上次取消在承运商那边成功了但本地写库失败，
再点一次取消就能收口。怎么认由插件判断（FedEx 的规则见「FedEx 插件」）。

### 编写承运商插件

一家承运商 = `CarrierAdapter`（`warehouse/carrier/integrations/base.py`）的一个子类，放在 `warehouse/carrier/integrations/<code>/`
或独立的 Python 包里；内置的 FedEx（`integrations/fedex/`）可作范本。分工：

- **WMS**（`warehouse/dn/carrier_services.py`，与承运商无关）：通用前置条件、建单记录的状态机、结果不明 / 补偿取消 / 确认作废、
  时间预算、建单前签发 CI / PL、运单号写回发货任务、面单存档入库，以及对接设置的保存、加密与表单校验。
- **插件**：声明设置项；检查一票货能不能交给这家承运商；组请求、调 API（含认证）、解析响应；把失败归类成 `CarrierError`；
  生成面单文件；取消；测试连接。

类属性：

| 属性 | 说明 |
|------|------|
| `code` | 插件代码（必填，小写，≤ 30 字符），存进 `carrier_integrations.adapter` 与建单记录的 `carrier` |
| `name` | 显示名（必填） |
| `portal_name` / `portal_url` | 承运商的发货后台，结果不明时提示操作员去那里核对 / 取消 |
| `fields` | 设置项，`setting(...)` 的元组 |
| `label_formats` | 支持的面单打印方式，`('A4', 'THERMAL')` 的子集；默认两者都支持 |
| `max_packages` | 一票最多几箱（超出 → `TOO_MANY_PACKAGES`），`None` = 不限 |

设置项 `setting(key, kind, *, label, required=False, default=None, options=None, help=None, group='account', placeholder=None)`：

- `kind`：`text` / `secret`（加密保存、接口不回显）/ `select` / `boolean`。
- `label` / `help` 按语言给 `{'en-US': …, 'zh-CN': …, 'ja-JP': …}`，界面按当前语言取，没有用 `en-US`；`select` 的 `options`
  用 `option(value, en, zh=None, ja=None)` 生成；`group`：`account` / `shipping` / `label`（表单分组）。
- 实例上有 `self.settings`（非密钥，空值已换成 `default`）与 `self.secrets`。保存时 `select` 须在选项里、`boolean` 须为布尔值，
  否则 400 `16124`；必填项缺 → `CARRIER_NOT_CONFIGURED`；更多校验覆盖 `setting_problems()`（返回英文说明 → `CARRIER_CONFIG_INVALID`）。
- 约定的键：`default_label_format`（建单不指定时的打印方式）、`service_type`（记到建单记录上，`service_name()` 给显示名）。

方法（`shipment` 是 WMS 交给插件的一票货，纯数据：`reference`、`ship_date`、`shipper`、`recipient`、`packages[]`、
`commodities[]`、`invoice`、`documents`，字段见 `CarrierAdapter` 的文档字符串）：

| 方法 | 约定 |
|------|------|
| `check_shipment(shipment)` → `[{code, message, field?}]` | 这票货交给这家承运商的问题（地址放不下、贸易条件与关税付款方矛盾……），并入 blockers。每次 `GET` 都会调，不要联网；`shipper` / `recipient` 可能为 `None`（出口资料不全等，已有别的 blocker），`ship_date` / `documents` 这时还没有 |
| `label_options(label_format)` → `{label_format, image_type, stock_type, page_size}` | 打印方式 → 文件格式（`PDF` / `PNG` / `ZPLII` / `EPL2`）与纸张；记到建单记录上，再原样传给 `create_shipment` / `label_archive` |
| `documents_enabled()` → `bool` | 为真时 WMS 把当前 CI 放进 `shipment.documents = {commercial_invoice: {content, file_name}}`，建单前调 `upload_documents(shipment, *, deadline)` → 文档 ID，作为 `document_ref` 传给建单 |
| `create_shipment(shipment, label, *, deadline, document_ref=None)` → `{tracking_number, package_tracking_numbers, service_type, ship_date, net_charge, currency, documents, transaction_id, alerts}` | 建单；`net_charge` 为 `Decimal` 或 `None`，`documents` 原样交给 `label_archive` |
| `label_archive(documents, label)` → `{content, extension, parts}` | 合成一个面单文件；`extension` 为 `pdf` 时下载 inline，其它作为附件；`parts` 存到 `shipment.label_parts`。抛异常按写库失败处理（补偿取消） |
| `cancel_shipment(tracking_number, *, sender_country, deadline)` → `{result, reason, problem, transaction_id, alerts}` | 取消整票。`result`：`confirmed`（承运商回「已取消 / 查无此运单」也算，`reason: already_cancelled`）/ `refused`（明确没取消，`problem` 写原因）/ `unclear`（回了但看不出，`reason: bad_response`）。请求本身失败抛 `CarrierError` |
| `test_connection(*, deadline=None)` → `dict` | 用当前设置认证一次（不建单），返回值并入 `{ok: true, message}`；失败抛 `CarrierError` |

失败一律抛 `CarrierError(message, *, step, status_code, errors=[{code, message}], transaction_id, timeout, maybe_processed,
budget_exhausted)`，WMS 按标志位处理：

- `maybe_processed=True`：承运商可能已经处理了请求（读超时、连接建立后中断、5xx、响应看不懂）→ 建单记 `unknown`、取消停在
  `cancelling`。否则是确定失败：`step='create'` 且带 `status_code`（4xx）→ `failed`（`rejected`），其余（取令牌失败、连接没建立）
  → `failed`（`not_sent`）；取消时回到 `active`。
- `timeout=True` → 504 `16074`，其余 502 `16073`；`status_code` 为 401 / 403 → `details.permission_denied`。
- HTTP 200 但取不到运单号 → 抛 `CarrierResponseError(message, tracking_number=…, transaction_id=…)`：带运单号 WMS 会取消它
  （补偿），不带记 `unknown`（`bad_response`）。
- 异常信息和日志里不要带凭证。

每次 HTTP 请求前用 `request_timeout(deadline, carrier=…, what=…, step=…)` 取 `(连接超时, 读取超时)`：读
`CARRIER_CONNECT_TIMEOUT_SECONDS` / `CARRIER_TIMEOUT_SECONDS`，按 `deadline` 的剩余时间收缩，剩余不足 5 秒抛
`CarrierError(budget_exhausted=True)`（请求不发）。

注册：内置插件加到 `warehouse/carrier/integrations/__init__.py` 的 `_BUILTIN`（`'warehouse.carrier.integrations.<code>:<类名>'`）；
独立的包在 `pyproject.toml` 里声明 entry point，装进 WMS 的虚拟环境后重启：

```toml
[project.entry-points."goodsmart_wms.carrier_integrations"]
dhl = "my_package.dhl:DhlAdapter"
```

注册表在进程里第一次用到时加载并缓存。不是带 `code` / `name` 的 `CarrierAdapter` 子类、或加载出错的插件记日志后跳过；
`code` 重复时内置的优先。

### FedEx 插件

`warehouse/carrier/integrations/fedex/`：`adapter.py`（设置项、检查、取消结果判断）、`client.py`（HTTP、OAuth）、`shipment.py`
（请求组装 / 响应解析 / 面单存档，纯函数）。用 Ship API（建单 / 取消）、Trade Documents Upload API（ETD）与 OAuth（client
credentials）。不要求承运商 code 为 `fedex`：哪条承运商记录设了 FedEx 对接就用哪条。

| 设置项 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `environment` | select，必填 | `sandbox` | `sandbox`（`https://apis-sandbox.fedex.com`，不发货不计费）/ `production`（`https://apis.fedex.com`）；Trade Documents Upload 的地址随之切换 |
| `account_number` | text，必填 | | FedEx 账号，运费记在这个账号上 |
| `api_key` / `secret_key` | secret，必填 | | FedEx 开发者项目凭证 |
| `service_type` | select | `INTERNATIONAL_ECONOMY` | 另有 `INTERNATIONAL_PRIORITY`、`FEDEX_INTERNATIONAL_PRIORITY`、`FEDEX_INTERNATIONAL_PRIORITY_EXPRESS`、`FEDEX_INTERNATIONAL_CONNECT_PLUS` |
| `pickup_type` | select | `CONTACT_FEDEX_TO_SCHEDULE` | 每次在 WMS 预约揽收（见「上门揽收」）；另有 `USE_SCHEDULED_PICKUP`（签了定期集荷）、`DROPOFF_AT_FEDEX_LOCATION`（自己送网点） |
| `duties_payment_type` | select | `RECIPIENT` | `SENDER` = 关税记到账号上（贸易条件 DDP 时必须选） |
| `etd_enabled` | boolean | `false` | 电子贸易单证（ETD）：建单前把 CI 上传给 FedEx，建单时以 `ELECTRONIC_TRADE_DOCUMENTS` 引用；账号须已开通 ETD |
| `default_label_format` | select | `A4` | 建单请求不指定时的面单打印方式 |
| `thermal_page_size` | select | `100x150` | `THERMAL` PDF 存档的页面（mm）：`100x150`，或 `101.6x152.4`（真 4 × 6 英寸纸，不缩放） |
| `thermal_image_type` | select | `PDF` | `THERMAL` 的文件格式：`PDF` / `ZPLII`（Zebra 等）/ `EPL2` |

| `label_format` | 打印机 | imageType / 纸张 | 页面 |
|----------------|--------|------------------|------|
| `A4` | 激光打印机、A4 / Letter 普通纸 | `PDF` / `PAPER_85X11_TOP_HALF_LABEL` | Letter 页，上半页面单、下半页折叠说明 |
| `THERMAL` | 4 英寸面单机（走驱动当普通打印机用） | `thermal_image_type` / `STOCK_4X6` | 100 × 150 mm（FedEx 的 4 × 6 英寸页等比缩到 98.4%） |

面单机不要用 `PAPER_4X6`：FedEx 回的是 Letter 页、面单在左上角（纸张因此不开放设置）。

FedEx 的 4 × 6 英寸（101.6 × 152.4 mm）面单内容几乎铺满整页（离边 0~0.7 mm），打在 100 × 150 mm 面单纸上按实际大小会切边，所以 `THERMAL` 的 PDF 存档每页等比缩小（只缩不放）、居中放到 `thermal_page_size`（默认 `100x150`）的页面上，浏览器打印时纸张选可打印区域为 100 × 150 mm 的纸、缩放选实际大小。要缩小超过 10% 才放得下的页（例如 Letter 尺寸的单证）保持原样；ZPLII / EPL2 不处理。用真 4 × 6 英寸纸时选 `101.6x152.4`（不缩放）。

- 检查（blockers）：一票最多 30 箱（`TOO_MANY_PACKAGES`）；贸易条件为 DDP 而 `duties_payment_type` 不是 `SENDER`
  （`INCOTERM_DUTIES_MISMATCH`，`field: incoterm`）；发件 / 收件地址放不进 FedEx 格式（`SHIPPER_ADDRESS_INVALID`，
  `field: address_en` / `RECIPIENT_ADDRESS_INVALID`，`field: consignee`）。
- 地址规则：FedEx 街道最多 3 行 × 35 字符、城市 ≤ 35 字符，放不下就拦截（不截断）。英文地址按逗号 / 换行切段，去掉国家名和邮编后的最后一段
  为城市，其余各段一段一行（超过 3 行时连起来重新折行）。例：`4-5-6 Sample-cho, Chuo-ku, Osaka 600-0000, JAPAN` →
  街道 `4-5-6 Sample-cho` / `Chuo-ku`、城市 `Osaka`、邮编 `6000000`。邮编取仓库 / 公司的 `zip_code`
  （日本地址也能从地址里认出，统一发 7 位数字）。收件人没有邮编时带空的 `postalCode`；美国 / 加拿大 / 波多黎各必须有两位州代码。
- 请求：每箱一行包裹（毛重 kg 向上取两位小数、尺寸 cm 向上取整，面单印 DN 订单号与发票号）；运费记账到账号，关税付款方按
  `duties_payment_type`；收件人税号类型映射到 `tinType`：EORI → `BUSINESS_UNION`，PCCC / CPF → `PERSONAL_NATIONAL`，
  其余 → `BUSINESS_NATIONAL`；电话只留数字。
- 面单存档包含响应里的**所有**文档（每箱面单 → 辅助运单 → 其他；国际件的「FEDEX AWB COPY」在第一箱面单 PDF 的后几页，
  不用另外要）：PDF / PNG 合成一个 PDF（[pypdf](https://pypi.org/project/pypdf/)，BSD），ZPLII / EPL2 把原始打印指令按同样顺序
  拼成一个文件（`.zpl` / `.epl`，`application/octet-stream`）。
- OAuth token（`/oauth/token`，client credentials）按「API 地址 + API Key」进程内缓存到过期前 60 秒；业务请求遇 401 换一次
  token 重试。凭证不写日志、不出现在响应里。「测试连接」= 强制重新取一次 token（返回里带 `environment`）。
- 取消：`PUT /ship/v1/shipments/cancel`（`DELETE_ALL_PACKAGES`）。HTTP 200 时 `cancelledShipment: true` → 已取消，`false` →
  明确没取消，没有这个字段 → 结果不明。
- 「已取消 / 查无此运单」：FedEx Ship API 公开文档没有单列这两种情况的错误码，本仓库也未在 sandbox 实测，所以只按错误码
  （不看随语言变化的 message）判断——码里同时有 `ALREADY` 与 `CANCEL` / `DELETE`，或同时有 `TRACKING` / `SHIPMENT` 与
  `NOT FOUND` / `NOT EXIST`，以及旧版 Web Services 的 `8159`（「Shipment Delete was requested for a tracking number already in
  a deleted state」）。命中时日志记原始错误码；正式环境遇到没覆盖的码，补到 `fedex/adapter.py` 的 `ALREADY_CANCELLED_CODES`。

#### 测试环境冒烟与面单认证

`scripts/fedex_sandbox_smoke.py` 直接调 `FedexAdapter`（与 WMS 建单同一套代码，不连数据库），按面单打印方式 × 箱数在 FedEx
**测试环境**各建一票运单，面单按 `<服务>_<箱数>pkg_<打印方式>.<扩展名>` 保存后取消（`--keep` 不取消）。只连 sandbox。
凭证只给这个脚本用，从环境变量读：`FEDEX_API_KEY` / `FEDEX_SECRET_KEY` / `FEDEX_ACCOUNT_NUMBER`，可选 `FEDEX_SERVICE_TYPE` /
`FEDEX_PICKUP_TYPE` / `FEDEX_DUTIES_PAYMENT_TYPE` / `FEDEX_LABEL_THERMAL_IMAGE_TYPE` / `FEDEX_LABEL_THERMAL_PAGE_SIZE`
（`--env-file` 只读入某个 .env 里的这些项）。发件人 / 收件人默认虚构，认证样张可用 JSON 文件给出（字段同出口资料 /
报关快照 consignee）：

```bash
python scripts/fedex_sandbox_smoke.py [--label-format A4|THERMAL|both] [--packages 1,2] [--to US|DE|HK] \
    [--shipper-json shipper.json] [--recipient-json recipient.json] [--etd] [--out ./fedex-sandbox-labels] \
    [--env-file .env] [--keep]
```

正式使用前 FedEx 要求面单认证：用测试环境打出所用服务的面单，扫描后连同 Label Cover Sheet 发给 FedEx
（见 developer.fedex.com → Certification），通过后在承运商的 FedEx 对接里把 `environment` 改成 `production`、换上正式环境的
凭证（可先「测试连接」）。

### 上门揽收

没有和承运商签定期集荷时，运单建好后要另约快递员上门（FedEx：建运单时 `pickupType` 用 `pickup_type` 设置的默认值
`CONTACT_FEDEX_TO_SCHEDULE`，揽收走 FedEx Pickup Request API，FedEx 开发者项目里要开通该 API）。上门揽收是插件能力
（`CarrierAdapter.supports_pickup`，内置 FedEx 插件支持），用所约承运商的对接设置。揽收按「当前仓库（`X-WAREHOUSE-ID`，必带）
+ 承运商 + 日期」约，一次带多张该承运商的运单，件数 / 总重量按所带运单的箱子（`dn_packages` 毛重）合计。记录在
`carrier_pickups`（`carrier` = 插件代码，`carrier_id` = 承运商），所带运单的 `dn_carrier_shipments.pickup_id` 指向它
（`shipments` 另存快照）。

| 接口 | 说明 |
|------|------|
| `GET /warehouse/dn/carrier-pickups?carrier_id=` | 本公司能预约揽收的承运商（`carriers`，不带 `carrier_id` 用第一个）、该承运商的待揽收运单（`active`、DN 未发货；`can_schedule`）、最近 14 天的预约（结果不明的一律列出）、前置条件 `blockers`、揽收地点 |
| `GET /warehouse/dn/carrier-pickups/availability?carrier_id=` | 只查询：承运商开放的揽收日与可选时间（`options[{pickup_date, cutoff_time, access_minutes, ready_times, latest_times, default_ready_time, default_latest_time}]`） |
| `POST /warehouse/dn/carrier-pickups` | `{carrier_id?, shipment_ids, pickup_date, ready_time, close_time, remarks?, notification_email?}` → 201 `{pickup, alerts, message}` |
| `POST /warehouse/dn/carrier-pickups/<id>/cancel` | 取消已约上、揽收日没过的预约 |
| `POST /warehouse/dn/carrier-pickups/<id>/dismiss` | `{confirm: true}`：结果不明的预约向承运商核对后作废 |

- 权限与自动运单相同：读 `dn_read` / `packing_read` / `delivery_read`，写 `packing_edit` / `delivery_edit`。
- **揽收地点**：地址 = 仓库英文地址（没有用公司的），联系人 = 仓库联系人（没有用公司出口联系人），电话 = 仓库电话（没有用公司的），
  公司名 = 公司英文名，由插件转成承运商的格式（FedEx：与建运单的发件地址同一套拆分规则，电话须 8～13 位数字）。
  前置条件（409 `16130`）：`PICKUP_CARRIER_MISSING` / `CARRIER_NOT_INTEGRATED` / `PICKUP_NOT_SUPPORTED` / 与自动运单相同的
  对接问题（`CARRIER_INTEGRATION_DISABLED`、`CARRIER_NOT_CONFIGURED` 等）/ `PICKUP_ADDRESS_MISSING` / `PICKUP_ADDRESS_INVALID` /
  `PICKUP_PHONE_INVALID`。
- **时间**：日期 / 时间都是仓库当地的（`DOCUMENT_TIMEZONE`，默认 `Asia/Tokyo`）。FedEx：`readyDateTimestamp` 带时区偏移
  （`2026-10-22T12:00:00+09:00`，FedEx 示例集里日本的写法），`customerCloseTime` 为当地 `HH:MM:SS`；揽收日是今天时
  `pickupDateType` = `SAME_DAY`，否则 `FUTURE_DAY`。承运商返回的截止时间（准备好时间不能更晚）与最短间隔由前端用来限制下拉选项，
  后端只校验先后、日期范围（今天起 30 天内）与插件的留言长度（`max_remarks`），其余由承运商判断。
- **被占着的运单**不能再约（409 `16132`，`reason: ALREADY_SCHEDULED`）：`pending` / `unknown` 的预约，或 `scheduled` 且揽收日没过。
  揽收日过了、DN 还没发货的运单可以重新预约（快递员没来 / 忘了完成发货）。别的承运商的运单：`reason: OTHER_CARRIER`。
- **预约**与建运单一样分三段，调承运商时不占行锁：锁所选运单 → 插 `pending` 记录并占住运单、提交 → 调承运商（受
  `CARRIER_CREATE_BUDGET_SECONDS` 约束）→ 约上 `scheduled`（`confirmation_code` / `location_code`）；明确拒绝 / 没发出去 →
  `failed`，运单放开（502 `16073` / 504 `16074`，`details.pickup`）；结果不明（超时、5xx、没有确认号）→ `unknown`，运单仍被占着，
  操作员查确认邮件 / 致电承运商后 `dismiss` 才放开。`pending` 超过 `CARRIER_PENDING_STALE_MINUTES` 按 `unknown`（`stale`）。
- **取消**：`scheduled` 且揽收日没过（否则 409 `16133`），用预约时的承运商的对接设置（停用了也能取消；删掉了 → 409 `16130`）。
  取消成功或回「已取消」（FedEx 的判断规则同运单取消）→ `cancelled`，运单放开；明确拒绝 → 仍 `scheduled`（`reason: cancel_refused`）；
  结果不明 → 仍 `scheduled`（`reason: cancel_unclear`），可重试。
- FedEx 确认邮件（`notification_email`）用 `en_US`；运单状态接口的 `shipment.pickup` 给出这张运单的揽收概要。

### 跟踪（揽收后自动发货、送达后自动签收）

运单是 WMS 建的，所以由 WMS 查承运商跟踪（插件能力 `CarrierAdapter.supports_tracking` + `track()`，内置 FedEx 插件用
Track API，FedEx 开发者项目里要开通 Track API）。实现在 `warehouse/dn/tracking_services.py`，与具体承运商无关：

- **查哪些**：`active` 的自动运单、有运单号、DN 是 `packed` / `delivered`、建单不超过 `CARRIER_TRACKING_MAX_DAYS` 天。
  定时任务每 `CARRIER_TRACKING_INTERVAL_MINUTES` 分钟查一轮（按承运商分组、一次最多 30 个单号，整轮受
  `CARRIER_TRACKING_BUDGET_SECONDS` 约束），调承运商时不占行锁。对接停用了照样查（已建的运单要跟到底）。
- **自动发货**：承运商已经经手（插件给出 `picked_up_at`，或状态是 `in_transit` / `out_for_delivery` / `delivered`）而 DN 还是
  `packed` → 以建运单的人的名义完成发货（`pending` 的发货任务先开始；单证、运单、计划发货日等校验与手动「完成发货」相同），推
  `dn.delivered`。
- **自动签收**：跟踪是 `delivered` 而 DN 是 `delivered` → 签收发货任务（签收时间 = 承运商的送达时刻，换成
  服务器本地时间，与 WMS 其他时间列同一口径），DN 完成，推 `dn.completed`。
- 做不成（如 16061 计划发货日在后、16069 单证过期）把原因记在运单的 `tracking_problem`（配送页显示），人处理后下一轮再试；
  做成了清空。手动完成发货 / 签收照常可用，谁先到算谁。
- **跟踪结果**存在 `dn_carrier_shipments.tracking_*`，运单状态接口的 `shipment.tracking`：
  `{status, code, status_text, picked_up_at, delivered_at, estimated_delivery, events[], error, synced_at, problem}`。
  `status` 是归一化的 `label_created` / `in_transit` / `out_for_delivery` / `delivered` / `exception` / `unknown`
  （`code` 是承运商自己的码，如 FedEx `OC` / `PU` / `DL`；认不出的码一律 `unknown`，不会触发自动发货 / 签收）。
  查询失败只记 `error`，保留上一次的结果。
- 状态 / 扫描记录有变化时推 `dn.tracking_updated`（`{dn_id, order_number, carrier, tracking_number, tracking{…}}`，
  `dedupe_key = dn:<id>`：没送出去的旧更新被新的取代，只送最新一条）。先自动发货 / 签收、后推跟踪。
- `POST /warehouse/dn/<id>/carrier-shipment/track`（`packing_edit` / `delivery_edit`）立即查一次并照样处理，回运单状态 +
  `tracking_sync {tracked, updated, shipped, signed, problems, errors}`；没有可跟踪的运单 → 409 `16134`。
  对接信息里的 `integration.tracks_shipments` 表示能跟踪。

### 配置

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `CARRIER_CONNECT_TIMEOUT_SECONDS` / `CARRIER_TIMEOUT_SECONDS` | `5` / `30` | 单次请求的连接 / 读取超时（秒）；建单 / 取消时再按剩余总时限收缩 |
| `CARRIER_CREATE_BUDGET_SECONDS` | `90` | 建单总时限（取令牌 + 电子单证上传 + 建单，最少 10 秒）；要小于 gunicorn / nginx 超时（例如 120 秒）。取消也按这个时限 |
| `CARRIER_PENDING_STALE_MINUTES` | `10` | `pending` 超过多少分钟按结果不明（`stale`）处理；实际不短于建单总时限 + 75 秒 |
| `CARRIER_TRACKING_INTERVAL_MINUTES` | `30` | 跟踪定时任务的间隔（分钟），`0` = 不跑 |
| `CARRIER_TRACKING_MAX_DAYS` | `60` | 只跟建单不超过这么多天的运单 |
| `CARRIER_TRACKING_BUDGET_SECONDS` | `120` | 一轮跟踪查询的总时限（秒） |

账号、密钥、服务等按承运商存在对接设置里，不用环境变量；密钥用 `SETTINGS_ENCRYPTION_KEY` 加密（生产环境必须设置，见「快速开始」）。

## 部署

### 生产自动部署（GitHub Actions）

推送到 `main` 触发 `.github/workflows/deploy.yml`（同一时间只跑一个，`concurrency: deploy-backend`）：

1. 服务器工作区不干净（`git status --porcelain` 非空）→ 中止，什么都不动；部署前先探测一次当前服务，完全没有 HTTP 响应也中止（手动触发时可勾选 `skip_precheck` 跳过）
2. 记下部署前的提交，`fetch` + `reset --keep` → `pip install` → `flask db upgrade`（`lock_timeout=15s`），任一步失败都回到部署前的提交、不重启。迁移整条链在一个事务里，PostgreSQL 上失败会整体回滚
3. 重启后探测 `GET /health`（不鉴权、不查库，返回 `{status, commit}`），要求 `commit` 等于新的短哈希；失败就回滚代码、重启旧代码（**数据库不回滚**，所以迁移要保持旧代码也能跑：先加后删）

探测地址默认 `http://127.0.0.1:5002/health`，用仓库变量 `BACKEND_HEALTH_URL` 覆盖。pm2 进程要能运行 `git`（`/health` 取 commit），否则返回 `unknown`、部署被判失败。

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

出口单证相关业务码：`14019` 出口资料文本超长、`14020` 国家代码不合法、`16063` 报关结构不合法、`16064` 报关行商品编码不在 DN 明细或重复、`16065` 已发货不可改（409）、`16066` 箱子数据不合法、`16067` 当前状态不能改箱子（409）、`16068` 单证条件不全（409，`details.problems`）、`16069` 发货前单证缺失或已过期（409，`details {missing_documents, outdated}`）、`16070` 不是海外单（409）、`16071` 单证不存在（404）、`16117` 单证排不下——某块文字（通常是收件人信息）一页放不下，请缩短（409，`details {document, reason: LAYOUT_OVERFLOW}`）。承运商运单（自动建运单）：`16072` 建单 / 取消的前置条件不满足（409，`details.blockers`）、`16073` 承运商返回错误或结果不明（502，`details.errors` / `transaction_id` / `maybe_processed`）、`16074` 承运商超时或时间预算用完（504，`details.budget_exhausted`）、`16075` 没有可取消 / 可确认作废的运单记录（409，`details.unresolved`）、`16076` 有进行中 / 有效 / 结果不明 / 取消中的运单时不能改箱子或报关数据，也不能签发数据有变化的单证（409，`details {reason: CARRIER_SHIPMENT_OPEN | DOCUMENT_DATA_CHANGED, status, carrier, carrier_shipment {id, status, tracking_number}}`）、`16077` `label_format` 不合法或该承运商不支持（400）、`16078` 运单号 / 承运商与有效的自动运单不一致，或有自动运单时新建 / 删除发货任务（409）、`16079` 有结果不明 / 进行中的建单记录，或运单正在取消 / 取消结果不明——不能建单、存运单号、发货（409，`details.unresolved`）、`16093` 取消 / 确认作废请求里的目标与当前记录不符（409，`details {expected, actual}`）、`16094` 确认作废带运单号的记录时承运商没有确认已取消 / 不存在（409）、`16095` 承运商已建好运单但 WMS 写库失败，已补偿处理（502，`details {error, compensation, unresolved, …}`）。承运商在线对接：`16124` 对接设置不合法（400，`field`：`adapter` / `enabled` / `settings.<key>` / `secrets.<key>`）、`16125` 承运商还有未结束的自动运单，不能删除对接或换插件（409，`details.shipments`）。上门揽收：`16130` 前置条件不满足（409，`details.blockers`）、`16131` 预约参数不合法（400，`field`）、`16132` 所选运单不能预约（409，`details.shipments[{shipment_id, tracking_number, reason}]`）、`16133` 当前状态不能取消 / 作废（409，`details {pickup_id, status, reason, pickup_date}`）；跟踪：`16134` 没有可跟踪的自动运单（409）；承运商报错 / 超时同运单（`16073` / `16074`，`details.action` 为 `pickup` / `pickup_cancel` / `pickup_availability`）。已不再使用（单证不再印运单号和承运商）：`16080`（原：完成发货时运单号与 CI 上印的 AWB 不一致）、`16092`（原：完成发货时承运商与 CI 上印的不一致）。发货任务：`16090` DN 已有未完成的发货任务（409，`details {delivery_task_id, status}`）、`16091` 海外 DN 不是当前发货任务（409，`details {delivery_task_id, current_status}`）。错误响应可能带结构化的 `details`。

作业流程（见上文「作业流程规则」）：`16110` DN 有未结束的承运商运单，请先取消运单（409）、`16111` 调整单已取消（409）、`16112` 调整单当前状态不能取消（409）、`16113` 调整明细无法执行：库位上已没有该商品 / 明细没有库位（409）、`16114` 盘点单有未录入的明细（409，`details.detail_ids`）、`16115` 数值字段不合法（400，`field`）、`16116` 分拣任务的 ASN 不能改（409）、`16120` 移库来源库位没有这个商品（404）、`16121` 同一个 `client_batch_id` 已提交过不同的明细（409，`details {client_batch_id, batch_id}`）、`16122` 修改过调整单明细的人不能审批（409）、`16123` `client_batch_id` 不合法（400）、`44009` 库位有历史记录，请改用停用（409）。任务：`16088` 任务已停用（所属 DN / ASN 已取消，409）、`16089` 拣货一件都没拣不能完成（409）。

主数据与权限：`16100` 商品数值不合法（400）、`16101` CSV 内商品编码重复（400，带行号）、`16102` 数据值不合法（400）、`16103` 不能分配已停用的角色（400）、`16104` 只有公司管理员能新建 / 删除仓库或更换负责人（403）、`16105` 没有公司管理权限时只能管理绑定自己的 API Key（403）、`44004` 被单据或库存引用的主数据不能删除，请改用停用（409，`details {entity, id, references}`）、`44005` 与已有数据冲突（409）。

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
