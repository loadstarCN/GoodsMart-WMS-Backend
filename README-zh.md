# GoodsMart WMS API 服务

GoodsMart WMS (Warehouse Management System) API 是一个基于 Flask 构建的仓库管理系统后端服务。提供完整的 RESTful API 接口，支持仓库管理、库存控制、订单处理、用户权限管理等核心功能。

> **客户端支持**: 
> - 🖥️ https://github.com/loadstarCN/GoodsMart-WMS-Web (推荐)
> - 📱 移动端客户端 (开发中，即将发布)
> - 🔧 开发者可基于API自行开发定制客户端

## 📜 许可证

本项目采用 **GNU Affero General Public License v3.0 (AGPL-3.0)** 许可证。

### 重要许可条款：
- ✅ **允许**：自由使用、修改和分发
- ✅ **要求**：开源衍生作品，保持相同许可证
- ✅ **要求**：明确标注版权和许可信息
- ✅ **要求**：声明对原始代码的更改
- ❌ **禁止**：将本软件用于商业用途（需单独授权）
- ❌ **禁止**：闭源分发或SaaS服务（需单独授权）

**商业使用许可**：如需将本项目用于商业用途，请联系作者获取商业许可证。

## 🚀 技术栈

- **后端框架**: Flask 3.1.x
- **数据库**: PostgreSQL 13+
- **ORM**: SQLAlchemy + Flask-SQLAlchemy
- **认证授权**: JWT + Flask-JWT-Extended
- **API文档**: OpenAPI/Swagger
- **缓存**: Redis
- **定时任务**: Flask CLI + cron

## 📋 前置要求

在开始之前，请确保您的系统已安装：

- Python 3.8 或更高版本
- PostgreSQL 13 或更高版本
- Redis 6.x 或更高版本
- pip 包管理器
- https://github.com/loadstarCN/GoodsMart-WMS-Web 前端项目（需同时部署）

## ⚡ 快速开始

### 1. 克隆项目

```bash
git clone https://github.com/loadstarCN/GoodsMart-WMS-Backend.git
cd GoodsMart-WMS-Backend
```

### 2. 创建虚拟环境并安装依赖

```bash
python -m venv venv
source venv/bin/activate  # Linux/Mac
# 或
venv\Scripts\activate     # Windows

pip install -r requirements.txt
```

### 3. 环境配置

复制环境变量示例文件并配置您的环境变量：

```bash
cp .env.example .env
```

编辑 `.env` 文件，配置您的数据库和其他服务：

```env
# 应用配置
FLASK_ENV=development
FLASK_DEBUG=True

# 数据库配置
SQLALCHEMY_DATABASE_URI=postgresql://username:password@localhost:5432/warehouse_db

# JWT配置
JWT_SECRET_KEY=your_secure_random_jwt_secret_key_here

# Redis配置
REDIS_URL=redis://localhost:6379/0

# 客户端配置（指向前端项目地址）
CLIENT_BASE_URL=http://localhost:3000
```

### 4. 数据库初始化

```bash
# 初始化迁移环境
flask db init

# 创建初始迁移脚本
flask db migrate -m "Initial migration"

# 应用迁移到数据库
flask db upgrade
```

### 5. 启动开发服务器

```bash
flask run
# 或
python app.py
```

API 服务将在 http://localhost:5000 运行。

### 6. 部署客户端项目

请同时部署 https://github.com/loadstarCN/GoodsMart-WMS-Web 前端项目以使用完整功能：

```bash
# 在另一个终端中克隆并启动客户端
git clone https://github.com/loadstarCN/GoodsMart-WMS-Web.git
cd GoodsMart-WMS-Web
npm install
npm run dev
```

客户端将在 http://localhost:3000 运行。

## 🗂️ 项目结构

