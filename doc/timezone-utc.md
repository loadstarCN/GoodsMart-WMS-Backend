# 时区通用化：时间存 UTC，按仓库时区显示（出库流程第三期）

GoodsMart WMS 是开源程序，不能假设所有仓库都在同一个时区。`doc/timezone-jst.md` 那次迁移把生产库的
「墙上时间」从上海时间换成了日本时间，但字段仍是 `timestamp without time zone`，代码用 `datetime.now()`
（进程时区）写入。本次改为：**时刻一律带时区（UTC）存储与计算，业务日期按仓库时区算，界面按仓库时区显示。**

## 1. 存储

- 所有时刻列改为 `timestamptz`。模型统一用 `extensions/db.py` 的 `UTCDateTime`（`TypeDecorator`，impl
  `DateTime(timezone=True)`，`cache_ok = True`）：
  - 写入：带时区的值转成 UTC；不带时区的值视为**旧口径时区**（`WMS_LEGACY_TIMEZONE`，默认取 `WMS_TIMEZONE`，
    都没有为 UTC）的墙上时间再转 UTC（兼容尚未改完的调用方）；只给 `date` 的按旧口径时区当天 00:00；ISO 字符串先解析。
    PostgreSQL 绑定带时区的 UTC 值；SQLite / MySQL 不能存时区，存不带时区的 UTC。
  - 读出：PostgreSQL `timestamptz` 返回带时区值，统一转成 UTC；SQLite（测试）不存时区，读出的值按 UTC 补上；
    PostgreSQL 上**还没转换的 `timestamp` 列**读出不带时区的值，按旧口径时区解释——代码在转换前后都能正确工作。
  - 还没转换的 `timestamp` 列上，带时区的绑定值与 `db.func.now()` 由 PostgreSQL 按**会话时区**换成墙上时间：`config.py`
    给 PostgreSQL 连接固定设 `-c timezone=<WMS_LEGACY_TIMEZONE>`（生产、开发配置都设，不再只在设了 `WMS_TIMEZONE` 时），
    与读出时的解释一致。
- 日期列（计划发货日、实际发货日、单证日期、揽收日……）保持 `date`，表示**仓库当地**的日期。
- 当前时刻：`warehouse/common/clock.py` 的 `now()` 返回带时区的 UTC（system 模块与模型默认值用 `extensions.db.utc_now`，
  避免循环引用）；代码里不再用不带时区的 `datetime.now()` / `datetime.utcnow()` 产生要存或要比较的时刻。数据库默认值用
  `db.func.now()`（`timestamptz` 下即当前时刻）。耗时 / 超时预算用 `time.monotonic()`（承运商插件的 `clock()`），不受影响；
  与它同名的模块在 `warehouse/asn/tracking.py`、`warehouse/dn/{carrier,pickup,tracking}_services.py` 里以 `wms_clock` 导入。
- 以后的迁移里时间列一律写 `sa.DateTime(timezone=True)`（`migrations/env.py` 的 `render_item` 让 autogenerate 把
  `UTCDateTime` 写成这样）。

## 2. 仓库时区与业务日期

- `warehouses.timezone`（IANA 名，如 `Asia/Tokyo`、`America/Los_Angeles`；可空；迁移 `d4f2a8c6e1b7`）。生效时区 = 仓库的 →
  `WMS_DEFAULT_TIMEZONE` → `WMS_TIMEZONE` → `UTC`（`clock.warehouse_timezone`）。仓库新建 / 编辑可选，`null` / 空串 = 用默认，
  不是合法的 IANA 名（zoneinfo 认不出、超过 64 字符）→ 400 `14022`（`field: timezone`）。
- 环境变量都必须是合法的 IANA 名，否则启动即 `RuntimeError`；`WMS_TIMEZONE` 不再限定 `Asia/Tokyo`。旧的
  `DOCUMENT_TIMEZONE`（原默认 `Asia/Tokyo`）只在显式设置、且 `WMS_DEFAULT_TIMEZONE` 没设时当作默认时区（不合法的忽略并打
  warning）；不再有 `Asia/Tokyo` 的默认值。
