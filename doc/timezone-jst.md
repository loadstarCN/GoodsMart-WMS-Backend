# WMS 日本时间迁移（一次性）

WMS 的仓库现场和管理端均在日本。当前生产 PostgreSQL 会话是 `Asia/Shanghai`；后端
`datetime.now()` 与数据库 `now()` 写入的是同一套上海墙上时间。数据库字段大多是
`timestamp without time zone`，因此仅改服务器时区会让历史记录与新记录相差一小时。

## 变更

- 先发布支持 `WMS_TIMEZONE=Asia/Tokyo` 的后端代码。此时未设置开关，现网行为不变。
- 通过 `Migrate WMS Time to Japan` 手动工作流在服务器执行一次维护命令：校验代码、
  当前时区、115 个时间字段和应用健康状态；停止 `wms-api`；在
  `/var/www/wms-backups` 留下权限为 `0600` 的 `pg_dump -Fc` 备份并验证；在一个数据库
  事务中把系统生成的历史时间按 `Asia/Shanghai → Asia/Tokyo` 转换；设置 `.env` 的
  `WMS_TIMEZONE`；重启并探测服务。备份路径与 SHA-256 会写入工作流日志。
- 开关使 Python 的 `datetime.now()` 和 PostgreSQL 应用连接都采用 `Asia/Tokyo`。
  单证日期原本已用 `Asia/Tokyo`，不重复转换。

## 历史字段口径

脚本列出了生产库全部 115 个无时区时间字段，并在执行时再次对照，新增字段会使迁移
停止。`companies.expired_at`、`cycle_count_tasks.scheduled_date`、
`delivery_tasks.signed_at`、`payments.payment_time` 是用户指定的业务时间，保留原值。
迁移前核对的生产库中，四条 `signed_at` 均为人工输入，与签收状态日志时间不同；
`payment_time` 和 `scheduled_date` 均无非空值。分拣、拣货、打包记录的操作时间均有
服务器生成的微秒精度，按系统时间转换。日期字段保留；唯独系统据发货完成时刻自动
写入的 `delivery_tasks.actual_shipping_date`，在上海 23 点至 24 点跨日时一并校正。

## 中断处理

- 备份或数据库事务失败：事务回滚，恢复原 `.env`，尝试重启旧时间口径的 API。
- 数据库事务已提交：保留日本时间开关，绝不启动上海时间写入者。若健康探测失败，
  查看工作流日志、PM2 日志和备份，再决定恢复步骤。不要直接重跑转换；数据库中的
  `wms_timezone_migrations` 表阻止重复执行。
- 备份是迁移前完整快照；恢复会覆盖备份后的业务写入，必须在停止 API 后操作。

工作流与正常后端部署共享 `deploy-backend` 并发组，避免迁移期间有另一轮部署。
