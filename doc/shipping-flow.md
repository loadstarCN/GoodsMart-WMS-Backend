# 出库流程与承运商对接设计（三期）

本文是出库流程重构的设计约定：后端、管理端（Web）、PDA 按它实现。GoodsMart WMS 是开源程序，设计不假设
国家、承运商或是否签了 API 合同。

## 0. 原则

1. **一张出库单一条流程**：下单 → 拣货 → 打包 →（跨境时）报关单证 → 运单 → 揽收 → 交承运商 → 运输中 → 签收。
   出库单详情页是流程主页，每一步显示状态、**实际发生时刻**、来源（仓库 / 承运商 / 规则 / 来源系统）和可做的操作。
2. **承运商对接不依赖 API**：每项能力（运单、揽收、跟踪）各自选对接方式。没有 API 合同也能完整走流程。
3. **国内 / 跨境同一套**：跨境只是多一步「报关单证」，建运单时多带报关扩展。是否跨境按「发货国 → 收件国」判断，
   不写死某个国家。
4. **时间是事情发生的时刻**：承运商来的状态用承运商的时刻；人工操作可以填实际时刻（默认现在）；推送里带发生时刻。
5. **国家专有规则不进核心**：日本的输出统计代码、法人番号这类放在国家规则包里。

## 1. 名词

| 名词 | 含义 | 存在哪 |
|---|---|---|
| 运单（waybill） | 一张出库单交给承运商的一票货：运单号、面单、服务 | `dn_carrier_shipments`（一张 DN 同一时刻至多一条未结束的） |
| 运单来源 `source` | `api`（插件建单）/ `file`（文件导出 → 承运商软件打单 → 导回运单号）/ `manual`（手填或扫码运单号） | `dn_carrier_shipments.source` |
| 揽收 | 承运商来仓库收货 | `carrier_pickups`（`source`：`api` / `manual`） |
| 交承运商 | 完成发货（发货任务 completed、DN delivered） | `delivery_tasks.completed_at`、`dn.delivered_at` |
| 签收 | 发货任务 signed、DN completed | `delivery_tasks.signed_at`、`dn.completed_at` |
| 对接方式 | 承运商每项能力用哪种方式 | `carriers` 上的 `*_method` |

## 2. 承运商对接方式

| 能力 | `api` | `file`（第二期） | `manual` / 其它 |
|---|---|---|---|
| 运单 `waybill_method` | 插件建单、拿面单与运单号（承运商在线对接必须已启用且插件 `supports_waybill`） | 按承运商软件的导入格式导出 CSV，打完单把运单号 CSV 导回，按出库单号匹配 | `manual`：手填 / 扫码运单号，面单在承运商网站或软件打 |
| 揽收 `pickup_method` | 插件预约（`supports_pickup`） | — | `manual`：电话 / 网站约好后在 WMS 记一笔；`scheduled`：定期集荷，不用逐票预约；`dropoff`：自送网点；`none`：不管揽收 |
| 跟踪 `tracking_method` | 插件定时查（`supports_tracking`），揽收后自动交承运商、送达后自动签收 | （可选）导入配送状态 | `link`：只给公开查询页链接；`none` |

- 任何承运商都可以退回手工：有 API 的承运商，运单步骤也允许「手工填写运单号」（承运商系统故障时）。
- 手工 / 文件来源的运单号，只要该承运商的插件 `tracks_any_number`（能查非本系统建的单号，FedEx 可以），也照常跟踪。
- **自动视为签收**（`carriers.auto_sign_days`，默认空 = 关）：交承运商后 N 天还没签收、且这张运单没有 API 跟踪，
  定时任务按「交承运商时刻 + N 天」签收，来源记 `rule`。任何时候都可以手工确认签收（填实际时刻）。

### 2.1 carriers 新增列（第一期）

| 列 | 类型 | 说明 |
|---|---|---|
| `waybill_method` | varchar(10) not null default `'manual'` | `api` / `file` / `manual`；迁移时有启用的在线对接的设 `api` |
| `pickup_method` | varchar(10) not null default `'none'` | `api` / `manual` / `scheduled` / `dropoff` / `none`；迁移时插件支持预约的设 `api` |
| `pickup_schedule` | json null | `scheduled` 时：`{weekdays: [1..7], ready_time: 'HH:MM', close_time: 'HH:MM'}`（ISO 星期，1 = 周一） |
| `pickup_contact` | varchar(255) null | 约揽收的电话 / 网址 / 说明（`manual` 时显示） |
| `tracking_method` | varchar(10) not null default `'link'` | `api` / `link` / `none`；迁移时插件支持跟踪的设 `api` |
| `tracking_url_template` | varchar(500) null | 公开查询页，`{tracking_number}` 占位；为空时用插件内置的（插件可声明 `tracking_url_template`） |
| `auto_sign_days` | integer null | 自动视为签收的天数（1–90），空 = 关 |

