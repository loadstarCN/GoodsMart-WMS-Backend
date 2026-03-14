## 后台任务模块

[English README](./README.md)

本模块包含所有需要在后台定时或按需执行的任务，通过 Flask CLI 命令触发，无需额外的任务队列服务。

## 任务列表

| 命令 | 说明 | 建议频率 |
|------|------|----------|
| `flask snapshot run` | 为所有仓库创建库存快照 | 每天一次 |

## 库存快照任务

快照任务会遍历系统中的所有仓库，将当前库存状态完整记录一份，用于历史数据查询和分析。

### 手动执行

```bash
cd /path/to/GoodsMart-WMS-Backend
flask snapshot run
```

执行完成后会输出类似如下结果：

```
Snapshot completed in 0.35 seconds, 3 warehouses
```

### 通过 API 触发

```http
POST /tasks/task/inventory_snapshot
Authorization: Bearer <token>
```

返回示例：

```json
{"message": "Snapshot completed in 0.35 seconds, 3 warehouses"}
```

### 定时调度（crontab）

```bash
# 每天凌晨 2 点执行
0 2 * * * cd /path/to/GoodsMart-WMS-Backend && flask snapshot run >> /var/log/wms-snapshot.log 2>&1
```

### 使用 Supervisord 管理

```ini
[program:wms-snapshot]
directory=/path/to/GoodsMart-WMS-Backend
command=bash -c 'while true; do flask snapshot run; sleep 86400; done'
autostart=true
autorestart=true
stderr_logfile=/var/log/wms-snapshot.err.log
stdout_logfile=/var/log/wms-snapshot.out.log
user=www-data
```

## 新增任务

如需添加新的定时任务：

1. 在 `snapshot.py` 旁边新建任务文件（如 `cleanup.py`），编写普通 Python 函数
2. 在 `commands.py` 中注册对应的 CLI 命令
3. 在 `app.py` 的 `create_app()` 中通过 `app.cli.add_command()` 挂载
4. 参照本文档在 README 中补充调度说明
