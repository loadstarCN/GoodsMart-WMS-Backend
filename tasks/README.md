## Background Task Module

[中文 README](./README-zh.md)

This module contains all tasks that need to run in the background on a schedule or on demand. Tasks are triggered via Flask CLI commands — no external task queue service required.

## Task List

| Command | Description | Recommended Frequency |
|---------|-------------|----------------------|
| `flask snapshot run` | Create inventory snapshot for all warehouses | Daily |

## Inventory Snapshot Task

The snapshot task iterates through all warehouses and records the current inventory state in full, for historical queries and analysis.

### Manual Execution

```bash
cd /path/to/GoodsMart-WMS-Backend
flask snapshot run
```

Output example:

```
Snapshot completed in 0.35 seconds, 3 warehouses
```

### Trigger via API

```http
POST /tasks/task/inventory_snapshot
Authorization: Bearer <token>
```

Response example:

```json
{"message": "Snapshot completed in 0.35 seconds, 3 warehouses"}
```

### Scheduled Execution (crontab)

```bash
# Run every day at 2 AM
0 2 * * * cd /path/to/GoodsMart-WMS-Backend && flask snapshot run >> /var/log/wms-snapshot.log 2>&1
```

### Using Supervisord

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

## Adding New Tasks

To add a new scheduled task:

1. Create a new task file alongside `snapshot.py` (e.g., `cleanup.py`) with a plain Python function
2. Register the corresponding CLI command in `commands.py`
3. Mount it via `app.cli.add_command()` inside `create_app()` in `app.py`
4. Document the scheduling instructions in the README