校验：`api` 方式要求在线对接已启用且插件声明了对应能力，否则保存 400（`field` 指明哪一项）。
第二期再加 `file_template`（json）与 `file_preset`（varchar）。

### 2.2 插件接口（`CarrierAdapter`）补充的声明

```python
scopes = ('domestic', 'international')   # 能承运的范围；FedEx 插件 = ('international',)
supports_waybill = True                   # 能建运单（建单 / 取消 / 面单）
supports_customs = False                  # 能带报关扩展（跨境件必须）；FedEx = True
tracks_any_number = False                 # 能查非本插件建的单号；FedEx = True
tracking_url_template = None              # 公开查询页，如 'https://www.fedex.com/fedextrack/?trknbr={tracking_number}'
```

交给插件的一票货（`shipment`）新增 `route = {origin_country, destination_country, international, requires_customs}`；
`commodities` / `invoice` / `documents` 只在 `requires_customs` 时有值，否则为 `None`。
第二期地址改为结构化（见 §6.2），`shipper` / `recipient` 增加 `local` 与 `latin` 两套文字。

## 3. 国内 / 跨境（route）

- 发货国 `origin_country` = 仓库 `country_code`，没有用公司的。
- 收件国 `destination_country` = 报关快照的收件国（有报关快照时）→ DN 收件地址的国家（第二期结构化地址）→ 收件人 `country`。
- `international = origin != destination`（任一方未知时按国内）。
- `requires_customs = international and not 同一关税同盟`。关税同盟在 `warehouse/common/customs_unions.py` 里登记
  （默认 EU）；有报关快照的 DN 一律视为 `requires_customs`（来源系统明确给了报关数据）。
- 建运单（`api`）的拦截：
  - 插件 `scopes` 不含这票货的范围 → `SCOPE_NOT_SUPPORTED`。
  - `requires_customs` 而没有报关快照 → `CUSTOMS_DATA_MISSING`（取代原来的 `NOT_EXPORT`）。
  - `requires_customs` 而插件不 `supports_customs` → `CUSTOMS_NOT_SUPPORTED`。
- 报关是扩展（`warehouse/dn/customs_extension.py`）：只在 `requires_customs` 且有报关快照时参与——建单前签发 CI / PL、
  报关问题并入拦截、交给插件的 `commodities` / `invoice` / `documents`、贸易单证 / ETD、建单后重签。国内件建运单不碰报关。

## 4. 发货核心（与承运商、国内外无关）

- `warehouse/shipping/common.py`：DN 加锁、当前发货任务、`LOCKED_DN_STATUSES`、业务日期时区等（原来散在
  `CustomsService` 里，`CustomsService` 改为引用这里）。
- 箱子（`dn_packages`）归发货：`PUT /dn/<id>/packages` 不再要求报关；改箱子只在有报关快照时作废 CI / PL。
- 运单状态机不变（`pending` / `active` / `unknown` / `cancelling` / `cancelled` / `failed` / `dismissed`），只用于
  `source = 'api'`。**锁定规则只对 `api` 运单生效**（运单在承运商那边真实存在，换号要先取消）：
  - `manual` / `file` 运单换号 = 旧记录 `cancelled`（`reason = 'replaced'`）+ 新号成为 `active`，不调承运商。
  - 清空运单号 = 当前 `manual` / `file` 运单 `cancelled`（`reason = 'cleared'`）。
- 手填运单号（`PUT /delivery/<id>/tracking`、完成发货时带运单号）同时写 `manual` 运单记录（`carrier_id` = 任务的承运商，
  `carrier` = 该承运商在线对接的插件代码，没有对接为空）。已有 `api` 运单时规则照旧（16078 / 16079）。
- 揽收：`api` 预约照旧；新增 `manual` 记录（`POST /dn/carrier-pickups/manual`：日期、时间段、受理号、备注，状态直接
  `scheduled`，取消只改本地）。可以挂 `api` / `manual` / `file` 任一来源的有效运单。
- 跟踪：跟踪所有 `active` 运单中「承运商插件 `supports_tracking` 且（`source = 'api'` 或插件 `tracks_any_number`）」的；
  旧包裹判定的基准时刻：`api` 用建单时刻，`manual` / `file` 用 DN 创建时刻。