```
GoodsMart-WMS-Backend/
├── app.py                    # 主应用程序入口
├── config.py                 # 配置文件
├── .env                      # 环境变量文件
├── requirements.txt          # 项目依赖
├── migrations/               # 数据库迁移文件夹
│   ├── versions/             # 各版本迁移脚本
│   ├── alembic.ini           # Alembic 配置
│   └── env.py                # Alembic 环境配置
├── extensions/               # Flask 扩展初始化
│   ├── __init__.py
│   ├── db.py                 # SQLAlchemy 数据库扩展
│   ├── jwt.py                # JWT 扩展
│   ├── redis.py              # Redis 扩展
│   ├── cache.py              # 缓存扩展
│   └── oss.py                # 阿里云 OSS 扩展
├── system/                   # 系统核心模块
│   ├── __init__.py
│   ├── auth/                 # 认证授权模块
│   │   ├── __init__.py
│   │   ├── models.py
│   │   ├── views.py
│   │   ├── schemas.py
│   │   └── utils.py
│   ├── inventory/            # 库存管理模块
│   │   ├── __init__.py
│   │   ├── models.py
│   │   ├── views.py
│   │   ├── schemas.py
│   │   └── utils.py
│   ├── order/                # 订单管理模块
│   │   ├── __init__.py
│   │   ├── models.py
│   │   ├── views.py
│   │   ├── schemas.py
│   │   └── utils.py
│   ├── warehouse/            # 仓库管理模块
│   │   ├── __init__.py
│   │   ├── models.py
│   │   ├── views.py
│   │   ├── schemas.py
│   │   └── utils.py
│   └── ...                   # 其他模块
├── tasks/                    # 后台任务模块
│   ├── __init__.py
│   ├── commands.py           # Flask CLI 命令（flask snapshot run）
│   ├── snapshot.py           # 库存快照任务
│   └── views.py              # 任务触发 API 接口
├── utils/                    # 工具函数
│   ├── __init__.py
│   ├── validators.py         # 数据验证器
│   ├── helpers.py            # 辅助函数
│   └── exceptions.py         # 自定义异常
├── tests/                    # 测试文件夹
│   ├── __init__.py
│   ├── conftest.py           # pytest 配置
│   ├── test_auth.py          # 认证测试
│   ├── test_inventory.py     # 库存测试
│   └── ...                   # 其他测试
└── docs/                     # 文档目录
    ├── api.md                # API 文档
    ├── deployment.md         # 部署指南
    └── ...                   # 其他文档
```

## 🔧 开发指南

### 数据库管理

#### 初始化迁移环境
```bash
flask db init
```

#### 创建迁移脚本
```bash
flask db migrate -m "描述性迁移消息"
```

#### 应用迁移
```bash
flask db upgrade
```

#### 回滚迁移
```bash
flask db downgrade
```

### 依赖管理

#### 使用 pip-review 更新依赖
```bash
# 检查可更新的包
pip-review --local

# 自动更新所有包
pip-review --local --auto

# 更新 requirements.txt
pip freeze > requirements.txt
```

#### 安装 PostgreSQL 开发包

**Ubuntu/Debian:**
```bash
sudo apt-get install libpq-dev python3-dev
```

**CentOS/RHEL:**
```bash
sudo yum install postgresql-devel python3-devel
```

### 运行测试

```bash
# 安装测试依赖
pip install pytest pytest-cov

# 运行所有测试
pytest tests/

# 运行特定测试文件
pytest tests/test_auth.py

# 生成测试覆盖率报告
pytest --cov=system tests/
```

## 📦 API 功能模块

### 认证授权模块
- 用户注册/登录
- JWT Token 管理
- 权限控制
- 角色管理

### 库存管理模块
- 商品信息管理
- 库存查询和调整
- 库存预警
- 批次管理

### 订单管理模块
- 订单创建和处理
- 订单状态跟踪
- 发货管理
- 退货处理

### 仓库管理模块
- 仓库信息管理
- 库位管理
- 库存盘点
- 调拨管理

## 🚀 部署

### 完整系统部署要求

要运行完整的 GoodsMart WMS 系统，您需要同时部署：

1. **本 API 服务** (当前项目)
2. **https://github.com/loadstarCN/GoodsMart-WMS-Web**
3. **PostgreSQL 数据库**
4. **Redis 服务器**

### 使用 Gunicorn (生产环境)

```bash
pip install gunicorn
gunicorn -w 4 -b 0.0.0.0:5000 app:app
```

### 使用 Supervisord

创建 `/etc/supervisor/conf.d/wms-api.conf`：

