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
用 `scripts/test_fast.sh`（并行，见[测试](#测试)）、`./run_tests.sh` 或 `python -m pytest tests/` 跑测试，
缺开发依赖时脚本会提示。

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
├── timezone_conversion.py     # 时刻列 timestamp → timestamptz 的转换（flask wms convert-utc，见「时区」）
├── extensions/                # Flask 扩展
│   ├── db.py                  # SQLAlchemy 数据库、UTCDateTime（时刻列，UTC）
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
│   ├── warehouse/             # 仓库管理（含仓库时区）
│   ├── common/                # 共用工具（clock.py：当前时刻、仓库时区、当地日期）
│   ├── location/              # 库位管理
│   ├── goods/                 # 商品管理
│   ├── inventory/             # 库存管理
│   ├── inventory_snapshot/    # 库存快照
│   ├── supplier/              # 供应商管理
│   ├── carrier/               # 承运商管理、在线对接插件（integrations/）、文件方式预设（presets/）
│   ├── recipient/             # 收货人管理
│   ├── asn/                   # 入库：ASN（预到货通知）
│   ├── sorting/               # 入库：分拣
│   ├── putaway/               # 入库：上架
│   ├── dn/                    # 出库：DN（发货通知）
│   ├── shipping/              # 出库：发货核心、流程汇总、发货阶段、运单服务选项
│   ├── country_rules/         # 国家规则包（报关检查、地址格式、电话写法）
│   ├── picking/               # 出库：拣货
│   ├── packing/               # 出库：打包
│   ├── delivery/              # 出库：发货
│   ├── payment/               # 出库：代收付款
│   ├── adjustment/            # 库存：库存调整
│   ├── cyclecount/            # 库存：盘点
│   ├── transfer/              # 库存：库存调拨
│   ├── removal/               # 库存：库存移除
│   └── search/                # 全局搜索（顶栏搜索框 / 搜索结果页）
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
- **角色与权限**: RBAC 细粒度权限控制（91 项内置权限）
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
- 收货规格采集：按收货通知明细采集重量、尺寸、原产国（见下文；默认不开）
- 上架至库位
- 收货权限：`PUT /warehouse/asn/<id>/receive/` 与刷新到货跟踪（`POST /warehouse/asn/<id>/track`）接受 `asn_receive` 或
  `asn_edit`；新建 / 修改 / 关闭 / 取消 / 删除收货通知及其明细仍要 `asn_edit`（`asn_delete`）。内置角色 `warehouse_operator`
  用 `asn_receive` 代替 `asn_edit`（迁移 `c4e8a1f6b2d9` 从该角色上移除 `asn_edit`；要让作业员也能建单，请手工加回）。
  分拣（含分拣完成时自动完成收货通知）只要 `sorting_edit`。

#### 收货规格采集

仓库可以要求在收货时采集商品的重量、尺寸、原产国。这是通用功能，**默认不开**（仓库没设置就不采集任何项）。

- **仓库默认规则**（`POST` / `PUT /warehouse/warehouse/<id>`，`warehouse_edit`）：`default_spec_capture_fields` ——
  `weight` / `dimensions`（长宽高三项一起）/ `origin_country` 的子集，`[]` = 不采集（默认，`null` 同）；
  `default_spec_capture_mode` —— `missing`（只补商品资料里缺的，默认）或 `remeasure`（要求的项全部重新测量）；
  `spec_capture_enforcement` —— `warn`（默认）或 `block`，分拣完成时用。取值不合法 → 400 `17020`（`field` 为字段名）。
  一次性设置脚本：`python scripts/set_warehouse_spec_capture.py`（不加 `--apply` 只预览；默认针对 `Kyoto#1` 仓库、三项全采、
  `missing`、`warn`；可用 `--warehouse-id` / `--warehouse-name` / `--company-id` / `--fields` / `--mode` / `--enforcement`）。
- **收货通知上的请求**：建单（`POST /warehouse/asn/`）与修改（`PUT /warehouse/asn/<id>`，仅 pending）时可传
  `spec_capture: {fields, mode}`。建单时没传（或对象里缺某个键）就从仓库默认值复制——是快照，之后改仓库设置不影响已建的单；
  修改时只改给了的键（`null` = 不变）。不合法 → 400 `17020`（`field`：`spec_capture` / `spec_capture.fields` /
  `spec_capture.mode`）。升级前建的收货通知不采集。收货通知的列表与详情都带 `spec_capture` 和 `spec_pending_count`。
- **待采集项**按规则算出、不落库。每行明细带 `spec_pending`（如 `["weight", "origin_country"]`；`[]` = 没有待采集），
  `GET /warehouse/asn/?spec_pending=true|false` 按 `spec_pending_count > 0` 筛选（同一规则，在 SQL 里算）：
  - `missing`：要求的项里商品资料缺的（重量为空或 ≤ 0；长宽高任一为空或 ≤ 0；原产国为空）。不论在哪里补上（这里采集、
    商品编辑、CSV 导入、别的收货通知），就不再待采集。
  - `remeasure`：这一行还没采集过 → 要求的全部项。
  - 填了跳过原因的行、已关闭的收货通知、已完成的收货通知上没到货（`actual_quantity` 为 0）的行，都没有待采集。
- **采集** `PUT /warehouse/asn/<id>/details/<detail_id>/spec`：要 `goods_edit` / `sorting_edit` / `packing_edit` 任一（与
  `PUT /warehouse/goods/<id>/origin-country` 相同）；收货通知为 `received` 时可以采集，`completed` 后也可以补采（效果相同，
  不会重推 `asn.completed`）；`pending` / `closed` → 409 `17023` `details {asn_id, status}`：

  ```json
  { "weight": 0.235, "length": 120, "width": 80, "height": 45, "origin_country": "CN",
    "note": null, "spec_source": "receiving" }
  ```

  重量 kg、尺寸 mm（长宽高要一起给，否则 400 `17022` `field: dimensions`）、原产国 ISO 3166-1 alpha-2。给了的值写回商品主数据
  ——空值 / `null` 忽略，采集不会清空资料；数值不合法 400 `16100`、国家代码不合法 400 `10014`——规格有变化就推
  `goods.spec_updated`，`source` = `spec_source`（默认 `receiving`；称重站传 `station`）。`note` 是跳过原因（文字，最多 255
  个字符；前端给预设选项：包装无原产国标识 / 无法拆封测量 / 商品破损 / 其他须填写）：要求的项里没给的就算跳过。既没有值也没有
  跳过原因 → 400 `17022`；`remeasure` 模式不填跳过原因就要给齐要求的全部项 → 400 `17022` `details {missing}`。明细记
  `spec_captured_at`、`spec_captured_by`（及 `spec_captured_by_user {id, user_name}`）与 `spec_capture_note`（之后不带原因再
  采集一次就清空）。响应是这一行明细（带最新的 `spec_pending`）。
- **分拣完成**（`PUT /warehouse/sorting/<id>/complete/`）只看本单分拣到的商品（合格 + 破损 > 0）的行：`warn` 照常完成，响应的
  `spec_pending` 列出仍待采集的行 `[{asn_detail_id, goods_id, goods_code, goods_name, spec_pending}]`；`block` 时只要还有既没
  采集、也没填跳过原因的行 → 409 `17021` `details {asn_id, enforcement: "block", lines: [同上]}`。
  `GET /warehouse/sorting/<id>` 也带同样的 `spec_pending`（现在完成会提示 / 拦截的行）；入库流程的分拣一步带
  `summary.spec_capture {fields, mode, enforcement, pending_count, sorted_pending}`。
- `asn.completed` 每行带 `spec_pending`（见「Webhook 集成」）。
- 商品列表筛选（通用）：`GET /warehouse/goods/?image_missing=true|false`（没有 `image_url`）、
  `?spec_missing=weight,dimensions,origin_country`（缺所列任一项，规则同上；不认识的项 → 400）。

### 出库（DN）
- 发货单创建与管理
- 拣货
- 打包
- 发货（含运单号；承运商设了在线对接时可自动建运单，内置 FedEx）
- 每张 DN 的发货流程（`GET /warehouse/dn/<id>/shipping-flow`）：国内 / 跨境、承运商对接方式（API / 文件 / 手工）、手填运单与
  手工揽收、没有 API 的承运商用 CSV 导出 / 导回（ヤマト B2クラウド、佐川 e飛伝Ⅲ、日本郵便 ゆうプリR 预设）、结构化国际地址、
  国家规则包、运单服务选项、自动签收——见「发货流程」
- 代收付款管理

### 库存运营
- 实时库存追踪（多阶段：ASN → 已收货 → 已分拣 → 在库 → DN → 已拣货 → 已打包 → 已发货）
- 库存调整（增减及原因追踪）
- 盘点（带审批流程）。盘点作业——开始盘点（`PUT /warehouse/cyclecount/<id>/process/`）、录入实盘数
  （`POST .../details-batch-save/`）、完成明细（`PUT .../details/<detail_id>/complete/`）、完成盘点单（`PUT .../complete/`）——
  接受 `cycle_count_execute` 或 `cycle_count_edit`；新建 / 修改盘点单、增加 / 修改明细仍要 `cycle_count_edit`。内置角色
  `warehouse_operator` 用 `cycle_count_execute` 代替 `cycle_count_edit`（迁移 `c4e8a1f6b2d9`）
- 库位间调拨
- 库存移除
- 库存快照（每日定时）
- 库存流水、按库位查看、历史库存（日终结存）、库存分析（库龄 / 周转 / ABC / 呆滞）、补货阈值批量设置——见「库存流水与库存分析」

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
  有效的出口单证作废（`void_reason: dn_cancelled`）；有未结束的承运商自动运单（`source: api`；进行中 / 有效 / 结果不明 / 取消中）
  → 409 `16110`，先在单证卡片取消运单；手填的运单随 DN 一起作废（`reason: dn_cancelled`）；`delivered` / `completed` / `closed` → 409 `16052`。`in_progress` / `picked` / `packed` 取消后推 `dn.cancelled`。
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

## 全局搜索

`GET /warehouse/search`（带尾斜杠也可）：顶栏搜索框（Ctrl K）与搜索结果页用。一个关键字同时搜单据、作业任务与主数据，按类型分组返回。代码在 `warehouse/search/`。

| 参数 | 说明 |
|------|------|
| `q` | 关键字，去首尾空白后最多 100 个字符（多的截掉）；少于 2 个字符只比单号与商品 / 库位编码（完全相同）；为空时各组 0 条 |
| `types` | 只搜这几类（逗号分隔或重复给）：`dn` `asn` `sorting` `picking` `packing` `delivery` `cyclecount` `adjustment` `goods` `location` `recipient`；不给 = 全部。不认识的 → 400 `40000`（`field: types`，`details {unknown, allowed}`） |
| `scope` | `warehouse`（默认）：当前仓库，同各列表（`X-WAREHOUSE-ID` 请求头或 `warehouse_id` 参数；不给时公司管理员 / 绑定公司的 API Key 按可访问的全部仓库，普通员工必须给 → 否则 400 `14003`）。`all`：不看当前仓库，调用方可访问的全部仓库（公司管理员 = 本公司全部仓库，普通员工 = 分配给自己的仓库，绑定公司的 API Key = 该公司全部仓库，平台管理员不受限）。其它值 → 400 `40000`（`field: scope`） |
| `limit` | 每类条数，默认 5，最多 50 |
| `page` / `per_page` | 只搜一类时分页（`per_page` 默认 = `limit`，最多 50），每条另带列表列 `columns` |
| `company_id` | 仅平台管理员：商品 / 收货方只看这家公司（同商品 / 收货方列表） |

各类型（`type` = `url_hint.page`，前端跳 `/<page>/detail/<id>`）与命中字段 `matched_field`：

| type | 搜索字段 |
|------|----------|
| `dn` | `number`（DN-163 / 纯数字）、`order_number`、`tracking_number`（当前有效运单号，去空白 / 连字符比较，同 DN 列表：完整号码，或 6 位以上的一段）、`recipient_name`、明细的 `goods_code` / `goods_name` |
| `asn` | `number`（ASN-12）、`order_number`（外部订单号）、`tracking_number`（去分隔符比较）、`supplier_name`、明细商品 |
| `sorting` | `number`（SORT-）、`asn_number`（ASN- / 纯数字）、所属 ASN 的明细商品 |
| `picking` / `packing` / `delivery` | `number`（PICK- / PACK- / DEL-）、`dn_number`（DN- / 纯数字）、所属 DN 的明细商品；`delivery` 另按 `tracking_number`（任务自己的运单号或所属 DN 的当前运单号，与 DN 列表同一口径） |
| `cyclecount` | `number`（CC-）、`task_name`、明细商品 |
| `adjustment` | `number`（ADJ- / 纯数字）、`cyclecount_number`（来源盘点单，只认 CC- 前缀，同调整单列表）、明细商品 |
| `goods` | `goods_code`（完全相同、大小写不敏感 = `exact: true`；其次前缀）、`name`（包含） |
| `location` | `location_code`（相同 / 前缀 / 包含） |
| `recipient` | `name`、`organization`（公司名）。只返回名称与城市级别信息（城市 / 州省 / 国家），不返回电话、邮箱、详细地址 |

明细商品：编码相同 / 前缀（`goods_code`）、名称包含（`goods_name`），副标题带上第一个命中的商品（编码 名称），另给结构化的
`matched_goods` `{goods_id, code, name, quantity}`（数量 = 这张单据里该商品的数量：计划量；盘点为系统数量，调整为调整量；没有按明细商品
命中时为 null）。

匹配与排序：大小写不敏感；LIKE 的 `%` `_` 按字面匹配；单号前缀的写法与各列表的关键字搜索一致（`DN-163`、`dn163`、`DN #163`）；纯数字同时试编号和运单号。组内：完整单号（带前缀）/ 完整运单号 / 商品编码完全相同（`exact: true`）→ 纯数字编号 / 关联单号 / 完全相同 → 前缀 → 包含，同档按 id 倒序。有 exact 条目的组排在前面，全部 exact 条目另在 `exact_matches` 里（总列表置顶用）。

权限与隔离：每类只在调用方有对应查看权限（`dn_read` `asn_read` `sorting_read` `picking_read` `packing_read` `delivery_read` `cycle_count_read` `adjustment_read` `goods_read` `location_read` `recipient_read`，或 `all_access` / `company_all_access`）时才搜，判断同 `permission_required`（API Key 按 key 的权限）；没有权限的类型不出现在 `types` / `counts` / `groups` 里，一类都没有 → 200 空结果（不 403）。数据范围直接用各模块列表的查询函数：公司 / 仓库隔离、默认只看启用的，与列表一致（单据 / 任务 / 库位按仓库范围，商品 / 收货方按公司）。

```json
{
  "q": "DN-163", "scope": "warehouse",
  "types": ["dn", "asn", "picking"],
  "counts": {"dn": 1, "asn": 0, "picking": 1},
  "exact_matches": [{"type": "dn", "id": 163, "...": "..."}],
  "groups": [
    {"type": "dn", "total": 1, "page": 1, "per_page": 5, "pages": 1, "items": [
      {"type": "dn", "id": 163, "code": "DN-163", "title": "Yamada Shop",
       "subtitle": "ORD-1001 · 7946 0000 0001 · Tokyo WH", "status": "delivered", "warehouse_id": 1,
       "url_hint": {"page": "dn", "id": 163}, "matched_field": "number", "exact": true,
       "highlights": [{"field": "code", "start": 0, "end": 6}], "columns": null}
    ]},
    {"type": "picking", "total": 1, "...": "..."},
    {"type": "asn", "total": 0, "page": 1, "per_page": 5, "pages": 0, "items": []}
  ]
}
```

- `counts`：本次搜的每类的命中数（0 也给）；结果页切到单类型分页时只含那一类，各类总数取不带 `types` 的那次请求。
- `title` / `subtitle`：DN 收货方 / 订单号 · 运单号 · 商品 · 仓库；ASN 供应商 / 订单号 · 运单号 · 商品 · 仓库；分拣 / 拣货 / 打包 / 发货 关联单号 / 供应商或收货方 · 订单号（发货另带运单号）· 商品 · 仓库；盘点 任务名 / 商品 · 仓库；调整 调整原因（没有用来源盘点单号）/ 来源盘点单号 · 商品 · 仓库；商品 名称 / 品牌 · 分类；库位 描述 / 仓库；收货方 名称 / 城市 · 州省 · 国家。各部分用 ` · ` 分隔。
- `status`：原始状态值（前端自己翻译）；主数据为 null。
- `highlights`：命中位置。`field` 为显示文本的键（`code` / `title` / `subtitle` / `columns.<列>`），`start` / `end` 为 UTF-16 码元下标（与 JavaScript 字符串下标一致），`end` 不含；运单号去分隔符匹配时已换算回显示文本里的位置。只圈真正参与匹配的部分（仓库名、品牌等不圈），纯数字命中单号只圈编号部分。
- `columns`（只搜一类时）：
  - `dn`：`recipient_name` `carrier_name` `tracking_number` `status` `shipping_stage` `expected_shipping_date` `order_number` `warehouse_name`
  - `asn`：`supplier_name` `status` `expected_arrival_date` `order_number` `tracking_number` `warehouse_name`
  - 作业任务：`task_type` `related_code` `related_url_hint` `status` `warehouse_name`
  - `goods`：`code` `name` `brand` `thumbnail_url` `available_stock_for_sale` `onhand_stock`（搜索范围内各仓库合计，口径同库存列表）
  - `location`：`code` `location_type` `warehouse_name` `goods_count`（数量 > 0 的商品种数）`quantity`（件数）
  - `recipient`：`name` `locality` `admin_area` `country`

「最近搜索」存在浏览器本地，后端不记。

性能：每类只取 `limit` 条（第一页没取满时不再 count），q 太短只做精确比较。包含匹配（`ILIKE '%q%'`）用不上普通 B-tree 索引，数据量大以后建议在 PostgreSQL 上加 `pg_trgm` 的 GIN 索引（`goods.name`、`goods.code`、`dn.order_number`、`asn.order_number`、`recipients.name` / `organization`、`suppliers.name`、`locations.code`、`cycle_count_tasks.task_name`）；编码完全相同可加 `lower(code)` 表达式索引；运单号去分隔符比较可加表达式索引 `upper(replace(replace(replace(tracking_number, ' ', ''), '-', ''), E'\t', ''))`（`dn_carrier_shipments`、`delivery_tasks`、`asn`）。DN 的当前运单号是关联子查询（与 DN 列表同一口径），单据很多时可考虑在 DN 上冗余当前运单号。

## 商品导入导出

代码在 `warehouse/goods/`（`columns.py` 列清单、`tabular.py` CSV / Excel 读写、`importer.py`、`exporter.py`、`file_views.py`）。
权限：导入与导入记录 `goods_add` / `goods_edit`；导出 `goods_read`。文件存库（`bytea`，与出口单证相同）；Excel 用 `openpyxl`
（requirements.txt）。

### 列

- 导入模板 = 导出预设 `import_template`，20 列、顺序固定：`code`、`name`、`unit`、`weight`（kg）、`length` / `width` / `height`
  （mm）、`origin_country`、`manufacturer`、`brand`、`image_url`、`thumbnail_url`、`category`、`tags`、`price`、
  `discount_price`、`currency`、`expiration_date`、`production_date`、`description`；`code`、`name` 必填。
- 只有导出才有（导入时忽略）：库存 `stock_on_hand`（`total_stock`）、`stock_available`（`available_stock_for_sale`）、
  `stock_pending_putaway`（`sorted_stock`）、`stock_damaged`（`damage_stock`），按库存范围合计；系统 `is_active`、`created_at`、
  `updated_at`（默认时区）、`created_by`。预设 `all` = 28 列。批次管理的列（管理方式、保质期、允收期、允发期……）商品表里还没有，
  批次二期再加。
- 表头按语言：`zh-CN` / `en-US` / `ja-JP`（编码 / Code / コード、重量 kg / Weight kg / 重量 kg……）。导入时三种语言、列键和常见别名
  都认（NFKC、转小写、去掉空白 / 括号 / 下划线后比较）。`GET /warehouse/goods/exports/columns`：分组、各语言的表头、预设。

### 导入

- `GET /warehouse/goods/bulk_upload/template?format=csv|xlsx&lang=`：CSV 为 UTF-8 带 BOM；不给 `lang` 表头是列键（同以前）；
  `lang` 不认识 → 400 `10019`。
- `POST /warehouse/goods/bulk_upload`（multipart）：`file`（.csv / .xlsx，最多 10,000 行数据）、`company_id`、`overwrite`
  （`skip` / `append` / `override`；`active` 只为旧调用方保留）、`dry_run`、`import_token`、`encoding`（`auto` / `utf-8` /
  `shift_jis`）、`sheet`、`lang`（错误行文件的语言）。
  - 编码已存在时：`skip` 跳过；`append` 只填原来为空的字段；`override` 有值的单元格替换原值，**空单元格一律保留原值、不清空**
    （要清空在商品编辑里做）。2026-10-25 之前 `override` 遇到空单元格会清空名称以外的品牌、价格、图片、描述等。`append` /
    `override` 同时把停用的商品恢复启用。缩略图留空：新增时取图片 URL；已有商品原缩略图为空或与原图片相同则跟着新图片走，否则保留。
  - CSV 编码 `auto`：有 BOM 或内容是合法 UTF-8 → UTF-8，否则 Shift_JIS（CP932）。Excel：默认第一个工作表（`sheet` 可指定）；
    数字 / 日期单元格按值读，长数字编码不走样。数字可带千位分隔（`1,200`）与全角数字；日期 `YYYY-MM-DD` / `YYYY/MM/DD`。
    空行跳过；`row` = 文件里的行号（表头为第 1 行）。
  - `dry_run=true` 不写任何东西 → 每一行的 `action`：`create`（`values`、`spec_missing`）/ `update`（`changes [{field, from,
    to}]`，恢复启用为 `is_active` false → true）/ `unchanged` / `skip` / `error`（`errors [{field, column, code, reason, value,
    message, …}]`），以及 `summary`、`file {name, format, encoding, encoding_detected, sheet, sheets, rows, columns}`、
    `columns`、`column_map`、`ignored_columns`、`read_only_columns` 与 `import_token`。
  - 带 `import_token` 执行：跳过错误行、导入其余（status `partial`）。文件 / 方式与预览时不同（`FILE_CHANGED`），或某行的结果
    （含预览里显示的原值）不同（`DATA_CHANGED`，期间商品被改过）→ 409 `16141`，什么都不导入。不带 `import_token`：有任何错误整批
    不导入，按第一个错误返回 400（details 同以前）。
  - 错误原因：`REQUIRED`（10006）、`DUPLICATE`（16101，`first_row`）、`INVALID_NUMBER`（10012；NaN / 无穷 16100）、
    `NEGATIVE`（价格 10012，其余 16100）、`OUT_OF_RANGE`（16100，`min` / `max`）、`INVALID_COUNTRY`（10014）、
    `INVALID_DATE`（10017）、`TOO_LONG`（10020，`max_length`）、`DISCOUNT_ABOVE_PRICE` / `DISCOUNT_WITHOUT_PRICE`（10012）。
  - `POST /warehouse/goods/bulk_upload/errors`（参数同上）→ 导入前下载错误行：原格式（CSV 同编码 / Excel），表头 + 错误行 +
    末列「错误 / Error / エラー」（Shift_JIS 的文件用日文）；没有错误行 204。
- 导入记录：每次执行（不含 dry_run）都记，整批被拒的也记：`GET /warehouse/goods/imports`（status `completed` / `partial` /
  `failed`、各计数、列、`failure {code, message, details}`）、`GET …/imports/<id>`（另带前 200 行 `error_rows`）、
  `GET …/imports/<id>/file`（原文件）、`GET …/imports/<id>/errors`（错误行文件）。文件保留 30 天（之后 409 `16142`），记录保留。
  这次导入新增 / 更新的商品：`GET /warehouse/goods/?import_id=<id>[&import_action=create|update]`。

### 导出

- 筛选 = 商品列表的参数（`code`、`name`、`keyword`、`category`、`brand`、`manufacturer`、`tags`、`currency`、`goods_codes`、
  `origin_missing`、`image_missing`、`spec_missing`、价格 / 日期区间），另有新增的（商品列表 `GET /warehouse/goods/` 也支持）：
  `is_active=all`、`has_stock`（当前仓库、没有则可访问的仓库里 `total_stock > 0`）、`created_from` / `created_to` /
  `updated_from` / `updated_to`（只给日期按默认时区当天、结束日含当天，或带偏移的 ISO 8601）、`import_id` / `import_action`。
  导出另可给 `filters.warehouse_id`（库存范围；默认当前仓库，没有则可访问的全部仓库）。
- `POST /warehouse/goods/exports/preview`（请求体同下）→ `{total, background, sync_limit, max_rows, too_large, columns [{key,
  header}], …, unencodable}`；`unencodable`（CSV + Shift_JIS）= `{count, characters, items [{id, code, name, columns,
  characters}]}`：文字里有 Shift_JIS 写不下的字符的商品（导出时写成 `?`；〜 ‖ − ¢ £ ¬ — 先换成 CP932 的对应字，不算）。
  Shift_JIS 写不下的表头（简体字）改用日文表头。
- `POST /warehouse/goods/exports` `{filters, columns | preset（import_template / all）, file_format csv | xlsx, encoding
  utf-8（带 BOM）| shift_jis, lang}`：≤ 1,000 件当场生成（201，`completed`）；更多的 202 `pending`，后台生成（API 进程里的线程，
  另有定时任务 `goods_files` 兜底），轮询 `GET …/exports/<id>`（`processed`、`total`、`progress`）。单次最多 100,000 件
  （400 `10022`）；请求不合法 400 `10021`（`field`）。
- `GET …/exports`（记录）、`GET …/exports/<id>/download`（不是 `completed` → 409 `16142`）、`POST …/exports/<id>/retry`（只有
  失败的能重试，否则 409 `16143`）、`POST …/exports/<id>/rerun`（按原条件新建一条，`source_export_id`）。文件保留 7 天（之后
  status `expired`）；中断的导出（10 分钟没有进度）重新排队，最多 3 次。限仓库的员工只看不含库存、或当前仓库库存的导出。

### 发货单批量添加：可售量

`GET /warehouse/inventory/availability?warehouse_id=&goods_codes=a,b,c`（或 `POST` `{goods_codes: [...]}`；最多 1,000 个编码；
`inventory_read` 或 `dn_edit`）→ `{warehouse_id, items: [{goods_id, code, name, unit, is_active, thumbnail_url,
available_stock_for_sale, onhand_stock, dn_stock, locked_stock}], not_found: [编码]}`，items 按请求顺序只列找到的商品。
可售 = `onhand_stock − locked_stock − dn_stock`（standard 库位）；批次的允发期还没有（一期没有带批次的库存）。编码为空 / 过多 →
400 `15019`（`field: goods_codes`）。收货通知批量添加用 `GET /warehouse/goods/?goods_codes=…&all=true` 取名称与单位（编码多时
分批，URL 别太长）。

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `GOODS_EXPORT_THREAD` | `true` | 大导出建任务后立刻在 API 进程里起线程生成；`false` = 只靠定时任务 `goods_files` |
| `GOODS_FILE_JOB_INTERVAL_MINUTES` | `1` | 定时任务 `goods_files` 的间隔（排队 / 中断的导出、删 7 天前的导出文件与 30 天前的导入文件）；`0` = 不跑 |

## Webhook 集成

WMS 通过 Webhook 向外部系统推送事件通知，使用 HMAC-SHA256 签名验证。

### 支持事件

| 事件 | 触发时机 |
|------|----------|
| `asn.received` | ASN 标记为已收货 |
| `asn.completed` | ASN 完成（含实际数量、完成时刻 `completed_at` / `completed_at_ms`） |
| `asn.cancelled` | 已收货（未分拣）的 ASN 被取消 |
| `dn.in_progress` | DN 开始处理（含开始时刻 `started_at`） |
| `dn.picked` | DN 拣货完成（含各商品计划 / 已拣量、拣货完成时刻 `picked_at`） |
| `dn.packed` | DN 打包完成（含各商品计划 / 已拣 / 已打包量、打包完成时刻 `packed_at`） |
| `dn.delivered` | DN 已发货（含运单号、发货时刻 `shipped_at`） |
| `dn.tracking_updated` | 自动运单的承运商跟踪有变化（状态 / 扫描记录，完整的最新结果，见「跟踪」） |
| `dn.completed` | DN 完成（含运单号、发货时刻 `shipped_at`、签收时刻 `signed_at`、签收来源 `signed_source`：`warehouse` / `carrier` / `rule`） |
| `dn.cancelled` | 开始处理后（`in_progress` / `picked` / `packed`）的 DN 被取消（含 `previous_status` 与各商品已拣 / 已打包量） |
| `goods.spec_updated` | 商品重量 / 尺寸 / 原产国变更（按公司广播，需订阅，见下文） |

`completed_at`（ASN）/ `started_at` / `picked_at` / `packed_at` / `shipped_at` / `signed_at` 是这一步实际发生的时刻（带时区偏移的 ISO 8601，如 `2026-10-23T13:03:00+09:00`），不是推送时刻：
仓库手动操作的 = 操作时刻（签收时填了签收时间的 = 填的时间），承运商揽收后自动发货的 = 承运商的揽收时刻，送达后自动签收的 =
承运商的送达时刻。推送可能晚到（定时推、失败重试），来源系统应按这些字段记各步的时间。

单据类事件（`asn.*`、`dn.*`）只推给创建该单据的 API Key。`asn.completed` 明细另带商品主数据：
`goods_weight_kg`、`goods_length_mm`、`goods_width_mm`、`goods_height_mm` 与
`goods_origin_country`（ISO 3166-1 alpha-2，`null` = 未录入），以及 `spec_pending`：收货通知要求采集（「收货规格采集」）、
到完成时仍没采集到的项（`weight` / `dimensions` / `origin_country`；`[]` = 没有——没要求采集、这一行填了跳过原因、或没到货时
也是 `[]`）。规格快照是完成时读的：`completed_at`（带仓库时区偏移的 ISO 8601，与其他事件时刻同格式）与 `completed_at_ms`
（epoch 毫秒）表示它有多新——同一商品拿 `completed_at_ms` 与 `goods.spec_updated` 的 `changed_at_ms` 比，取较新的一份。
商品表没有单独记规格的最近变更时刻，所以明细里不带每个商品的变更时刻。

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

- `source`：取商品新建 / 修改请求体里的 `spec_source`（`station` / `manual` / `import` / `api` / `receiving`）；
  收货通知明细上的规格采集（`PUT /warehouse/asn/<id>/details/<detail_id>/spec`）默认为 `receiving`（请求里传了 `spec_source`
  就照用，称重站传 `station`）；
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

## 库存流水与库存分析（库存底座）

所有改实物库存的地方（分拣完成、上架、下架、拣货、打包 / 少打包退回、DN 取消退回、发货、签收、移库、调整单）都经过统一的库存变动服务
（`warehouse/stock/service.py`），每次变动按桶（仓库 × 商品 × 批次 × 库位 × 库存类别）写一行库存流水；`inventory` 上的实物字段是行表的缓存。
口径详见 `doc/库存数值系统说明.md` 第九–十三节。查看类接口的权限为 `inventory_read`（阈值修改 `inventory_edit`），范围同库存列表。

| 接口 | 说明 |
|---|---|
| `GET /warehouse/stock-ledger/`、`/export` | 库存流水列表 / CSV：按仓库、商品、批号、库位、库存类别、变动类型、单据（doc_type + doc_id / doc_no）、操作人、来源、`trace_id`、时间段筛选 |
| `GET /warehouse/stock-ledger/summary` | 进销存：期初 / 入 / 出 / 期末（按变动类型拆分；仓内搬动不算入出） |
| `GET /warehouse/inventory/by-location`、`/by-location/items` | 按库位：库位占用（有库存的库位占比、设了容积另给容积利用率）、按货架（编码前缀）聚合、库位 × 商品明细 |
| `GET /warehouse/inventory/history`、`/history/export`、`/history/at`、`/history/series`、`/history/flows` | 历史库存（仓库当地日期 24:00，含待上架等全部类别；`coverage` 标出只有库位库存的旧快照日子）、任意时刻、日终走势、每日出入库量 |
| `GET /warehouse/inventory/metrics`、`/metrics/goods/<id>` | 库存分析（夜间汇总）：近 7 / 30 / 90 天出库（按发货完成）、周转天数、ABC、库龄档、呆滞 |
| `PUT /warehouse/inventory/thresholds`、`/thresholds/by-filter`，`GET /thresholds/suggest` | 补货阈值批量设置（逐个 / 按分类、品牌、勾选范围设固定值或建议值）与建议值预览 |

- 移库：从破损 / 退货库位移回 standard 库位必须填原因 `reason`（没给时非空的 `remark` 也算）→ 否则 400 `15018`；原因写进流水备注。
- 库存列表新增筛选 `below_low_threshold=true` / `above_high_threshold=true`；待上架汇总支持 `keyword`（与列表一致）。
- 写入守卫 `STOCK_GUARD`：`log`（默认，绕过统一服务改库存只记 warning 日志）/ `raise`（测试、开发默认，直接报错）/ `off`。
- 维护命令：`flask stock init-ledger`（期初，流水为空时才执行）、`reconcile [--apply] [--sync-legacy-counters]`（校正，默认演练）、`audit`（对账，
  有问题退出码 1）、`daily` / `rebuild-daily --from --to`（日终结存）、`metrics [--date]`（库存分析）、`fix-legacy-pending [--include-picked] [--apply]`（已确认的旧 bug 造成的计数差额校正：B-06 拣货下架误计入待上架、B-54 少打包差额留在已拣，默认演练）、`replay [--apply] [--report 文件]`（历史流水回放，
  默认演练、只出与旧快照逐批核对的差异报告）。
- 定时任务：每小时（`STOCK_DAILY_MINUTE`，默认第 7 分钟）按各仓库当地日界补生成前一天的日终结存与库存分析，生成新的一天后对账（差异只记日志）。

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
DN 须为 `picked` 或 `packed`（否则 409 `16067`）；箱子归发货，国内件（没有报关快照）也录。请求体 `{"packages": [{package_no, gross_weight_kg, length_mm, width_mm, height_mm, remark}]}`，
返回 `{packages, voided_documents}`。1–99 箱，编号从 1 连续（缺省自动编）；毛重 0.01–999.999 kg（三位小数）；
长宽高 1–3000 mm 整数；不合法 400 `16066`。有报关快照且已出单证时改箱子 → 现有单证作废（`void_reason: packages_changed`）。
有未结束的自动运单（`source: api`）时不能改（409 `16076`）；手填的运单号不拦。

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
- 完成发货只是记下货已经交出去，不查单证：没有 CI / PL、或单证已过期（箱子 / 快照 / 出口资料改过没重出）都放行
  （以前的 `16069` 不再出现）；过期只在报关卡片 / 海外发货步骤里提示（`GET /dn/<id>/customs` 的 `documents_outdated`）。
- DN（海外 / 国内一样）的自动运单进行中 / 结果不明，或正在取消 / 取消结果不明（`pending` / `unknown` / `cancelling`）时，
  带不带运单号都不能完成发货 → 409 `16079`（`details.unresolved`），先处理（取消 / 确认作废）。
- 完成发货可带 `shipped_at`（实际交承运商的时刻，ISO 8601，默认现在；晚于现在 → 400 `16136`），记 `completed_source: warehouse`
  （见「发货流程」）。
- 单证不印运单号 / 承运商，完成发货时填的运单号、承运商不再与 CI 比对（原来的 `16080` / `16092` 已不再使用）；
  有有效自动运单时运单号 / 承运商的锁定见「承运商对接」（`16078`）。
- 发货任务只在打包完成时自动生成，没有手工新建接口。一张 DN 同时只允许一条未完成（`pending` / `in_progress`）的有效发货任务：
  已有（例如修复前手工建的存量任务）→ 复用那条，不再生成第二条。
- 有报关快照或有运单记录（任一来源）的 DN，单证 / 运单只认当前发货任务（最新的有效任务）：对别的任务完成发货 / 保存运单号 /
  修改 → 409 `16091` `details {delivery_task_id, current_status}`。
- `dn.delivered` 的 `tracking_number` 取被完成的那条任务的运单号。
- DN 发货（`delivered` / `completed`）后，报关快照、箱子、单证全部锁定 → 409 `16065`。
- 海外 DN 的 `dn.delivered` 追加 `customs_documents`（含 `download_path`）、`packages`、
  `invoice_total{currency, goods_value, freight, insurance, total}`；国内件 payload 不变。

### 配置

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `DOCUMENT_TIMEZONE` | （不设） | `WMS_DEFAULT_TIMEZONE` 的旧名（它没设时读取）。单证日期是 DN 仓库当地的日期，见「时区」 |
| `CUSTOMS_SIGNATURE_COMPANY_ID`、`CUSTOMS_SIGNATURE_FROM_DN_ID`、`CUSTOMS_SIGNATURE_SIGNATORY_NAME`、`CUSTOMS_SIGNATURE_SIGNATORY_TITLE`、`CUSTOMS_SIGNATURE_PATH` | （不设） | 可选的出口公司 ID、首个启用签名的 DN ID、签署人姓名和职务，以及授权签名 PNG 的绝对路径；五项须同时配置。图片须保存在源码目录之外、仅后端进程可读。较早的 DN 维持原签署人和未签名 CI；启用后的 DN 更换签名图片会使已签发单证显示为过期。 |
| `CUSTOMS_PDF_FONT_PATH` | （不设） | 单证用的 TrueType 字体（`.ttf`，或 `.ttc` 的第 1 个字体；不支持 CFF 轮廓的 OTF，如 Noto Sans CJK）。设了就用它印全部文字，字体子集嵌入 PDF；要让收件人的日文等也嵌入，选带这些字形的字体（如 IPAexGothic）。字体里没有的字符、以及不设时 Helvetica 印不出的字符，退回 reportlab 内置的日文 CID 字体（不嵌入，依赖阅读器的字体） |

## 发货流程（国内 / 跨境）

一张出库单一条流程：下单 → 拣货 → 打包 →（要报关时）报关单证 → 运单 → 揽收 → 交承运商 → 运输中 → 签收。设计见
`doc/shipping-flow.md`；第一期做了承运商对接方式、国内 / 跨境判断、手填运单与手工揽收、来源与实际时刻、按规则自动签收和流程
汇总接口；第二期加了文件方式（CSV 导出 / 导回）、结构化国际地址、国家规则包和运单服务选项。设计不假设国家、承运商或是否签了 API 合同：没有 API 的承运商也能手工走完整个流程。

### 国内 / 跨境（route）

`warehouse/shipping/route.py` 按 DN 判断：

- 发货国 `origin_country` = 仓库的 `country_code`，没有用公司的。
- 收件国 `destination_country` = 报关快照的收件国（`recipient_country`，没有用 `consignee.country`）→ DN 收件地址快照的
  `ship_to.country` → 收件人的 `country`。
- `international` = 发货国 ≠ 收件国（任一方未知按国内）。
- `requires_customs` = 跨境且不在同一关税同盟（`warehouse/common/customs_unions.py`，默认登记 EU）；**有报关快照的 DN
  一律要报关**（来源系统明确给了报关数据）。
- 出口单证（CI / PL）、交给承运商的 commodities / invoice、贸易单证 / ETD、建单后重签是报关**扩展**
  （`warehouse/dn/customs_extension.py`），只在 `requires_customs` 且有报关快照时参与；国内件不碰报关。
- 箱子（`PUT /warehouse/dn/<id>/packages`）归发货：不要求报关快照（国内件也录箱子）；只有有报关快照时改箱子才作废 CI / PL。

### 承运商对接方式

承运商每项能力各选一种对接方式（`carriers` 新增列，迁移 `55e9fa27fb9e`）：

| 字段 | 取值（默认） | 说明 |
|------|--------------|------|
| `waybill_method` | `api` / `file` / `manual`（`manual`） | `api`：插件建单；`file`：按承运商软件的格式导出 CSV、导回运单号（见「文件方式」）；`manual`：手填 / 扫码运单号，面单在承运商网站或软件打 |
| `pickup_method` | `api` / `manual` / `scheduled` / `dropoff` / `none`（`none`） | `api`：插件预约；`manual`：电话 / 网站约好后在 WMS 记一笔；`scheduled`：定期集荷；`dropoff`：自送网点；`none`：不管揽收 |
| `pickup_schedule` | json 或 `null` | `scheduled` 时：`{weekdays: [1..7]（ISO，1 = 周一）, ready_time: "HH:MM", close_time: "HH:MM"}` |
| `pickup_contact` | ≤ 255 字 | 约揽收的电话 / 网址 / 说明（`manual` 时显示） |
| `tracking_method` | `api` / `link` / `none`（`link`） | `api`：插件定时查（揽收后自动交承运商、送达后自动签收）；`link`：只给公开查询页链接 |
| `tracking_url_template` | 带 `{tracking_number}` 的 URL，≤ 500 | 公开查询页；为空用插件内置的（FedEx：`https://www.fedex.com/fedextrack/?trknbr={tracking_number}`） |
| `auto_sign_days` | 1–90 或 `null`（关） | 交承运商后这么多天还没签收、且运单没有 API 跟踪时自动视为签收 |
| `file_preset` / `file_template` / `file_account` | 文字 / json / json | 文件方式的设置（见「文件方式」，迁移 `a7c4e2f9b3d1`） |

- `GET /warehouse/carrier/`（列表）与 `GET / PUT /warehouse/carrier/<id>` 返回这些字段和只读的
  `available_methods: {waybill: [...], pickup: [...], tracking: [...]}`：`api` 只在承运商的在线对接已启用、且插件声明了
  对应能力（`supports_waybill` / `supports_pickup` / `supports_tracking`）时出现；`file` 在设了文件预设 / 模板后出现（可以在同一个
  请求里设）。
- `POST` / `PUT` 校验：取值不合法、方式现在不可选、`pickup_schedule` / `tracking_url_template`（须为
  `http(s)://…{tracking_number}…`）/ `auto_sign_days` 不合法 → 400 `16135`（`field` 指明哪一项，如 `waybill_method`、
  `pickup_schedule.weekdays`）。没改的方式不重新校验（对接停用后照样能保存承运商的其它资料）。
- 设置在线对接时自动跟上：新建 / 重新启用 / 换插件后，还是默认值的方式改成 `api`（插件支持的）；删掉对接（或换成不支持的插件）
  时 `api` 退回默认值。
- 迁移回填：有启用的 FedEx 对接的承运商三项都设 `api`（与升级前一致）；FedEx 对接停用了的只设 `tracking_method: api`
  （已建的运单照旧跟到底）。第三方插件不回填，升级后在承运商页选。
- 插件预约揽收要求 `pickup_method: api`（否则 blocker `PICKUP_METHOD_NOT_API`，409 `16130`）。插件建运单只要求对接可用
  （运单方式决定流程里先给哪个操作）。经 API 跟踪要求 `tracking_method: api`。

### 运单：插件 / 手填 / 文件

`dn_carrier_shipments` 现在记一张 DN 的所有运单，`source` 区分来源：`api`（插件建的）、`manual`（手填 / 扫码）、
`file`（文件导出导入，见「文件方式」）。一张 DN 同一时刻仍至多一条未结束的运单记录（`pending` / `active` / `unknown` /
`cancelling` / `exported`，部分唯一索引，不分来源）。`carrier`（插件代码）改为可空；`carrier_data`（json）留给插件 / 文件模板。

- 保存运单号（`PUT /warehouse/delivery/<task_id>/tracking`、`PUT /warehouse/delivery/<task_id>`、完成发货时带
  `tracking_number`）同时写一条 `manual` 运单记录：`status: active`，`carrier_id` = 任务的承运商，`carrier` = 该承运商在线对接的
  插件代码（没有对接为 `null`），`created_by` = 操作人。换号或换承运商 → 旧记录 `cancelled`（`reason: replaced`）+ 新记录
  `active`；清空 → `cancelled`（`reason: cleared`）；同一个号码换了写法（空格 / 连字符）只更新写法。删掉带运单号的发货任务 →
  `cleared`；取消 DN → `dn_cancelled`。都不调承运商。
- **锁定规则只对 `api` 运单生效**（在承运商那边真实存在，换号要先取消）：`16078` / `16079`、`16076`（改箱子 / 报关数据）、
  `16110`（取消 DN）、`16125`（删对接）以及运单接口只看 `source = api`。有未结束的 `api` 运单时不另写 `manual` 记录（运单号由它定）。
- 发货任务上有手填运单号时插件建单照旧拦（`TRACKING_NUMBER_EXISTS`），先清空。
- 原来只对海外 DN 的守卫改为按运单记录判断：任何 DN 的 `api` 运单进行中 / 结果不明 / 取消中都不能完成发货（409 `16079`）；
  有报关快照**或**有运单记录的 DN 只能对当前发货任务完成发货 / 修改 / 存运单号（409 `16091`）。

### 揽收：插件预约 / 手工记录

- 揽收页（`GET /warehouse/dn/carrier-pickups`）列出 `pickup_method` 是 `api`（对接启用的在前）或 `manual` 的承运商，
  以及所选承运商的有效运单（任一来源：`api` / `manual` / `file`，DN 未发货）。新增：`method`（`api` / `manual`）、
  `can_record`、`carrier.pickup_method`、`carrier.pickup_contact`、`shipments[].source`、`pickups[].source`。
- `POST /warehouse/dn/carrier-pickups/manual`（`packing_edit` / `delivery_edit`，请求头 `X-WAREHOUSE-ID`）记一笔电话 /
  网站约好的揽收：

```json
{ "warehouse_id": 1, "carrier_id": 4, "shipment_ids": [201, 202], "pickup_date": "2026-10-27",
  "ready_time": "13:00", "close_time": "17:00", "confirmation_code": "TEL-0425", "remarks": "Booked by phone" }
→ 201 { "pickup": { "id": 9, "source": "manual", "status": "scheduled", "carrier": null, "carrier_id": 4, "...": "..." },
        "alerts": [], "message": null }
```

  `carrier_id` 必填（揽收方式 `manual` 或 `api`：`api` 的承运商系统故障时也可以退回手工记）；`warehouse_id` 可带，须是当前仓库；
  `confirmation_code` ≤ 40、`remarks` ≤ 100；日期 / 时间同预约（今天起 30 天内，最晚时间晚于准备好时间）。不合法 → 400
  `16131`（`field`）；运单不能带 → 409 `16132`（原因同预约）。不调承运商；记录像预约一样占着所带运单。手工记录的
  `POST …/carrier-pickups/<id>/cancel` 只改本地。

### 跟踪任意运单号

跟踪定时任务（见「跟踪」）跟所有 `active` 运单中：承运商 `tracking_method: api`、插件 `supports_tracking`，且运单是插件建的
（`source = api`）或插件 `tracks_any_number`（FedEx：在 FedEx Ship Manager 建、手填进 WMS 的号码也跟）。「查到同号旧包裹」的
判定基准：`api` 运单用建单时刻，`manual` / `file` 用 DN 创建时刻。这类手填运单也能用
`POST /warehouse/dn/<id>/carrier-shipment/track` 手动刷新。

### 实际时刻与来源

- `PUT /warehouse/delivery/<task_id>/complete/` 可带 `shipped_at`（实际交承运商的时刻，ISO 8601，带不带时区偏移都行，默认现在）：
  完成时间、实际发货日、DN 的 `delivered_at`、`dn.delivered` 的 `shipped_at` 都是它。格式不对 → 400 `16050`，晚于现在
  （允许 5 分钟时钟误差）→ 400 `16136`（`field: shipped_at`）。
- 发货任务记来源：`completed_source` / `signed_source` = `warehouse`（手工）/ `carrier`（承运商跟踪自动）/ `rule`（自动视为签收）；
  `null` = 旧数据（按 `warehouse`）。发货任务接口都返回；`dn.completed` 带 `signed_source`。

### 自动视为签收（规则）

承运商设了 `auto_sign_days` 时，定时任务（每 `CARRIER_AUTO_SIGN_INTERVAL_MINUTES` 分钟，默认 60，`0` = 不跑）把满足条件的
发货任务签收：DN 是 `delivered`、当前发货任务 `completed`、有效运单**没有**经 API 跟踪（口径同上）、完成时刻 + N 天已过。
`signed_at` = 完成时刻 + N 天，`signed_source: rule`，操作人 = 运单建单人（没有用 DN 创建人）；走手工签收同一个逻辑（DN
`completed`、推 `dn.completed`）。一张失败不影响别的。任何时候都可以手工确认签收（填实际时刻）。

### 流程汇总接口

`GET /warehouse/dn/<id>/shipping-flow`（`dn_read` / `packing_read` / `delivery_read`）：管理端和 PDA 都按它渲染，不各自推算状态：

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

- `warehouse_id`：DN 所属仓库（揽收按仓库约）。
- `carrier`：当前发货任务的承运商（没有用 DN 的），都没有为 `null`；`integration` = 插件代码；`tracking_url` = 当前运单号的
  公开查询页（没有运单号或 `tracking_method: none` 为 `null`）。
- `steps[].key` 固定按顺序：`ordered`、`picking`、`packing`、`documents`、`waybill`、`pickup`、`handover`、`transit`、`delivered`。
- `steps[].state`：`done` / `current`（流程走到这一步）/ `todo`（还没到）/ `warning`（要人处理：运单结果不明、单证过期、跟踪异常、
  自动步骤被挡、揽收结果不明、揽收日已过……）/ `blocked`（走到了但被前面的问题挡着，如没有报关数据）/ `skipped`（不适用：
  不报关的件的 `documents`、`pickup_method: none` 的 `pickup`、没有跟踪的 `transit`、DN 取消时没做的步骤）。
- `steps[].at`：这一步实际发生的时刻（ISO 8601，带时区偏移）；`source`：`warehouse` / `carrier` / `rule` / `api`（下单来自来源系统；
  `waybill` / `pickup` 步骤为经承运商插件建的运单 / 约的揽收）/ `system`。
- 各步骤的 `summary`：`ordered` {order_number, dn_type, expected_shipping_date}；`picking` {task_ids, picked, planned}；
  `packing` {task_ids, packed, planned, package_count, gross_weight_kg, packages[], packages_required}（要录箱子 —— 打包完
  没录时提示 `PACKAGES_MISSING` —— 的是要报关的件、`api` 运单、文件模板有不带 `|default` 的 `{package.*}` 列的；国内手工运单不要）；`documents` {required, customs, version,
  outdated, missing[], ready, documents[]（当前单证的元数据）}；`waybill` {method, tracking_number, source, service, service_name,
  label_document_id, label_download_path, shipment（同 `carrier-shipment.shipment`，没有当前运单时为最近一张已取消的插件运单）,
  unresolved, can_create, blockers[]}；`pickup` {method, pickup（揽收记录或 null）, contact, schedule}；`handover`
  {delivery_task_id, actual_shipping_date}；`transit` {tracking（运单的跟踪结果或 null）, tracking_url, tracked_by_api}；
  `delivered` {estimated_delivery, auto_sign_days, auto_sign_at}。第二期：`waybill` 另有 `export`（只在文件运单已导出、待导回运单号时
  给：{exported_at（带偏移）, batch_id, file_name, export_count, exported_by: {id, user_name}}，否则 null；文件运单的
  `shipment.file` 另有 {preset, exported_at, export_count, import_batch_id, imported_at}）和 `options`（{values, schema, defaults,
  locked, locked_reason, locked_message}，见「运单服务选项」）；`service_name` 为服务的显示名（文件运单为模板 `service_option` 的
  名称）；交承运商后还挂着的「已约」揽收为 `done`，`summary.pickup.done: true`、`summary.handed_over: true`（不再提示重约）。
- `actions[].key`：`create_waybill`、`enter_tracking`、`cancel_waybill`、`dismiss_waybill`、`print_label`、`book_pickup`、
  `record_pickup`、`cancel_pickup`、`complete_shipping`、`refresh_tracking`、`open_tracking`、`sign`、`issue_documents`、
  `edit_packages`、`export_file`、`import_numbers`、`discard_export`。运单方式为 `file` 时运单步骤给 `export_file`（单张导出；
  已导出的带 `re_export: true`）、`import_numbers`（批量导回）和（已导出时）`discard_export`（带 `shipment_id`），都带
  `carrier_id`；新增原因码 `FILE_NOT_CONFIGURED`（没有可用的模板 / 账号项没填）、`WAYBILL_EXISTS`。`enabled: false` 时带 `reason`（英文）和翻译用的 `reason_code`（`DN_SHIPPED`、`NOT_PICKED`、`NOT_PACKED`、`NO_WAYBILL`、
  `PICKUP_EXISTS`、`NO_OPEN_TASK`、`WAYBILL_UNRESOLVED`……见 `warehouse/shipping/flow.py` 的 `REASON_CODES`；也可能是建运单 /
  预约揽收的拦截码或报关问题码）；部分操作带要操作的对象 id
  （`delivery_task_id`、`pickup_id`、`document_id`、`url`）。界面按 key 接到已有接口。
- `problems[]`：`{code, level: error | warning, message, step, …}`——报关问题、运单方式为 `api` 时建单的拦截原因、运单 / 揽收
  结果不明、跟踪异常、自动步骤被挡。

### 列表的发货阶段

`stage`（以及 DN 列表的 `shipping_stage`）用 SQL 判定（`warehouse/shipping/stage.py`），流程接口与列表同一口径：`closed`（DN
关闭 / 取消）、`delivered`（已签收）、`preparing`（待处理 / 拣货中 / 已拣货）、`exception`（插件运单结果不明、跟踪异常或自动步骤
被挡、揽收结果不明）、`in_transit`（已交承运商、未签收），打包完的 DN 再分 `awaiting_waybill`（还没有运单 / 运单号）、
`awaiting_pickup`（承运商要逐票约揽收——`api` / `manual`——而运单还没被揽收占着）、`awaiting_handover`；`awaiting_number`
= 文件运单已导出、还没导回运单号（任务上也没有运单号）。

- `GET /warehouse/dn/` 每行带 `shipping_stage` 与 `shipping: {carrier_id, carrier_name, tracking_number}`（整页一条查询算好）；
  `?shipping_stage=<stage>` 按它筛选（不认识的阶段 400）。
- `GET /warehouse/delivery/` 每行带 `shipping_stage`（所属 DN 的阶段）。

### 文件方式（不需要 API 合同）

没有 API 合同的承运商用它自己的发货软件：WMS 按软件的导入格式导出 CSV，在软件里打完面单，再把带运单号的 CSV 导回
（迁移 `a7c4e2f9b3d1`）。

**预设**：`GET /warehouse/carrier/file-presets`（`carrier_read`）列出 `warehouse/carrier/presets/<国家>/<代码>.json` 里的预设
（欢迎社区补充，同一结构，带 `version` 与 `sources`）：`[{code, name: {en-US, zh-CN, ja-JP}, country, version, notes, verified,
sources[], tracking_url_template, tracking_url_fallback, tracking_url_notes, service_option, service_options[],
account_fields[], columns: [{header, required, max_length}], export: {encoding, delimiter, quoting, header, line_ending,
length_unit, charset}, import: {encoding, encoding_fallbacks, header_rows, reference_column, tracking_column,
tracking_normalize} | null}]`。第一批三份都**还没在承运商软件里实测**（`verified: false`）：

| 代码 | 软件 | 列数 | 说明 |
|------|------|------|------|
| `jp.yamato_b2cloud` | ヤマト B2クラウド「基本レイアウト」 | 95 | Shift_JIS，长度按 Shift_JIS 字节，只收 JIS 第1・第2水准的字（`charset: jis_x0208`）；导回 =「発行済データの検索 → 外部ファイル出力」（第 1 列匹配键、第 4 列运单号）；账号项 `billing_customer_code` / `billing_class_code` / `freight_mgmt_no`；查询页 `https://member.kms.kuronekoyamato.co.jp/parcel/detail?pno={tracking_number}` |
| `jp.sagawa_ehiden3` | 佐川 e飛伝Ⅲ「標準_飛脚宅配便_CSV_ヘッダ有」 | 74 | 住所 / 名称为全角字段（`zenkaku` 过滤器）；导回 = 出荷履歴（UTF-8，读不了按 Shift_JIS；削除区分 = 2 的行跳过）；保留旧查询页，`tracking_url_fallback` = `https://e-customer.sagawa-exp.co.jp/k2k`（佐川查询服务受限期间没有带单号的深链） |
| `jp.japanpost_yupri_r` | 日本郵便 ゆうパックプリントR「標準フォーマットv3」（明細数 = 1） | 87 | 无表头、每个字段都加双引号；导回 = 新ＤＥＮＦＤ確定（第 8 列匹配键、第 7 列运单号）；查询页 `https://trackings.post.japanpost.jp/services/srv/search/direct?reqCodeNo1={tracking_number}` |

**承运商设置**（`GET / PUT /warehouse/carrier/<id>`）：`file_preset`（预设代码或 `null`）、`file_template`（在预设基础上改的部分——
对象逐键合并、列表整个替换、`null` 删掉该键；没有预设时是完整模板；`null` = 完全用预设）、`file_account`（模板 `account_fields`
声明的账号项 `{key: 文字}`），以及只读的 `file_mode: {configured, ready, preset: {code, name, verified, exists} | null, error,
service_options, account_fields, missing_account_fields, can_import, tracking_url_template, tracking_url_fallback, encoding}`。
只读的 `service_options` 是这家承运商的 DN 运单服务选项按它校验的声明（运单方式 `file` 用文件模板的，否则用在线对接插件的；
没有为 `[]`）。设了预设 / 模板后 `available_methods.waybill` 才给 `file`；`file_preset` 与 `waybill_method: file` 可以在同一个请求里设。
不合法 → 400 `16135`，带 `field`（`file_preset`、`file_template.export.columns[3].value`、`file_account.<key>`；方式还是
`file` 时去掉模板 → `file_preset`）。承运商自己没填查询页、插件也没有时，用文件模板的查询页。

**模板**（与预设同一结构；`shift_jis` 一律按 cp932 读写）：

```
{code, name{语言}, country, version, sources[], notes, verified, tracking_url_template, tracking_url_fallback?,
 service_option?（哪个服务选项代表服务种类：记到运单的 service_type / service_name）,
 export: {format: 'csv', encoding: 'cp932' | 'utf-8' | 'utf-8-sig', delimiter, quoting: 'minimal' | 'all',
          header: bool, line_ending: '\r\n' | '\n', length_unit: 'chars' | 'sjis_bytes', row_unit: 'dn',
          charset?: 'jis_x0208', columns: [{header, value, required?, max_length?, notes?}]},
 import: {encoding, encoding_fallbacks[], delimiter?, header_rows, reference_column（1 起的列号）, reference_value: 'DN{dn.id}',
          reference_parse: '^DN(\\d+)$', tracking_column, tracking_normalize: 'digits' | null,
          skip_rows_when?: {column, equals}},
 service_options: [{key, kind: 'select' | 'date' | 'money' | 'text', label{语言}, values?[{value, label}], default?, help?}],
 account_fields: [{key, label{语言}, required, help?}]}
```

`value` 是字面量加 `{变量|过滤器:参数|…}`（`{{` / `}}` 为字面的花括号）。变量：`dn.id`、`dn.order_number`、`dn.remark`、
`dn.expected_shipping_date`、`dn.special_handling`、`dn.packaging_info`、`ship_date`（仓库时区的今天）、`ship_to.*` /
`shipper.*`（结构化地址字段，见下，另有 `contact`、`address_text`、`country_name`、`latin.<字段>`）、`goods.summary`（商品名，
「 / 」分隔）、`goods.first_name`、`goods.quantity`、`goods.count`、`package.count`（没录箱子为空）、`package.total_weight_kg`、
`options.<key>`（DN 的运单服务选项，没设的用默认值）、`carrier.account.<key>`、`carrier.name`、`carrier.code`。过滤器：`digits`、
`truncate:n`、`truncate_bytes:n`（按 cp932 字节，不切断双字节字）、`slice:a:b`、`date:%Y/%m/%d`（空值输出空串）、`default:x`、
`map:k=v,k=v`、`upper`、`zenkaku`（半角转全角）、`nospace`（去半角 / 全角空格）、`jp_phone`（`+81 …` → 0 开头的国内号码，全角数字
转半角）、`int`。保存模板时不认识的变量 / 过滤器直接拒绝。匹配键一律写 `DN{dn.id}`（订单号可能为空、不唯一、超长），订单号另放在
面单上看得到的备注列。

**导出**——`POST /warehouse/dn/carrier-files/export`（`packing_edit` / `delivery_edit`），JSON
`{carrier_id, dn_ids: [...]（1–500）, warehouse_id?}`（仓库 = `X-WAREHOUSE-ID` / 请求体的 `warehouse_id`，都没有用第一张 DN 的）：

- 200：文件——`Content-Disposition: attachment; filename="<承运商代码>_<YYYYmmdd-HHMMSS>.csv"`，按模板编码的字节（cp932 时
  `text/csv; charset=Shift_JIS`），响应头 `X-Batch-Id` / `X-Row-Count`。每张 DN 建一条 `file` 运单记录，`status: exported`
  （新的**未结束**状态：一张 DN 仍至多一条未结束记录）、带 `file_batch_id`；已是 `exported` 的 DN 重新导出时记录不变、换批次。
  记一个导出批次（`carrier_file_batches`）。
- 先逐行检查，**任何一行有问题整批不导出**（不出文件、不建记录）：409 `16137`
  `details {rows: [{dn_id, order_number, problems: [{column, column_index?, code, message, …}]}], total}`。DN 级的 code：
  `DN_NOT_FOUND`、`OTHER_WAREHOUSE`、`NOT_PACKED`、`DN_SHIPPED`、`DN_CLOSED`、`NO_OPEN_TASK`、`CARRIER_NOT_SET`、
  `OTHER_CARRIER`（发货任务的承运商——没有用 DN 的——须是这家）、`WAYBILL_EXISTS`（已有别的未结束运单）、`TRACKING_NUMBER_EXISTS`、
  `OPTION_INVALID`（`column: options.<key>`）；列级的 code（`column` = 列名）：`REQUIRED`、`TOO_LONG`（带 `length`、`max_length`、
  `length_unit`——姓名 / 地址超长不静默截断）、`UNENCODABLE`（`characters`）、`CHARACTER_NOT_ALLOWED`（`charset: jis_x0208` 时
  不在 JIS 第1・第2水准的字）。字段内换行换成空格。
- 400 `16138`：`carrier_id` / `dn_ids` / `warehouse_id` 不合法（`field`）；409 `16138`：承运商的 `waybill_method` 不是 `file`、
  模板用不了、必填账号项没填（`field: file_account.<key>`、`details.missing_account_fields`）。

**导入**——`POST /warehouse/dn/carrier-files/import`（`packing_edit` / `delivery_edit`），multipart：`file`（≤ 5 MB、≤ 5000 行）、
`carrier_id`、`dry_run`（默认 `true` / `false`）、`warehouse_id?`：

```json
{ "dry_run": true, "file_name": "b2.csv", "encoding": "cp932", "batch_id": null,
  "rows": [{"line": 2, "reference": "DN163", "dn_id": 163, "order_number": "SHP-1", "tracking_numbers": ["401234567890"],
            "result": "matched", "reason_code": null, "message": null, "primary": true}, "..."],
  "summary": {"matched": 2, "not_found": 1, "already_has_number": 0, "same_number": 0, "invalid": 0, "skipped": 0,
              "dns": {"matched": 1, "not_found": 1, "already_has_number": 0, "same_number": 0, "invalid": 0}} }
```

- 按 `import.encoding` 与 `encoding_fallbacks` 解码（候选里有 UTF-8 时，有 BOM 或是合法 UTF-8 就按 UTF-8）；`header_rows`
  只在它不像数据行时跳过；`skip_rows_when` 的行计入 `summary.skipped`；匹配键按 `reference_parse` 解析，运单号按
  `tracking_normalize` 规范化（`digits` 去掉连字符）。
- 一张 DN 多行（多件）= 多个运单号：第一个为主单号，其余进 `package_tracking_numbers`。
- `result`：`matched`（会写入 / 已写入）、`not_found`（本公司 / 本仓库没有这张 DN，或匹配键不是 `DN<id>`）、`already_has_number`
  （DN 已有不同的运单号，需人工决定）、`same_number`（已导入过，忽略）、`invalid`（没有运单号、DN 已关闭、承运商不同、插件运单
  结果不明、号码已属于别的 DN、没有未完成的发货任务、没导出过就已发货）。
- 除 `matched` 外每行带 `reason_code` 与英文 `message`（兜底），界面按 `reason_code` 出本地化文字：`not_found` → `DN_NOT_FOUND` /
  `NOT_WMS_REFERENCE` / `NO_REFERENCE`；`invalid` → `NO_TRACKING_NUMBER` / `DN_CLOSED` / `OTHER_CARRIER` /
  `NUMBER_OWNED_BY_OTHER_DN` / `API_SHIPMENT_UNRESOLVED` / `EXPORTED_FOR_OTHER_CARRIER` / `SHIPPED_WITHOUT_FILE_WAYBILL` /
  `NO_OPEN_TASK`；`same_number` → `ALREADY_IMPORTED` / `ALREADY_SAVED`；`already_has_number` → `HAS_OTHER_NUMBER`。拼文字用的
  字段（有才给）：`existing_tracking_number`（DN 已有的 / 已属于别的 DN 的号码）、`other_carrier` / `other_carrier_id`、
  `other_dn_id`、`shipment_status`。
- `dry_run: false`：逐张 DN 加锁后重新判断，在一个事务里写入 `matched` 的：`exported` 记录 → `active`（没导出过的 DN 直接建
  `file` 运单），主单号写到发货任务上（任务没有承运商时一并设上）——与保存运单号同一套逻辑，跟踪（`tracks_any_number`）、
  揽收、流程都与其它运单一样。记一个导入批次（`batch_id`）。
- 400 `16138`：没有文件 / 文件为空 / 超过 5 MB / 读不了（`field: file`），`dry_run` / `carrier_id` 不合法；409 `16138`：
  不是文件方式 / 模板不能导入。

**作废导出**——`POST /warehouse/dn/<id>/carrier-shipment/discard-export`：已导出的记录 → `cancelled`（`reason: discarded`；只改
WMS，在承运商软件里打过的请在那边删掉）；200 = `GET …/carrier-shipment` 的内容加 `waybill`（任一来源的当前运单或 `null`）；
没有可作废的 → 409 `16075`。已导出期间手填了运单号 → 这次导出作废（`reason: replaced`）；`file` 运单的换号 / 清空与手填的相同。

**批次**——`GET /warehouse/dn/carrier-files/batches?carrier_id=&warehouse_id=&limit=`（`dn_read` / …）：新的在前，
`[{id, carrier_id, warehouse_id, kind: export | import, file_name, row_count, summary, created_at, created_by,
created_by_user}]`（不存文件内容）。

### 结构化地址

收件人、仓库、DN 的收件地址快照，以及交给插件 / 文件模板的 `ship_to` / `shipper` 用同一组字段名：`country`（ISO 3166-1
alpha-2）、`postal_code`、`admin_area`（州 / 省 / 都道府县）、`locality`（市区町村）、`dependent_locality`、`address_line1`、
`address_line2`、`organization`、`name`、`phone`、`email`，以及拉丁转写 `latin: {name, organization, address_line1,
address_line2, locality, admin_area}`（跨境面单用；没有时用本地文字或报关快照里的英文收件人）。

- **收件人**（`/warehouse/recipient/`）：新增 `admin_area`、`locality`、`dependent_locality`、`address_line1`、`address_line2`、
  `organization`、`latin`；`zip_code` 就是 `postal_code`（两个名字都收、都返回；两个都给且不同 → 400 `14019`）。`null` 清空。
  只给了结构化字段（没给 `address`）时按国家格式生成单行的 `address`（结构化字段改了会重新生成）。
- **仓库**（`/warehouse/warehouse/`）：`admin_area`、`locality`、`address_line1`、`address_line2`、`latin`（`postal_code` ≡
  `zip_code`）；`address` 同样生成；`address_en` 保留为拉丁地址的单行写法，为空时由 `latin` 生成。
- **DN**：`ship_to`（json 快照）与 `shipping_options`（json）。`POST /warehouse/dn/` 接受 `ship_to`；没给时从收件人的结构化字段
  拷贝（没有结构化字段的旧收件人 → `null`）。`shipping_address` 照收；只给了 `ship_to` 时按国家格式生成（都没有 → 400
  `40000`）。`PUT`（pending 的 DN）可以替换 `ship_to` / `shipping_options`。打包后用 `GET / PUT /warehouse/dn/<id>/ship-to`
  （`{ship_to}` → `{ship_to, ship_to_effective, shipping_address, locked, locked_reason, locked_message}`）改快照（如承运商文件报
  地址超长时改短），`shipping_address` 按新地址重新生成，报关快照不动。锁定同运单服务选项：有 `api` 运单或 `file` 运单
  （exported / active）→ 409 `16076`（`details.reason: SHIP_TO_LOCKED`），已发货 → 409 `16065`，已关闭 → 400 `16001`；
  手填运单号不锁。DN 详情返回 `ship_to`、`shipping_options` 和
  `ship_to_effective`（面单 / 文件实际用的地址：`ship_to`，没有用收件人的结构化字段，再没有 `address_line1` = `shipping_address`
  加收件人的姓名 / 电话 / 邮编）。route 的收件国：报关快照的 → `ship_to.country` → 收件人的。
- 字段不合法 → 400 `14019`（不是文字 / 超长，`field` 如 `ship_to.latin.name`）/ `14020`（国家代码）。不认识的键忽略；空值可以传
  `null`。
- 排版：`warehouse/common/address_format.py` 的 `format_lines(address, style='local' | 'latin')` / `format_single_line(...)`，按国家
  规则包（日本：〒邮编 / 都道府県市区町村 / 番地・建物；美国：街道 / `City, ST ZIP`；其它：街道 / 城市 州 邮编 / 国家）。
- 插件：`shipper` / `recipient` 原来的键照旧（FedEx 不变），另带 `local`（结构化地址）与 `latin`；国内件的收件人地址行 / 城市 /
  州取自 `ship_to`。

### 国家规则包

`warehouse/country_rules/`：`base.py`（`CountryRules`：`customs_line_problems(line)`、`customs_problems(ctx)`、
`exporter_tax_label`、`address_format`（local / latin 的行模板）、`phone_normalize`）、`registry.py`（`rules_for(国家)`，没登记的
国家用默认规则；第三方包经 entry point 组 `goodsmart_wms.country_rules` 登记）、`jp.py`、`us.py`。核心只调注册表。日本的报关检查
（9 位输出统计品目番号 `JP_EXPORT_CODE_MISMATCH` / `JP_EXPORT_CODE_MISSING` / `JP_EXPORT_VALUE_UNVERIFIED`）搬进 `jp.py`，文案不变；
在出口国（仓库的国家，没有用公司的）是日本时参与。日本出口方没设 `tax_id_label` 时单证改印 `Corporate No.`（默认规则：`Tax ID`）；
以前印 `Tax ID` 签发的单证不算过期。

### 运单服务选项

插件（`CarrierAdapter.service_options`，FedEx 为空）与文件模板（`service_options`）声明选项，如 `delivery_date`、
`delivery_time_slot`、`invoice_type`、`cod_amount`、`cool_type`。按 DN 的承运商取（`waybill_method: file` 用文件模板的，否则用在线
对接插件的）。

- `GET /warehouse/dn/<id>/shipping-options` → `{options, values, schema, defaults, locked, locked_reason, locked_message}`；
  `PUT {options: {...}}`（`packing_edit` / `delivery_edit`）整体替换 → 200 同样的内容。不是声明的键 / select 值不在列表 /
  日期（`YYYY-MM-DD`）/ 金额（≥ 0、最多 2 位小数）/ 文字不合法 → 400 `16139`（`field: options.<key>`；没带 `options` →
  `field: options`）。
- 有 `api` 运单或 `file` 运单（exported / active）后锁定 → 409 `16076`（`details.reason: SHIPPING_OPTIONS_LOCKED`，
  `carrier_shipment`）；已发货 → 409 `16065`。手填运单号不锁。
- `POST /warehouse/dn/` 可带 `shipping_options`：已声明的键按类型校验（400 `16139`，`field: shipping_options.<key>`），没声明的
  照收（承运商以后还可能换）；导出 / 建单时不用没声明的键。
- 建单时交给插件 `shipment['options']`（只含声明了的键）；导出时作为 `{options.<key>}`；已存的值不合法时导出报 `OPTION_INVALID`。
- 文件运单显示服务名称：`shipment.service_type` = 模板 `service_option` 那个选项的值，`service_name` = 它的名称（`service_label`
  带各语言）。

## 承运商对接（自动建运单）

DN 的承运商设置了在线对接时，WMS 可以直接在承运商系统建运单，替代「在承运商网站手工建运单、再把运单号存进 WMS」
这一步；其余流程不变：运单号走现有的保存运单号逻辑存到发货任务、完成发货、`dn.delivered` 照旧（CI / PL 不印运单号，
不用重签）。
对接与具体承运商无关：每家承运商一个插件（`warehouse/carrier/integrations/`），内置 FedEx（见「FedEx 插件」），也可以自己写
（见「编写承运商插件」）。没设对接的承运商照旧手工保存运单号（`PUT /warehouse/delivery/<task_id>/tracking`，记为 `manual` 运单），
这也是对接不可用时的退路。国内件走同一套，不碰报关（见「发货流程」）；报关扩展（CI / PL、商品明细、ETD、贸易单证）只在要报关时参与。

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
route, declared_value_carriage, delivery_task_id, tracking_number, shipment, warnings[]}`（只看自动运单 `source: api`；
`route` = `{origin_country, destination_country, international, requires_customs}`）：

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
  `label_parts[]`（面单存档里各文档的来源、箱号、类型、页数、是否存入）、`updated_at`、`source`（`api`）、`tracking_url`
  （公开查询页：承运商的 `tracking_url_template`，没填用插件内置的；`tracking_method: none` 时为 `null`）等。

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
  - 国内 / 跨境（取代原来的 `NOT_EXPORT`）：插件的 `scopes` 不含这票货的范围（国内 / 跨境；`SCOPE_NOT_SUPPORTED`，带 `scope`——
    FedEx 只接跨境件）、要报关却没有报关快照（`CUSTOMS_DATA_MISSING`，`field: customs`）、要报关而插件不能带报关
    （`CUSTOMS_NOT_SUPPORTED`）、插件不能建运单（`WAYBILL_NOT_SUPPORTED`）。有这几条时不再跑插件自己的检查；
  - DN：不是 `packed`（`NOT_PACKED`）或已发货（`DN_SHIPPED`）、已有有效运单
    （`SHIPMENT_EXISTS`）或已手工存过运单号（`TRACKING_NUMBER_EXISTS`）、没有箱子（`PACKAGES_MISSING`）/ 超过插件的箱数上限
    （`TOO_MANY_PACKAGES`，带 `limit`）/ 箱子缺重量或尺寸（`PACKAGE_INCOMPLETE`）；报关件：报关视图有错误级问题（码同报关视图）、
    收件人姓名和公司都缺（`RECIPIENT_NAME_MISSING`）或电话缺（`RECIPIENT_PHONE_MISSING`）；国内件：收件人姓名 / 电话缺
    （`RECIPIENT_NAME_MISSING` / `RECIPIENT_PHONE_MISSING`，`field: recipient.*`）、DN 没有收件地址（`RECIPIENT_ADDRESS_MISSING`）、
    仓库和公司都没有地址（`SHIPPER_ADDRESS_MISSING`）；
  - 插件自己的检查（地址放不放得下、贸易条件与关税付款方是否矛盾……；FedEx 见「FedEx 插件」）。
- **运送申告价额**高于已打包货值时自动压到已打包货值（承运商拒收申告价额高于报关货值的运单，FedEx 已实测；部分打包时只保
  实际发出的货）。实际提交的值记在 `shipment.declared_value`；`warnings` 里给 `{code: "DECLARED_VALUE_CAPPED", message,
  requested, applied}`（`GET` 在没有有效运单时作预告，`POST` 响应里是这次实际做的）。
- 报关件：没有当前有效的 CI / PL（或已过期）时先按现有逻辑签发；国内件不出单证。
- 插件开了电子单证（FedEx 为 `etd_enabled`）时，先把当前 CI PDF 上传给承运商，建单时引用；没开时仓库照旧打印 CI 随货。
- 交给插件的数据（与承运商无关，形状见 `CarrierAdapter`；每票都带 `route`，不要报关时 `commodities` / `invoice` / `documents`
  为 `null`）。国内件：发件人 = 公司名（本地，没有用英文名）、仓库地址（本地，没有用英文；仓库没有用公司的）、电话（仓库优先）；
  收件人 = DN 收件人（名称、联系人、电话、邮箱、邮编）+ DN 收件地址（一个字符串，`address_text` = `address_line1`）。
  报关件：发件人 = 公司出口资料（英文名、电话（仓库优先）、联系人、税号）
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
| `scopes` | 能承运的范围，默认 `('domestic', 'international')`；FedEx 为 `('international',)`（否则 `SCOPE_NOT_SUPPORTED`） |
| `supports_waybill` | 能建运单（建单 / 取消 / 面单），默认 `True`；只用来预约揽收 / 查跟踪的插件设 `False` |
| `supports_customs` | 能带报关扩展（`commodities` / `invoice` / `documents`），要报关的件必须；默认 `False`，FedEx 为 `True` |
| `supports_pickup` / `supports_tracking` | 预约揽收 / 查跟踪（见「上门揽收」/「跟踪」） |
| `tracks_any_number` | 能查不是本插件建的运单号（手填 / 文件导回的运单也照常跟踪）；FedEx 为 `True` |
| `tracking_url_template` | 公开查询页，`{tracking_number}` 占位；承运商自己没填 `tracking_url_template` 时用它 |
| `service_options` | DN 可带的运单服务选项（`({key, kind: select / date / money / text, label, values?, default?, help?}, …)`，默认为空）；WMS 校验后交给插件 `shipment['options']` |

设置项 `setting(key, kind, *, label, required=False, default=None, options=None, help=None, group='account', placeholder=None)`：

- `kind`：`text` / `secret`（加密保存、接口不回显）/ `select` / `boolean`。
- `label` / `help` 按语言给 `{'en-US': …, 'zh-CN': …, 'ja-JP': …}`，界面按当前语言取，没有用 `en-US`；`select` 的 `options`
  用 `option(value, en, zh=None, ja=None)` 生成；`group`：`account` / `shipping` / `label`（表单分组）。
- 实例上有 `self.settings`（非密钥，空值已换成 `default`）与 `self.secrets`。保存时 `select` 须在选项里、`boolean` 须为布尔值，
  否则 400 `16124`；必填项缺 → `CARRIER_NOT_CONFIGURED`；更多校验覆盖 `setting_problems()`（返回英文说明 → `CARRIER_CONFIG_INVALID`）。
- 约定的键：`default_label_format`（建单不指定时的打印方式）、`service_type`（记到建单记录上，`service_name()` 给显示名）。

方法（`shipment` 是 WMS 交给插件的一票货，纯数据：`reference`、`ship_date`、`route`、`shipper`、`recipient`、`packages[]`、
`commodities[]`、`invoice`、`documents`、`options`，字段见 `CarrierAdapter` 的文档字符串；`shipper` / `recipient` 另带 `local`
（结构化地址）与 `latin`；不要报关（`route.requires_customs` 为假）时
`commodities` / `invoice` / `documents` 为 `None`）：

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
| `GET /warehouse/dn/carrier-pickups?carrier_id=` | 本公司揽收方式是 `api` / `manual` 的承运商（`carriers`，不带 `carrier_id` 用第一个）、`method`、该承运商的待揽收运单（`active`、任一来源、DN 未发货；`can_schedule`）、最近 14 天的预约（结果不明的一律列出）、前置条件 `blockers`、揽收地点、`can_create`（插件预约）/ `can_record`（手工记录） |
| `GET /warehouse/dn/carrier-pickups/availability?carrier_id=` | 只查询：承运商开放的揽收日与可选时间（`options[{pickup_date, cutoff_time, access_minutes, ready_times, latest_times, default_ready_time, default_latest_time}]`） |
| `POST /warehouse/dn/carrier-pickups` | `{carrier_id?, shipment_ids, pickup_date, ready_time, close_time, remarks?, notification_email?}` → 201 `{pickup, alerts, message}` |
| `POST /warehouse/dn/carrier-pickups/manual` | 手工记一笔电话 / 网站约好的揽收（不调承运商），见「发货流程」 |
| `POST /warehouse/dn/carrier-pickups/<id>/cancel` | 取消已约上、揽收日没过的预约（手工记录的只改本地） |
| `POST /warehouse/dn/carrier-pickups/<id>/dismiss` | `{confirm: true}`：结果不明的预约向承运商核对后作废 |

- 权限与自动运单相同：读 `dn_read` / `packing_read` / `delivery_read`，写 `packing_edit` / `delivery_edit`。
- **揽收地点**：地址 = 仓库英文地址（没有用公司的），联系人 = 仓库联系人（没有用公司出口联系人），电话 = 仓库电话（没有用公司的），
  公司名 = 公司英文名，由插件转成承运商的格式（FedEx：与建运单的发件地址同一套拆分规则，电话须 8～13 位数字）。
  前置条件（409 `16130`）：`PICKUP_METHOD_NOT_API`（插件预约要求 `pickup_method: api`）/ `PICKUP_CARRIER_MISSING` /
  `CARRIER_NOT_INTEGRATED` / `PICKUP_NOT_SUPPORTED` / 与自动运单相同的
  对接问题（`CARRIER_INTEGRATION_DISABLED`、`CARRIER_NOT_CONFIGURED` 等）/ `PICKUP_ADDRESS_MISSING` / `PICKUP_ADDRESS_INVALID` /
  `PICKUP_PHONE_INVALID`。
- **时间**：日期 / 时间都是仓库当地的（仓库时区，见「时区」）。FedEx：`readyDateTimestamp` 带时区偏移
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

- **查哪些**：`active`、有运单号、承运商 `tracking_method: api` 的运单——插件建的，或插件 `tracks_any_number`（FedEx）时手填 /
  文件导回的——建单不超过 `CARRIER_TRACKING_MAX_DAYS` 天，DN 是 `packed` / `delivered`，
  或 DN 已完成（仓库手工签收）但跟踪还没到送达（只更新显示）。定时任务每 `CARRIER_TRACKING_INTERVAL_MINUTES` 分钟查一轮
  （最久没查的先查；按承运商分组、按插件的 `track_batch_size` 分批，一批失败只影响这一批；整轮受
  `CARRIER_TRACKING_BUDGET_SECONDS` 约束，用完的留到下一轮），调承运商时不占行锁。对接停用了照样查（已建的运单要跟到底）；
  运单记录的插件与承运商现在的对接插件不同时不查。揽收 / 送达时刻早于建单（手填 / 文件导回的运单：早于 DN 创建）12 小时以上
  （承运商回收再用的单号查到旧包裹）的结果不用，记成 `error`（`TRACKING.OLD_SHIPMENT`）。自动发货 / 签收记
  `completed_source` / `signed_source: carrier`。
- **自动发货**：承运商已经经手（插件给出 `picked_up_at`，或状态是 `in_transit` / `out_for_delivery` / `delivered`）而 DN 还是
  `packed` → 以建运单的人的名义完成发货（`pending` 的发货任务先开始；校验与手动「完成发货」相同：不查单证，计划发货日晚于揽收日
  照样拦），推 `dn.delivered`。发货时刻 = 承运商的揽收时刻（换成服务器本地时间）：发货任务的完成时间、实际发货日、DN 的
  `delivered_at`、`dn.delivered` 的 `shipped_at` 都是它，不是查到的时刻。
- **自动签收**：跟踪是 `delivered` 而 DN 是 `delivered` → 签收发货任务（签收时间 = 承运商的送达时刻，换成
  服务器本地时间，与 WMS 其他时间列同一口径；早于完成发货时刻的按完成发货时刻），DN 完成（`completed_at` 同签收时间），
  推 `dn.completed`（带 `tracking_number`、`shipped_at`、`signed_at`）。
- 做不成（如 16061 计划发货日在后）把原因记在运单的 `tracking_problem`（配送页显示），人处理后下一轮再试；
  做成了清空。手动完成发货 / 签收照常可用，谁先到算谁。
- **跟踪结果**存在 `dn_carrier_shipments.tracking_*`，运单状态接口的 `shipment.tracking`：
  `{status, code, status_text, picked_up_at, delivered_at, estimated_delivery, events[], error, synced_at, problem}`。
  `status` 是归一化的 `label_created` / `in_transit` / `out_for_delivery` / `delivered` / `exception` / `unknown`
  （`code` 是承运商自己的码，如 FedEx `OC` / `PU` / `DL`；认不出的码一律 `unknown`，不会触发自动发货 / 签收）。
  查询失败只记 `error`，保留上一次的结果。
- 状态 / 扫描记录有变化时推 `dn.tracking_updated`（`{dn_id, order_number, carrier, tracking_number, tracking{…}}`，
  `dedupe_key = dn:<id>`：没送出去的旧更新被新的取代，只送最新一条）。先自动发货 / 签收、后推跟踪。
- 手动刷新与定时任务撞上时，这一轮开始后别人已存了更新结果的不覆盖；运单已取消 / 换号的不写。
- `POST /warehouse/dn/<id>/carrier-shipment/track`（`delivery_edit`：可能当场完成发货 / 签收）立即查一次（约 40 秒时限）
  并照样处理，回运单状态 + `tracking_sync {tracked, updated, shipped, signed, problems, errors, skipped}`；
  没有可跟踪的运单 → 409 `16134`。
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
| `CARRIER_AUTO_SIGN_INTERVAL_MINUTES` | `60` | 自动视为签收定时任务的间隔（分钟，`carriers.auto_sign_days`，见「发货流程」），`0` = 不跑 |

账号、密钥、服务等按承运商存在对接设置里，不用环境变量；密钥用 `SETTINGS_ENCRYPTION_KEY` 加密（生产环境必须设置，见「快速开始」）。

## 时区

WMS 不假设所有仓库在同一个时区（设计见 `doc/timezone-utc.md`）。

- **存储**：时刻一律存 `timestamptz`（UTC）。模型用 `extensions/db.py` 的 `UTCDateTime`，Python 里拿到的都是带时区的 UTC
  `datetime`；当前时刻用 `warehouse/common/clock.now()`。只有日期的字段（计划 / 实际发货日、单证日期、揽收日……）保持
  `date`，表示**仓库当地**的日期。
- **仓库时区**：`warehouses.timezone`——IANA 名，如 `Asia/Tokyo`、`America/Los_Angeles`，可空，新建 / 编辑仓库时设置
  （`"timezone": null` 或 `""` = 用默认；不合法 → 400 `14022`，`field: timezone`）。生效时区 = 仓库的 → `WMS_DEFAULT_TIMEZONE`
  → `WMS_TIMEZONE` → `UTC`。业务日期都按它：实际发货日、单证日期、运单发货日与文件导出的发货日、揽收的「今天」、
  「预计到货日已过」、自动视为签收的 N 天（当地日历日）。不带偏移的输入时刻（`shipped_at`、`signed_at`、作业时间、
  `payment_time`、`scheduled_date`、列表的时间筛选……）按单据所属仓库的时区解释；只给日期的按当地 00:00（PDA 签收）。
- **接口输出**：所有时刻字段都是带偏移的 ISO 8601（UTC，`2026-10-25T01:02:03+00:00`）；日期字段仍是 `YYYY-MM-DD`。
  仓库对象（包括嵌在单据、任务、员工里的）带 `timezone`（生效值）；仓库列表 / 详情另带 `timezone_configured`（仓库自己设的；
  用默认时为 `null`，列表行里省略）。DN、ASN 详情和它们的流程接口（`GET /dn/<id>/shipping-flow`、`GET /asn/<id>/flow`）带
  顶层 `timezone`（单据所属仓库的）；列表行保留 `warehouse_id`。揽收（`GET /dn/carrier-pickups`、可约时间）返回该仓库的
  `today` / `timezone`。
- **Webhook**：`dn.*` 里的时刻（`started_at`、`picked_at`、`packed_at`、`shipped_at`、`signed_at`）是同一时刻、用 DN 仓库时区的
  偏移表示（`2026-10-23T13:03:00+09:00`）；`goods.spec_updated` 的 `changed_at` 用默认时区。接收方照常解析。
- 月度统计的「本月」按所筛仓库（没筛按默认）的时区；数据库按会话时区分月（PostgreSQL 为 `WMS_LEGACY_TIMEZONE`，SQLite 为 UTC）。

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `WMS_TIMEZONE` | （不设） | 旧版本写入数据的墙上时间口径，兼默认时区。任意 IANA 名（不合法拒绝启动）。也设成进程时区 `TZ`（日志时间、库存快照的 `SNAPSHOT_HOUR`） |
| `WMS_DEFAULT_TIMEZONE` | `WMS_TIMEZONE`，再没有为 `UTC` | 没设时区的仓库用的时区。没设它时仍读旧名 `DOCUMENT_TIMEZONE`（原来的 `Asia/Tokyo` 默认值取消） |
| `WMS_LEGACY_TIMEZONE` | `WMS_TIMEZONE`，再没有为 `UTC` | 还没转换的 `timestamp` 列里旧数据的墙上时间。PostgreSQL 连接的会话时区设成它，代码在转换前后都能正确工作 |

### 已有实例的升级

旧版本在 `timestamp without time zone` 列里存服务器的墙上时间。新代码按 `WMS_LEGACY_TIMEZONE` 解释它们，所以在一次性转换成
`timestamptz` 之前、之后都能正确工作：

1. 启动新版本**之前**把 `WMS_LEGACY_TIMEZONE`（或 `WMS_TIMEZONE`）设成旧数据写入时的时区——旧版本服务器 / `TZ` 的时区，
   如 `Asia/Shanghai`。按需设 `WMS_DEFAULT_TIMEZONE` 和各仓库的时区。
2. 部署、`flask db upgrade`（加 `warehouses.timezone`）、重启。
3. 备份数据库（`pg_dump -Fc`），最好先停应用，再转换：

   ```bash
   flask wms convert-utc --legacy-tz Asia/Shanghai           # 只列出要转换的列和 SQL
   flask wms convert-utc --legacy-tz Asia/Shanghai --apply   # 一个事务里执行；会要求确认（--yes 跳过）
   ```

   `public` 下所有 `timestamp without time zone` 列（不含 `alembic_version`）按 `USING col AT TIME ZONE '<旧口径时区>'`
   改为 `timestamptz`，每张表一条 `ALTER TABLE`；`wms_timezone_migrations` 里记一行，防止重复执行。任何一步出错整体回滚。
4. 再启动应用。

新装的实例由模型建表，本来就是 `timestamptz`，不需要转换；SQLite 存的就是 UTC。以后的迁移里时间列一律写
`sa.DateTime(timezone=True)`（autogenerate 会把 `UTCDateTime` 写成这样）。

生产用手动工作流 **Migrate WMS Time to UTC**（`.github/workflows/migrate-utc.yml` → `scripts/manual/migrate_utc.py`，与部署共用
`deploy-backend` 并发组）转换。不勾选 `apply` 只做检查（服务器提交、`/health`、迁移已到代码的最新版本、应用的旧口径时区与
`.env` 一致、要转换的列）并打印 SQL；勾选 `apply` 才停 `wms-api`、把校验过的 `pg_dump -Fc` 备份写到 `/var/www/wms-backups`
（0600，SHA-256 写进日志）、一个事务里转换、重启并探测 `/health`。提交前失败什么都没改；提交后不会自动回退（代码在转换后的
结构上照常工作），看工作流日志、`pm2 logs wms-api` 和备份再处理。

## 测试

测试用 SQLite 内存库和 fakeredis，本机不需要 PostgreSQL / Redis（`pip install -r requirements-dev.txt` 装 pytest、
pytest-xdist、fakeredis）。

```bash
scripts/test_fast.sh                        # 全量并行（pytest-xdist，进程数 = CPU 核数，最多 16），最后打印耗时
scripts/test_fast.sh tests/test_asn.py      # 只跑某个文件 / 目录 / 用例（单进程）
scripts/test_fast.sh -k revoke -x           # 其余参数原样交给 pytest
WORKERS=8 scripts/test_fast.sh              # 指定并行进程数（WORKERS=0 不并行）
PYTHON=../other/.venv/Scripts/python.exe scripts/test_fast.sh   # 指定解释器（默认依次找 .venv、venv、python3、python）
```

Windows：在 Git Bash 里运行脚本（`bash scripts/test_fast.sh`），或在 PowerShell / cmd 里直接调 pytest：
全量 `.venv\Scripts\python.exe -m pytest tests -n auto --dist worksteal -q`，单文件
`.venv\Scripts\python.exe -m pytest tests\test_asn.py`。`./run_tests.sh` 照旧单进程跑全部。

为什么快、为什么互不影响（`tests/helpers.py`、`tests/conftest.py`）：

- 每个进程只建一次表结构和基础数据（模板内存库）；每条用 `client` 的测试用 SQLite 的 backup API 复制一份（毫秒级），
  应用的引擎用 `StaticPool` 绑在这份副本上。业务代码照常 commit / rollback，测试之间看不到彼此的数据。
- Redis 换成 fakeredis，每个测试应用一个独立的假服务（token 吊销、IP 黑白名单、找回密码验证码）；限流用内存存储。
  测试里连 6379 端口会直接让该测试失败。
- 每条测试结束释放它建的应用：关引擎，并把 `Api` 从 flask-restx 的模块级命名空间上摘下（以前每条测试的应用和数据库都
  被它们引用着释放不了，每个进程涨到好几 GB）。
- PostgreSQL：`.github/workflows/test-postgres.yml` 用 `-p tests.pg_plugin` 跑库存 / 并发子集，每条测试重建表结构，不用
  SQLite 模板库。

## 部署

### 生产自动部署（GitHub Actions）

推送到 `main` 触发 `.github/workflows/deploy.yml`（同一时间只跑一个，`concurrency: deploy-backend`）：

1. 服务器工作区不干净（`git status --porcelain` 非空）→ 中止，什么都不动；部署前先探测一次当前服务，完全没有 HTTP 响应也中止（手动触发时可勾选 `skip_precheck` 跳过）
2. 记下部署前的提交，`fetch` + `reset --keep` → `pip install` → `flask db upgrade`（`lock_timeout=15s`），任一步失败都回到部署前的提交、不重启。迁移整条链在一个事务里，PostgreSQL 上失败会整体回滚
   → 权限种子 → `flask stock init-ledger`（库存流水期初，已有流水就跳过）
3. 重启后探测 `GET /health`（不鉴权、不查库，返回 `{status, commit}`），要求 `commit` 等于新的短哈希；失败就回滚代码、重启旧代码（**数据库不回滚**，所以迁移要保持旧代码也能跑：先加后删）
4. 探测通过后 `flask stock reconcile --apply`：把部署窗口里旧进程改了库存却没写流水的差额写成 `reconcile` 行（部署前的版本还没有库存流水时加 `--sync-legacy-counters`）。期初 / 校正失败不回滚，最后标记失败

PostgreSQL 上的测试：`.github/workflows/test-postgres.yml`（postgres:16 上跑迁移往返和库存 / 并发相关的测试子集）。

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
| 入库规则 | 17000-17999 | 400 / 409 | 收货规格采集（及以后的入库规则） |

出口单证相关业务码：`14019` 出口资料文本超长、`14020` 国家代码不合法、`16063` 报关结构不合法、`16064` 报关行商品编码不在 DN 明细或重复、`16065` 已发货不可改（409）、`16066` 箱子数据不合法、`16067` 当前状态不能改箱子（409）、`16068` 单证条件不全（409，`details.problems`）、`16070` 不是海外单（409）、`16071` 单证不存在（404）、`16117` 单证排不下——某块文字（通常是收件人信息）一页放不下，请缩短（409，`details {document, reason: LAYOUT_OVERFLOW}`）。承运商运单（自动建运单）：`16072` 建单 / 取消的前置条件不满足（409，`details.blockers`）、`16073` 承运商返回错误或结果不明（502，`details.errors` / `transaction_id` / `maybe_processed`）、`16074` 承运商超时或时间预算用完（504，`details.budget_exhausted`）、`16075` 没有可取消 / 可确认作废的运单记录（409，`details.unresolved`）、`16076` 有进行中 / 有效 / 结果不明 / 取消中的运单时不能改箱子或报关数据，也不能签发数据有变化的单证（409，`details {reason: CARRIER_SHIPMENT_OPEN | DOCUMENT_DATA_CHANGED, status, carrier, carrier_shipment {id, status, tracking_number}}`）、`16077` `label_format` 不合法或该承运商不支持（400）、`16078` 运单号 / 承运商与有效的自动运单不一致，或有自动运单时新建 / 删除发货任务（409）、`16079` 有结果不明 / 进行中的建单记录，或运单正在取消 / 取消结果不明——不能建单、存运单号、发货（409，`details.unresolved`）、`16093` 取消 / 确认作废请求里的目标与当前记录不符（409，`details {expected, actual}`）、`16094` 确认作废带运单号的记录时承运商没有确认已取消 / 不存在（409）、`16095` 承运商已建好运单但 WMS 写库失败，已补偿处理（502，`details {error, compensation, unresolved, …}`）。承运商在线对接：`16124` 对接设置不合法（400，`field`：`adapter` / `enabled` / `settings.<key>` / `secrets.<key>`）、`16125` 承运商还有未结束的自动运单，不能删除对接或换插件（409，`details.shipments`）。上门揽收：`16130` 前置条件不满足（409，`details.blockers`）、`16131` 预约参数不合法（400，`field`）、`16132` 所选运单不能预约（409，`details.shipments[{shipment_id, tracking_number, reason}]`）、`16133` 当前状态不能取消 / 作废（409，`details {pickup_id, status, reason, pickup_date}`）；跟踪：`16134` 没有能经 API 跟踪的有效运单（409）；承运商报错 / 超时同运单（`16073` / `16074`，`details.action` 为 `pickup` / `pickup_cancel` / `pickup_availability`）。已不再使用：`16069`（原：完成发货时单证缺失或已过期；完成发货不再查单证）；单证不再印运单号和承运商：`16080`（原：完成发货时运单号与 CI 上印的 AWB 不一致）、`16092`（原：完成发货时承运商与 CI 上印的不一致）。发货任务：`16090` DN 已有未完成的发货任务（409，`details {delivery_task_id, status}`）、`16091` 有报关快照或运单记录的 DN 不是当前发货任务（409，`details {delivery_task_id, current_status}`）、`16136` `shipped_at` 晚于现在（400，`field: shipped_at`；格式不对为 `16050`）。发货流程：`16135` 承运商对接方式 / 集荷安排 / 查询页 / `auto_sign_days` / 文件方式设置（`file_preset`、`file_template.<位置>`、`file_account.<key>`）不合法，或所选方式现在不可选（400，`field`）；文件方式：`16137` 有行有问题、整批不导出（409，`details.rows[{dn_id, order_number, problems[{column, code, message}]}]`）、`16138` 文件方式用不了：请求 / 导入文件不合法（400）或承运商不是文件方式 / 模板不全（409），带 `field`；`16139` 运单服务选项不合法（400，`field: options.<key>`）；`16076` 另有 `details.reason: SHIPPING_OPTIONS_LOCKED` / `SHIP_TO_LOCKED`，`16075` 另用于没有可作废的已导出文件运单（`details.waybill`）；结构化地址用 `14019`（不是文字 / 超长 / `zip_code` 与 `postal_code` 不同）与 `14020`（国家代码）；`16130` 另有 `PICKUP_METHOD_NOT_API`；`16131` 手工揽收另有 `field` `warehouse_id` / `confirmation_code`；建运单的 blocker `SCOPE_NOT_SUPPORTED` / `CUSTOMS_DATA_MISSING` / `CUSTOMS_NOT_SUPPORTED` / `WAYBILL_NOT_SUPPORTED` 取代 `NOT_EXPORT`。仓库：`14022` 时区不合法（不是 IANA 时区名，400，`field: timezone`）。错误响应可能带结构化的 `details`。

作业流程（见上文「作业流程规则」）：`16110` DN 有未结束的承运商运单，请先取消运单（409）、`16111` 调整单已取消（409）、`16112` 调整单当前状态不能取消（409）、`16113` 调整明细无法执行：库位上已没有该商品 / 明细没有库位（409）、`16114` 盘点单有未录入的明细（409，`details.detail_ids`）、`16115` 数值字段不合法（400，`field`）、`16116` 分拣任务的 ASN 不能改（409）、`16120` 移库来源库位没有这个商品（404）、`16121` 同一个 `client_batch_id` 已提交过不同的明细（409，`details {client_batch_id, batch_id}`）、`16122` 修改过调整单明细的人不能审批（409）、`16123` `client_batch_id` 不合法（400）、`44009` 库位有历史记录，请改用停用（409）。任务：`16088` 任务已停用（所属 DN / ASN 已取消，409）、`16089` 拣货一件都没拣不能完成（409）。库存：`15017` 补货阈值批量设置有错误行、全部不改（400，`details.errors[{index, goods_id, goods_code, field, code, message}]`）、`15018` 从破损 / 退货库位移回标准库位没有填原因（400，`field: reason`）、`15019` 库存流水 / 历史 / 分析接口的查询参数不合法（400，`field`）、`15020` 流水导出超过 50,000 行（400，`details {total, max_rows}`）；`15016` 待上架里的破损品只能上到破损库位，standard / return 库位只能用良品部分（`sorted_stock − sorted_damage_stock`）（400，`details {good_pending, damaged_pending}`）；`16162` 只能从 standard 库位拣货（400，`details {location_id, location_type}`）。待上架：`GET /warehouse/inventory/?pending_putaway=true`（只返回 `sorted_stock > 0` 的行，带 `sorted_damage_stock`、`pending_since`）与 `GET /warehouse/inventory/pending-putaway/summary`（doc/库存数值系统说明.md 第八节）。

收货规格采集：`17020` 采集设置不合法——收货通知的 `spec_capture`、仓库默认规则（400，`field`）、`17021` 分拣完成被拦：
分拣到的行还没采集规格、也没填跳过原因（409，`details {asn_id, enforcement, lines}`）、`17022` 采集请求不合法——既没有值也没有
跳过原因、长宽高没给齐、`remeasure` 没给齐要求的项、`note` 不合法（400，`field` / `details.missing`）、`17023` 只有已收货
（received）或已完成（completed，补采）的收货通知能采集（409，`details {asn_id, status}`）。

主数据与权限：`16100` 商品数值不合法（400）、`16101` CSV 内商品编码重复（400，带行号）、`16102` 数据值不合法（400）、`16103` 不能分配已停用的角色（400）、`16104` 只有公司管理员能新建 / 删除仓库或更换负责人（403）、`16105` 没有公司管理权限时只能管理绑定自己的 API Key（403）、`44004` 被单据或库存引用的主数据不能删除，请改用停用（409，`details {entity, id, references}`）、`44005` 与已有数据冲突（409）。密码：`10015` 新密码须 8–16 位，含大写字母、小写字母、数字和特殊符号（`@$!%*?&`），不能有其它字符（400，`field`，`details {min_length, max_length, special_characters}`；忘记密码重置、修改密码、新建 / 修改用户与员工都查）。内置权限 / 角色（输出 `is_builtin`）：`11011` 内置权限不能改代码、不能删（409，`details {permission, reason}`），`11012` 内置角色（admin / company_admin / warehouse_admin / warehouse_operator）不能改名、停用、删除（409，`details {role, reason}`）。商品导入导出（见「商品导入导出」）：`10004` 不是 .csv / .xlsx，`10005` / `10006` / `10012` / `10014` / `16100` / `16101` 带 `details {row, rows, code, columns, value}`，`10016` 文件读不了（`details.reason` `UNKNOWN_ENCODING` / `NOT_UTF8` / `NOT_SHIFT_JIS` / `NOT_XLSX` / `NOT_CSV`），`10017` 日期不合法，`10018` 超过 10,000 行 / Excel 过大，`10019` 导入参数不合法（`field`），`10020` 文字超长，`10021` 导出请求不合法（`field`），`10022` 导出超过 100,000 件，`16141` 预校验结果已过时（409，`details.reason` `FILE_CHANGED` / `DATA_CHANGED`），`16142` 文件不能下载（409，`details.status`），`16143` 只有失败的导出能重试（409）；可售量 `15019`（`field: goods_codes`）。

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