- 发货任务记来源：`delivery_tasks.completed_source`、`signed_source`（`warehouse` / `carrier` / `rule`，空 = 旧数据视为
  `warehouse`）。手工完成发货可传 `shipped_at`（实际交承运商时刻，默认现在，不能晚于现在）。
- 推送：`dn.completed` 增加 `signed_source`。

## 5. 流程汇总接口

`GET /warehouse/dn/<id>/shipping-flow`（权限 `dn_read`；Web 和 PDA 都用它渲染，不各自推算状态）：

```json
{
  "dn_id": 163, "dn_status": "delivered", "order_number": "SHP-261015-37210", "warehouse_id": 2,
  "route": {"origin_country": "JP", "destination_country": "AU", "international": true, "requires_customs": true},
  "carrier": {"id": 4, "name": "FedEx", "code": "fedex", "integration": "fedex",
              "methods": {"waybill": "api", "pickup": "api", "tracking": "api"},
              "auto_sign_days": null, "tracking_url": "https://www.fedex.com/fedextrack/?trknbr=878978126644"},
  "stage": "in_transit",
  "steps": [
    {"key": "ordered",   "state": "done", "at": "2026-10-15T16:56:51+09:00", "source": "api", "summary": {}, "actions": []},
    {"key": "picking",   "state": "done", "at": "...", "source": "warehouse", "summary": {"task_ids": [..], "picked": 14, "planned": 14}},
    {"key": "packing",   "state": "done", "at": "...", "summary": {"task_ids": [..], "package_count": 1, "gross_weight_kg": 3.858}},
    {"key": "documents", "state": "done", "at": "...", "summary": {"version": 4, "outdated": false, "missing": []}},
    {"key": "waybill",   "state": "done", "at": "...", "source": "api",
       "summary": {"tracking_number": "878978126644", "source": "api", "service": "...", "label_document_id": 9, "shipment": {...}},
       "actions": [{"key": "print_label", "enabled": true}, {"key": "cancel_waybill", "enabled": false, "reason": "...", "reason_code": "CANNOT_CANCEL"}]},
    {"key": "pickup",    "state": "done", "at": "...", "summary": {"method": "api", "pickup": {...}}},
    {"key": "handover",  "state": "done", "at": "2026-10-23T13:03:00+09:00", "source": "carrier"},
    {"key": "transit",   "state": "current", "at": "2026-10-24T00:19:00+09:00",
       "summary": {"tracking": {...}, "tracking_url": "..."}, "actions": [{"key": "refresh_tracking", "enabled": true}]},
    {"key": "delivered", "state": "todo", "at": null, "summary": {"estimated_delivery": "2026-10-30", "auto_sign_at": null},
       "actions": [{"key": "sign", "enabled": false, "reason": "...", "reason_code": "NOT_HANDED_OVER"}]}
  ],
  "problems": [{"code": "...", "level": "error|warning", "message": "...", "step": "waybill"}],
  "tasks": {"picking": [..], "packing": [..], "delivery": 126}
}
```

- `state`：`done` / `current` / `todo` / `warning`（要人处理）/ `blocked`（前一步没完成）/ `skipped`（不适用：国内件的
  `documents`、`pickup_method = none` 的 `pickup`、没有跟踪的 `transit`……）。
- `at`：这一步实际发生的时刻（带时区偏移）；`source`：`warehouse` / `carrier` / `rule` / `api`（`ordered` 为来源系统；
  `waybill` / `pickup` 为经承运商插件）/ `system`。
- `actions[].key`：`create_waybill`、`enter_tracking`、`cancel_waybill`、`dismiss_waybill`、`print_label`、`export_file`、
  `import_numbers`（第二期）、`book_pickup`、`record_pickup`、`cancel_pickup`、`complete_shipping`、`refresh_tracking`、
  `open_tracking`、`sign`、`issue_documents`、`edit_packages`；`enabled = false` 时带 `reason`（英文句子）和
  `reason_code`（界面按它翻译：`DN_SHIPPED`、`NOT_PICKED`、`NOT_PACKED`、`NO_WAYBILL`、`PICKUP_EXISTS`、`NO_OPEN_TASK`、
  `WAYBILL_UNRESOLVED`……见 `warehouse/shipping/flow.py` 的 `REASON_CODES`；也可能是建运单 / 预约揽收的拦截码或报关问题码）。
  操作可带额外字段（`delivery_task_id`、`document_id`、`pickup_id`、`url`）。界面按 key 接到已有接口。