```ini
[program:wms-api]
directory=/path/to/GoodsMart-WMS-Backend
command=gunicorn -w 4 -b 0.0.0.0:5000 app:app
autostart=true
autorestart=true
environment=FLASK_ENV="production"
stderr_logfile=/var/log/wms-api.err.log
stdout_logfile=/var/log/wms-api.out.log
user=www-data
```

管理命令：
```bash
# 重启服务
sudo supervisorctl restart wms-api

# 查看状态
sudo supervisorctl status

# 重新加载配置
sudo supervisorctl reread
sudo supervisorctl update
```

### 客户端项目部署

请参考 https://github.com/loadstarCN/GoodsMart-WMS-Web 项目的 README 文件进行客户端部署。

### 使用 Docker (可选)

提供 Dockerfile 和 docker-compose.yml 用于容器化部署完整系统。

## Webhook 集成（Wholesale API 对接）

WMS 支持通过 Webhook 向外部系统（如 Wholesale API）推送事件通知。

### 数据库迁移

首次部署或升级时，执行 `doc/migration-webhook-integration.sql` 完成数据库变更：

```bash
psql $SQLALCHEMY_DATABASE_URI -f doc/migration-webhook-integration.sql
```

该脚本会：
- `asn` 表新增 `order_number` 字段（关联 Wholesale 到货单号）
- `api_keys` 表新增 `company_id`、`webhook_url`、`webhook_secret` 字段
- 创建 `webhook_events` 事件队列表

### 配置 API Key

为需要接收 Webhook 推送的外部系统配置对应的 API Key：

```sql
UPDATE api_keys
SET company_id    = 1,
    webhook_url   = 'https://your-system.example.com/api/webhook/wms',
    webhook_secret = 'your-hmac-secret-here'
WHERE key = 'your-api-key-here';
```

| 字段 | 说明 |
|------|------|
| `company_id` | 关联的公司 ID |
| `webhook_url` | 接收事件推送的目标地址 |
| `webhook_secret` | 用于签名验证的 HMAC 密钥 |

### 定时推送任务

Webhook 事件通过 `flask webhook push` 命令批量推送。建议使用 crontab 每分钟执行一次：

```bash
# 编辑 crontab
crontab -e

# 添加以下行（每分钟推送一次待发送事件）
* * * * * cd /path/to/GoodsMart-WMS-Backend && flask webhook push >> /var/log/wms-webhook.log 2>&1
```

或使用 Supervisord 管理推送进程：

```ini
[program:wms-webhook]
directory=/path/to/GoodsMart-WMS-Backend
command=bash -c 'while true; do flask webhook push; sleep 60; done'
autostart=true
autorestart=true
stderr_logfile=/var/log/wms-webhook.err.log
stdout_logfile=/var/log/wms-webhook.out.log
user=www-data
```

## 库存快照定时任务

WMS 支持通过 `flask snapshot run` 命令对所有仓库的当前库存进行快照记录，用于历史数据查询、库存变动分析和报表统计。

### 触发方式

**通过 API 触发（手动执行一次）：**

```http
POST /tasks/task/inventory_snapshot
Authorization: Bearer <token>
```

**通过命令行触发：**

```bash
cd /path/to/GoodsMart-WMS-Backend && flask snapshot run
```

### 配置定时任务（crontab）

建议每天定时执行一次快照，例如每天凌晨 2 点：