- `clock.local_today(where)`、`clock.to_local(dt, where)`、`clock.local_date(dt, where)`、`clock.local_midnight(day, where)`；
  `where` 可以是仓库、仓库 id、带 `warehouse` / `warehouse_id` 的单据，发货任务等经 `dn` / `asn` 找仓库，也可以是时区名。
  按仓库时区的业务日期：
  - 实际发货日（交承运商时刻在仓库当地的日期，含承运商跟踪自动发货）、单证日期（签发时刻的当地日期）、运单 / 文件导出的发货日、
    揽收的「今天」与可约日期范围、FedEx 揽收的 `readyDateTimestamp` 偏移；
  - 「预计到货日已过」（ASN 流程）、揽收日是否已过（列表阶段的 SQL 用 `clock.local_today_expression(P.warehouse_id)`：只为
    设了时区、且今天与默认时区不同的仓库加 CASE 分支）；
  - 自动视为签收：完成时刻 + N 个**当地日历日**（跨夏令时切换也是同一钟点）；
  - 盘点任务名里的时刻、文件导出的文件名按仓库当地时间。
- 用户输入的时刻（`clock.parse_input_datetime`）：带偏移（含 `Z`）的按其偏移；不带的按**该单据所属仓库的时区**解释；只给
  日期的（PDA 签收）按仓库当地 00:00；结果一律是 UTC。用到的地方：完成发货的 `shipped_at`、签收的 `signed_at`、拣货 / 打包 /
  分拣的作业时间、`payment_time`（发货单的仓库）、盘点的 `scheduled_date`、公司的 `expired_at`（没有仓库：默认时区）。
- 列表的时间筛选：上架 / 下架 / 移库记录的 `start_time` / `end_time`、公司的 `expired_at_start` / `expired_at_end`（只给日期）
  按所筛仓库（没有按默认时区）当地的那一天，`end` 含当天整天（`clock.filter_time_range`）；盘点的计划时间范围、操作日志的
  `created_at`（时刻）不带时区的按所筛仓库 / 默认时区解释。
- 月度统计：「本月」按所筛仓库（没有按默认）的时区（`clock.stats_now`）；月份分组仍由数据库按会话时区提取（PostgreSQL 为
  `WMS_LEGACY_TIMEZONE`，SQLite 为 UTC），只在月初 / 月末的几个小时可能差一格。
- 库存快照的定时（`SNAPSHOT_HOUR`）按进程时区（设了 `WMS_TIMEZONE` 时就是它）；`snapshot_time` 是时刻。

## 3. 接口输出

- 所有时刻字段输出带偏移的 ISO 8601（UTC，`+00:00`）：flask-restx `fields.DateTime` 与 `to_dict()` 的 `isoformat()` 拿到的都是
  带时区的 UTC 值；流程接口的 `at`、`closed_at`、`exported_at`、`auto_sign_at`、跟踪的 `synced_at` 等手写的字段同样是 UTC
  （到秒）。日期字段仍是 `YYYY-MM-DD`。
- 仓库对象（仓库列表 / 详情，以及嵌在 DN、ASN、任务、库存、库位、员工……里的）都带 `timezone`（生效值）；仓库列表 / 详情另带
  `timezone_configured`（仓库自己设的，可空；列表行按 `skip_none` 省略空值，缺 = 没设）。输入的 `timezone` 是仓库自己的设置，
  界面不要把生效值原样提交回来（那样会把默认时区固定到仓库上）。
- DN、ASN 详情与流程接口（`GET /dn/<id>/shipping-flow`、`GET /asn/<id>/flow`）带顶层 `timezone`（单据所属仓库的生效时区）；
  列表行保留 `warehouse_id`（界面按仓库列表把 id 映射到时区）。揽收概览 / 可约时间的 `today` 与 `timezone` 是该仓库的。
- webhook 的 `event_time(value, where)` 输出该单据仓库时区的偏移（如 `+09:00`），表示同一时刻，接收方照常解析：`dn.*` 的
  `started_at` / `picked_at` / `packed_at` / `shipped_at` / `signed_at`，`dn.delivered` 的 `customs_documents[].issued_at`；
  `goods.spec_updated` 的 `changed_at` 没有仓库，用默认时区。`dn.tracking_updated` 里承运商的时刻照旧（插件归一成 UTC）。

## 4. 界面