- `warehouse_id`：DN 所属仓库（揽收按仓库约）。
- `stage`（列表用的阶段）：`preparing`（作业中）/ `awaiting_waybill` / `awaiting_number`（第二期：已导出待导回）/
  `awaiting_pickup` / `awaiting_handover` / `in_transit` / `exception`（运单结果不明、跟踪异常、自动步骤被挡）/
  `delivered` / `closed`。
- DN 列表接口返回每行的 `shipping_stage` 与 `shipping {carrier_id, carrier_name, tracking_number}`（承运商 = 当前发货任务的，
  没有用 DN 的），支持 `shipping_stage=` 筛选（SQL 判定，与上面同一口径）。

## 6. 第二期

### 6.1 文件方式（不需要 API 合同）

- 承运商的文件设置：`carriers.file_preset`（预设代码，如 `jp.yamato_b2cloud`）、`carriers.file_template`（json，
  在预设基础上改的部分；为空 = 完全用预设）、`carriers.file_account`（json，预设 `account_fields` 声明的账号项，
  如ヤマト的請求先顧客コード）。`waybill_method = 'file'` 要求有预设或模板。
- 模板（预设文件同一结构，草稿见调研结果）：
  ```
  {code, name{语言}, country, version, sources[], notes, tracking_url_template, tracking_url_fallback?,
   export: {format: 'csv', encoding: 'cp932' | 'utf-8' | 'utf-8-sig', delimiter, quoting: 'minimal' | 'all',
            header: bool, line_ending: '\r\n', length_unit: 'chars' | 'sjis_bytes', row_unit: 'dn',
            columns: [{header, value, required?, max_length?, notes?}]},
   import: {encoding, encoding_fallbacks[], header_rows, reference_column(1 起的列号), reference_value: 'DN{dn.id}',
            reference_parse: '^DN(\\d+)$', tracking_column, tracking_normalize: 'digits' | null,
            skip_rows_when?: {column, equals}},
   service_options: [{key, kind: 'select' | 'date' | 'money' | 'text', label{语言}, values?[{value, label}]}],
   account_fields: [{key, label{语言}, required, help?}]}
  ```
  预设里写的 `shift_jis` 一律按 cp932 读写（Python 的 shift_jis 编不了「～」「①」等）。
- 模板变量：`{dn.id}` `{dn.order_number}` `{ship_date}` `{ship_to.*}` `{shipper.*}`（结构化地址字段，见 6.2）
  `{goods.summary}` `{package.count}` `{package.total_weight_kg}` `{options.<key>}` `{carrier.account.<key>}`、字面量。
  过滤器：`|digits` `|truncate:n` `|truncate_bytes:n`（按 cp932 字节，不切断双字节字）`|slice:a:b` `|date:%Y/%m/%d`
  （空值输出空串）`|default:x` `|map:k=v,k=v` `|upper` `|zenkaku`（半角转全角）`|nospace`（去半角 / 全角空格）
  `|jp_phone`（+81 → 0 开头国内号码，全角数字转半角）`|int`。
- 导出前逐行检查：必填为空、超长（按 `length_unit`；地址 / 姓名超长不静默截断，报错让人改）、编码不了的字符、
  字段内换行（替换为空格）。有问题的行不导出，整批返回逐行问题。
- 匹配键：导出的「お客様管理番号」类列一律写 `DN{dn.id}`（订单号可能为空、不唯一、超长），订单号另放在面单上看得到的
  备注列。
- 预设放在 `warehouse/carrier/presets/<country>/<code>.json`（开源社区可补充），带版本与来源说明。第一批：
  `jp/yamato_b2cloud`、`jp/sagawa_ehiden3`、`jp/japanpost_yupri_r`（均标注「未在承运商软件实测」）。
- 导出：`POST /warehouse/dn/carrier-files/export {carrier_id, dn_ids, options?}` → 文件下载（文件名带承运商与时间）。
  每张 DN 建 `file` 运单记录：`status = 'exported'`（新状态，属于未结束状态），无运单号，记导出批次
  `carrier_file_batches`（id、carrier_id、warehouse_id、kind export / import、file_name、row_count、summary json、
  created_by、created_at）与 `dn_carrier_shipments.file_batch_id`。已是 `exported` 的 DN 可以重新导出（记录不变、
  批次更新）；作废导出记录 = `cancelled`（reason `discarded`，只改本地）。