```bash
# 编辑 crontab
crontab -e

# 每天凌晨 2 点执行库存快照
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

## 错误码分类体系

| 类别 | 错误码范围 | HTTP状态码 | 描述 |
|------|------------|------------|------|
| **通用错误** | 10000-10999 | 400 | 通用业务逻辑错误 |
| **认证错误** | 11000-11999 | 401 | 身份验证相关问题 |
| **权限错误** | 12000-12999 | 403 | 访问权限不足 |
| **资源错误** | 13000-13999 | 404 | 资源不存在 |
| **数据验证错误** | 14000-14999 | 400 | 数据格式或内容错误 |
| **库存错误** | 15000-15999 | 400/403 | 库存相关业务错误 |
| **状态流转错误** | 16000-16999 | 400 | 状态转换相关错误 |

## 详细错误码分配

### 1. 通用错误 (10000-10999)
| 错误码 | 错误消息 | 说明 |
|--------|----------|------|
| 10001 | Invalid file type. Only PNG, JPG, JPEG, and GIF are allowed. | 文件类型错误 |
| 10002 | OSS upload failed: {e} | OSS上传失败 |
| 10003 | Old password is incorrect | 旧密码不正确 |
| 10004 | Only CSV files are supported. | 仅支持CSV文件 |
| 10005 | Missing required columns: {', '.join(missing_columns)} | 缺少必需列 |
| 10006 | Row {row_num}: Missing values for {', '.join(missing_values)} | 行数据缺少值 |

### 2. 认证错误 (11000-11999)
| 错误码 | 错误消息 | 说明 |
|--------|----------|------|
| 11001 | Invalid username or password | 用户名或密码错误 |
| 11002 | Token is invalid | Token无效 |
| 11003 | Unauthorized | 未授权访问 |
| 11004 | No current user found | 未找到当前用户 |
| 11005 | Current user is not a staff member | 当前用户不是员工 |
| 11006 | User is not active | 用户未激活 |
| 11007 | Company is expired | 公司已过期 |

### 3. 权限错误 (12000-12999)
| 错误码 | 错误消息 | 说明 |
|--------|----------|------|
| 12001 | Access denied: insufficient permissions | 权限不足（通用） |
| 12002 | Your IP is blacklisted. | IP被列入黑名单 |
| 12003 | Your IP is not whitelisted. | IP不在白名单中 |
| 12004 | API Key not found. | API密钥未找到 |
| 12005 | Current user is not a staff member. | 用户不是员工类型 |
| 12005 | Unsupported user type. | 不支持的用户类型 |

### 4. 资源错误 (13000-13999)
| 错误码 | 错误消息 | 说明 |
|--------|----------|------|
| 13001 | Resource not found | 资源未找到（通用） |
| 13002 | User not found | 用户未找到 |
| 13003 | Goods not found in the specified location | 指定位置未找到商品 |
| 13004 | GoodsLocation not found | 商品位置未找到 |
| 13005 | Warehouse not found | 仓库未找到 |


### 5. 数据验证错误 (14000-14999)
| 错误码 | 错误消息 | 说明 |
|--------|----------|------|
| 14001 | Invalid format  | 格式错误 |
| 14002 | Warehouse is not active | 仓库未激活 |
| 14003 | User must provide a valid warehouse_id when force_warehouse is True | 需要提供有效的仓库ID |
| 14004 | Missing 'details' in request data | 请求数据缺少'details' |
| 14005 | No details provided in request data | 请求数据未提供明细 |
| 14006 | The from_location_id and to_location_id cannot be the same | 源和目标位置不能相同 |
| 14007 | Invalid status value: {status} | 无效的状态值 |
| 14008 | Invalid required_permissions format. Must be a string or list of permission strings. | 权限格式无效 |
| 14009 | Invalid warehouse ID | 无效的仓库ID |


### 6. 库存错误 (15000-15999)
| 错误码 | 错误消息 | 说明 |
|--------|----------|------|
| 15001 | Stock is not enough | 库存不足（通用） |
| 15002 | Insufficient stock for goods_id={goods_id} in location_id={location_id} | 指定位置商品库存不足 |
| 15003 | Insufficient stock to lock | 库存不足无法锁定 |
| 15004 | Insufficient locked stock to unlock | 锁定库存不足无法解锁 |
| 15005 | Not enough ASN stock. | ASN库存不足 |
| 15006 | Not enough received stock. | 收货库存不足 |
| 15007 | Not enough sort stock. | 分拣库存不足 |
| 15008 | Not enough DN stock. | DN库存不足 |
| 15009 | Not enough pick stock. | 拣货库存不足 |
| 15010 | Not enough packed stock. | 打包库存不足 |
| 15011 | Not enough delivered stock. | 配送库存不足 |
| 15012 | Not enough DN stock to close. | DN库存不足无法关闭 |
| 15013 | Low stock threshold must be non-negative or -1 to disable. | 低库存阈值无效 |
| 15014 | High stock threshold must be non-negative or -1 to disable. | 高库存阈值无效 |
| 15015 | High stock threshold must be greater than low stock threshold. | 高库存阈值必须大于低库存阈值 |

### 7. 状态流转错误 (16000-16999)
| 错误码 | 错误消息 | 说明 |
|--------|----------|------|
| 16000 | Invalid state transition: {action} cannot be performed on resource in {current_state} state | 无效状态转换：资源处于 {current_state} 状态时无法执行 {action} 操作 |
| 16001 | Cannot update a non-pending resource | 无法更新非待处理资源 |
| 16002 | Cannot delete a non-pending resource | 无法删除非待处理资源 |
| 16003 | Cannot add details to a non-pending resource | 无法向非待处理资源添加明细 |
| 16004 | Cannot update details in a non-pending resource | 无法更新非待处理资源的明细 |
| 16005 | Cannot delete details from a non-pending resource | 无法从非待处理资源删除明细 |
| 16006 | Cannot sync details in non-pending resource | 无法同步非待处理资源的明细 |
| 16007 | Cannot process a non-pending resource | 无法处理非待处理资源 |
| 16008 | Cannot complete a non-in_progress resource | 无法完成非进行中资源 |
| 16009 | Cannot create detail in a non-in-progress resource | 无法在非进行中资源创建明细 |
| 16010 | Cannot update detail in a non-in-progress resource | 无法更新非进行中资源的明细 |
| 16011 | Cannot delete detail in a non-in-progress resource | 无法删除非进行中资源的明细 |
| 16012 | Cannot create batch in a non-in-progress resource | 无法在非进行中资源创建批次 |
| 16013 | Cannot update batch in a non-in-progress resource | 无法更新非进行中资源的批次 |
| 16014 | Cannot delete batch in a non-in-progress resource | 无法删除非进行中资源的批次 |
| 16015 | 'details' must be a list | 明细必须为列表 |
| 16016 | Resource has no details | 资源无明细 |
| 16017 | Detail is already completed | 明细已完成 |
| 16018 | Cannot approve a resource that is not pending | 无法批准非待处理资源 |
| 16019 | Cannot complete a resource that is not approved | 无法完成未批准资源 |
| 16020 | No differences found in resource details | 资源明细无差异 |
| 16021 | Cannot receive a non-pending resource | 无法接收非待处理资源 |
| 16022 | Cannot close a non-pending resource | 无法关闭非待处理资源 |
| 16023 | Cannot update a completed or signed resource | 无法更新已完成或签收的资源 |
| 16024 | Cannot sign a non-completed resource | 无法签收未完成资源 |
| 16025 | Duplicate goods_id: {goods_id} | 重复商品ID |
| 16026 | goods_id and warehouse_id are required to create an inventory record. | 创建库存记录需要商品ID和仓库ID |
| 16027 | Manager does not belong to the same company | 经理不属于同一公司 |
| 16028 | Cannot cancel a non-pending resource | 无法关闭非待处理资源 |


## 🤝 贡献指南

我们欢迎任何形式的贡献！请阅读我们的贡献指南：

1. Fork 本项目
2. 创建特性分支 (`git checkout -b feature/AmazingFeature`)
3. 提交更改 (`git commit -m 'Add some AmazingFeature'`)
4. 推送到分支 (`git push origin feature/AmazingFeature`)
5. 开启一个 Pull Request

## 🆘 支持

如果您遇到任何问题或有任何疑问，请：

1. 查看 docs/api.md
2. 搜索 https://github.com/loadstarCN/GoodsMart-WMS-Backend/issues
3. 确保已正确部署客户端项目
4. 提交新的 Issue

## 🔗 关联项目

本项目是 GoodsMart WMS 系统的一部分，相关项目链接：

https://github.com/loadstarCN/GoodsMart-WMS - 包含完整系统的文档和协调信息  
https://github.com/loadstarCN/GoodsMart-WMS-Web - 配合使用的前端系统

建议开发者同时关注主仓库以获取最新系统更新和完整文档。

## 🙏 致谢

感谢所有为这个项目做出贡献的开发者。

---

**重要提示**: 
- 这是一个开源项目，请确保不要将任何敏感信息提交到版本控制系统
- 使用环境变量来管理敏感配置
- 本项目采用AGPLv3许可证，**禁止商业使用**，如需商业用途请联系作者获取商业许可
- **官方客户端项目**:
  - https://github.com/loadstarCN/GoodsMart-WMS-Web
  - 移动端客户端 (即将发布)
- 开发者可基于API自行开发定制客户端