- 管理端：时间按**单据所属仓库的时区**显示（列表逐行取该行仓库的时区；没有时用当前选择的仓库，再没有用浏览器
  时区），与浏览器时区不同时在时间旁标出时区缩写 / 偏移。时间输入框按仓库时区解释并带偏移提交。仓库编辑页可设时区。
- PDA：设备通常就在仓库，时间按仓库时区显示（接口给了时就用，否则用设备时区）。

## 5. 生产库转换（一次性维护）

- 代码先部署（转换前后都能工作，见 §1）。之后手动运行工作流 `Migrate WMS Time to UTC`
  （`.github/workflows/migrate-utc.yml` → `scripts/manual/migrate_utc.py`；与部署共用 `deploy-backend` 并发组）。
  不勾选 `apply` 时跑 `--dry-run`：只做下面第 1 步并打印 SQL，什么都不改；勾选后跑 `--apply`：
  1. 校验：服务器提交与工作流一致、已跟踪文件没有改动、`/health` 报的是当前提交、代码里有 `UTCDateTime` 与迁移
     `d4f2a8c6e1b7`、库的 `alembic_version` 等于代码的迁移 head、旧口径时区（`.env` 的 `WMS_LEGACY_TIMEZONE` → `WMS_TIMEZONE`，
     生产为 `Asia/Tokyo`）PostgreSQL 也认得、用当前代码起的应用进程读到的 `WMS_LEGACY_TIMEZONE` 与会话时区都是它、
     标记表里没有本次转换的记录；
  2. 停 `wms-api`；`pg_dump -Fc` 备份到 `/var/www/wms-backups`（0600，`pg_restore --list` 校验，SHA-256 写进日志），再核对一次列；
  3. 一个事务里（`lock_timeout 15s`、每条 `statement_timeout` 默认 15 分钟、advisory lock）把 `public` 下所有 BASE TABLE 的
     `timestamp without time zone` 列（不含 `alembic_version` 与标记表）改为 `timestamptz`，**每张表一条**
     `ALTER TABLE … ALTER COLUMN … TYPE timestamptz USING … AT TIME ZONE '<旧口径时区>', …`；核对所有列都已是
     `timestamp with time zone`、抽查一个值（转换前取 `activity_logs.created_at` 最新值，转换后按旧口径时区换回应不变）；
     标记表 `wms_timezone_migrations`（JST 那次已建，`CREATE TABLE IF NOT EXISTS`）写一行
     `wms_timestamp_to_timestamptz_2026_10`（防重复执行）；
  4. 重启并探测 `/health`。失败处理同 `doc/timezone-jst.md`：事务未提交 → 回滚，用同一份代码重启（库没变）；已提交 →
     不回退（代码在转换后的结构上照常工作），照样重启；健康探测失败看工作流日志、`pm2 logs wms-api` 和备份再处理。
     不要手动重跑：标记表会拒绝第二次执行。
- 转换的 SQL 生成、列筛选、事务内的步骤在 `timezone_conversion.py`（只依赖标准库，维护脚本与 CLI 共用；单元测试用假的游标，
  见 `tests/test_timezone.py`）。
- 自建实例：`flask wms convert-utc [--legacy-tz <IANA>] [--apply] [--yes] [--statement-timeout 15min]`。不带 `--apply` 只列出列与
  SQL；带 `--apply` 时提示先备份（`pg_dump -Fc`）、建议停应用并要求确认，同一个事务里执行同样的步骤（不管 pm2 / 备份）。
  `--legacy-tz` 不给时用配置的旧口径时区；`--legacy-tz`、`WMS_LEGACY_TIMEZONE`、`WMS_TIMEZONE` 都没有时拒绝 `--apply`
  （旧数据是旧版本服务器的墙上时间，不能默认按 UTC）。`--legacy-tz` 与配置不同时提醒先把 `WMS_LEGACY_TIMEZONE` 改对、重启应用。
  SQLite 不需要转换（命令直接说明并退出）。
- 转换后数据库会话时区不再影响数据（`timestamptz`），`connect_args` 的会话时区设置保留无害（影响 SQL 里的年月提取）。
- 新装的实例由模型建表即为 `timestamptz`，不需要运行转换；从旧版本升级的实例按上面运行一次（README「时区」）。
- `WMS_TIMEZONE` 不再限定 `Asia/Tokyo`：表示旧数据的墙上时间口径和默认时区；设了仍会设置进程时区（`TZ`）。