- 导入：`POST /warehouse/dn/carrier-files/import`（multipart：`file`、`carrier_id`、`dry_run`）→ 按 `reference_parse`
  解析出 DN，写运单号；一张 DN 多行（多件）= 多个运单号，第一个为主单号，其余进 `package_tracking_numbers`；
  `exported` → `active`（没导出过的 DN 也接受，直接建 `file` 运单）；同步到发货任务。逐行结果：`matched` /
  `not_found` / `already_has_number`（号码不同，需人工决定）/ `same_number`（已导入过，忽略）/ `invalid`。
  `dry_run = true` 只返回预览，确认后再以 `dry_run = false` 提交。
- 流程：运单步骤 `waybill_method = file` 时给 `export_file`（单张导出）/ `import_numbers` / `discard_export`；
  阶段 `awaiting_number` = 已导出待导回。列表工作队列：「待建运单」批量导出、「待导回」批量导入。

### 6.2 结构化国际地址

- 结构化地址（同一组字段名用于收件人、仓库、DN 收件地址快照、交给插件 / 文件模板的 `ship_to` / `shipper`）：
  `country`（ISO 3166-1 alpha-2）、`postal_code`、`admin_area`（州 / 省 / 都道府县）、`locality`（市区町村）、
  `dependent_locality`、`address_line1`、`address_line2`、`organization`、`name`、`phone`、`email`，以及拉丁转写
  `latin: {name, organization, address_line1, address_line2, locality, admin_area}`（跨境面单用；没有时插件退回
  本地文字或报关快照里的英文收件人）。
- 收件人表加列：`admin_area`、`locality`、`dependent_locality`、`address_line1`、`address_line2`、`organization`、
  `latin`（json）；`zip_code` 即 `postal_code`（接口两个名字都收，输出两个都给）。旧的单行 `address` 保留：
  只给了结构化字段时按国家格式生成。
- 仓库表加列：`admin_area`、`locality`、`address_line1`、`address_line2`、`latin`（json；旧的 `address_en` 作为
  拉丁地址的单行写法保留）。
- DN 新增 `ship_to`（json 快照）：创建 DN 接口接受 `ship_to` 对象（可选）；没给时从收件人的结构化字段拷贝；
  旧的 `shipping_address` 字符串照收，只给了 `ship_to` 时按国家格式生成。DN 详情输出 `ship_to`。批发站改为传
  结构化地址（收件人 + `ship_to`）。
- 地址排版 `warehouse/common/address_format.py`：`format_lines(address, style='local' | 'latin')`，按国家规则包的
  格式（日本：〒邮编 / 都道府県市区町村 / 番地・建物；欧美：街道 / 城市 州 邮编 / 国家）。

### 6.3 国家规则包

`warehouse/country_rules/`：`base.py`（`CountryRules`：`customs_line_problems(line)`、`exporter_tax_label`、
`address_format`（local / latin 的行模板）、`phone_normalize`）、`registry.py`（按国家代码取，没有登记的用默认规则）、
`jp.py`（输出统计代码检查、Corporate No.、日本地址格式）。核心只调用注册表，不出现国家判断；原来散在报关服务里的
日本专有检查迁进 `jp.py`，行为不变。

### 6.4 运单服务选项

插件 / 文件预设声明 `service_options`（如配送日 `delivery_date`、时间带 `delivery_time_slot`、送り状种类
`invoice_type`、代收货款 `cod_amount`、冷藏 `cool_type`）。DN 新增 `shipping_options`（json）：创建 DN 时来源系统
可传，也可在流程的运单步骤里改（`PUT /warehouse/dn/<id>/shipping-options`，建运单 / 导出后锁定）。建单时交给插件
（`shipment['options']`），导出时作为 `{options.<key>}`。校验按当前承运商声明的选项（未知键 400、select 值不在
列表 400）。

### 6.5 其它

- 运单摘要显示服务名称（`service_name`）而不是服务代码。
- 揽收：交承运商后，还挂着的「已约」揽收显示为已完成（不再提示重约）。

### 6.6 落地时的澄清（第二期实现，迁移 `a7c4e2f9b3d1`）

- 导出请求体是 `{carrier_id, dn_ids（1–500）, warehouse_id?}`，不带 `options`：选项按每张 DN 自己的 `shipping_options`
  （没设的用选项的 `default`）。任何一行有问题整批不导出：409 `16137`（`details.rows[{dn_id, order_number, problems[{column,
  code, message}]}]`）；文件方式用不了 / 请求或导入文件不合法：`16138`（409 / 400，带 `field`）；选项不合法：400 `16139`。
- 模板另有：`service_option`（哪个选项代表服务种类，导出时记成运单的 `service_type`，`service_name` = 它的名称）、
  `export.charset: 'jis_x0208'`（只收 JIS 第1・第2水准的字，ヤマト用）。导入解码：候选编码里有 UTF-8 时，有 BOM 或是合法
  UTF-8 就按 UTF-8，否则按 `encoding` → `encoding_fallbacks` 顺序；`header_rows` 只在那一行不像数据行（匹配键解析不出 DN）时跳过。
- 导入的 `invalid` 包括：运单号为空、DN 已关闭、DN 的承运商不是这家、插件运单结果不明、号码已属于别的 DN、没有未完成的发货任务、
  没导出过就已发货。已导出、导回前就完成了发货的 DN 仍可导回（补上发货任务的运单号）。
- `exported` 期间手填了运单号 → 这次导出作废（`reason replaced`）；`file` 运单换号 / 清空同 `manual`。
- 运单服务选项：有 `api` 运单或 `file` 运单（exported / active）后锁定（409 `16076`，`details.reason SHIPPING_OPTIONS_LOCKED`），
  手填运单号不锁；创建 DN 时只校验已声明键的取值，没声明的键照收（承运商以后还可能换），导出 / 建单时不用。
- 流程汇总的运单步骤：文件运单已导出、待导回时 `summary.export = {exported_at（带偏移）, batch_id, file_name, export_count,
  exported_by: {id, user_name}}`，否则 `null`；导出 / 导回的明细在 `summary.shipment.file`（{preset, exported_at, export_count,
  import_batch_id, imported_at}）。服务名称多语言在 `shipment.service_label`（`service_name` 为英文）。
- 承运商接口另给只读的 `service_options`（与 DN 的 `shipping_options` 校验同一口径：文件方式用模板的，否则插件的），
  新建 DN 页按它显示选项。作废导出时已经没有导出记录 → 409 `16075`（`details.waybill`）。导出逐行问题里模板逐列的另带 `value`
  （这一列渲染出的文字）。
- 打包后改收件地址：`GET / PUT /warehouse/dn/<id>/ship-to`（`{ship_to}` → `{ship_to, ship_to_effective, shipping_address, locked,
  locked_reason, locked_message}`），如导出报地址超长（`TOO_LONG`）时改短再导出；单行 `shipping_address` 随之重新生成，报关快照
  不动。锁定口径同服务选项：有 `api` / `file`（exported / active）运单 → 409 `16076`（`details.reason SHIP_TO_LOCKED`），已发货
  409 `16065`，已关闭 400 `16001`；手填运单号不锁。
- 结构化地址：DN 详情另给 `ship_to_effective`（面单 / 文件实际用的地址：`ship_to` → 收件人的结构化字段 → 单行地址 + 收件人的
  姓名 / 电话 / 邮编）。`ship_to` 快照没带的姓名 / 电话 / 邮箱 / 邮编按收件人的补。字段不合法：400 `14019` / `14020`（国家）。
- 国家规则包按出口国（仓库的国家，没有用公司的）取；日本的报关检查只在出口国是日本时参与（以前 JPY 货值和统计番号格式的检查
  不看出口国）。日本出口方没设 `tax_id_label` 时单证印 `Corporate No.`，以前印 `Tax ID` 签发的单证不算过期。

## 7. 第三期

- **入库单流程主页**（轻量）：`GET /warehouse/asn/<id>/flow`：创建 / 预计到货 → 在途（入库单的运单号：插件能查就跟踪，
  否则查询页链接）→ 到货收货 → 分拣 → 上架 → 完成；入库单详情页用同一个流程组件。
- **PDA**：配送 / 打包页改用流程接口只读显示；界面文字多语言（中 / 英 / 日）。
- **时区通用化**：
  - 时间列改为带时区（`timestamptz`），存 UTC；代码用带时区的当前时刻。
  - 仓库增加 `timezone`（IANA），业务日期（实际发货日、单证日期、揽收日）按仓库时区算；界面按仓库时区显示。
  - `WMS_TIMEZONE` 不再限定 `Asia/Tokyo`，只作为旧数据的口径和新仓库的默认时区。
  - 生产库转换走一次性维护工作流（备份 → 一个事务内 `ALTER COLUMN … TYPE timestamptz USING … AT TIME ZONE <旧口径>` →
    重启探测），做法同 `doc/timezone-jst.md`。
