# GoodsMart WMS API Service

[中文 README](./README-zh.md)

GoodsMart WMS (Warehouse Management System) API is a backend service for warehouse management built on Flask. It provides comprehensive RESTful API interfaces supporting core functionalities including warehouse management, inventory control, order processing, and user permission management.

> **Client Support**:
> - 🖥️ https://github.com/loadstarCN/GoodsMart-WMS-Web (Recommended)
> - 📱 Mobile Client (In Development, Coming Soon)
> - 🔧 Developers can build custom clients based on the API

## 📜 License

This project is licensed under the **GNU Affero General Public License v3.0 (AGPL-3.0)**.

### Important License Terms:
- ✅ **Permitted**: Free use, modification, and distribution
- ✅ **Required**: Open source derivative works, maintain same license
- ✅ **Required**: Clear attribution of copyright and license information
- ✅ **Required**: Declaration of changes to original code
- ❌ **Prohibited**: Commercial use of this software (separate authorization required)
- ❌ **Prohibited**: Closed-source distribution or SaaS services (separate authorization required)

**Commercial Use License**: To use this project for commercial purposes, please contact the author for a commercial license.

## 🚀 Tech Stack

- **Backend Framework**: Flask 3.1.x
- **Database**: PostgreSQL 13+
- **ORM**: SQLAlchemy + Flask-SQLAlchemy
- **Authentication & Authorization**: JWT + Flask-JWT-Extended
- **Task Queue**: Celery + Redis
- **API Documentation**: OpenAPI/Swagger
- **Message Queue**: MQTT (IoT Device Integration)
- **Caching**: Redis

## 📋 Prerequisites

Before starting, ensure your system has:

- Python 3.8 or higher
- PostgreSQL 13 or higher
- Redis 6.x or higher
- pip package manager
- https://github.com/loadstarCN/GoodsMart-WMS-Web frontend project (requires simultaneous deployment)

## ⚡ Quick Start

### 1. Clone the Project

```bash
git clone https://github.com/loadstarCN/GoodsMart-WMS-Backend.git
cd GoodsMart-WMS-Backend
```

### 2. Create Virtual Environment and Install Dependencies

```bash
python -m venv venv
source venv/bin/activate  # Linux/Mac
# or
venv\Scripts\activate     # Windows

pip install -r requirements.txt
```

### 3. Environment Configuration

Copy the environment variable example file and configure your environment variables:

```bash
cp .env.example .env
```

Edit the `.env` file to configure your database and other services:

```env
# Application Configuration
FLASK_ENV=development
FLASK_DEBUG=True

# Database Configuration
SQLALCHEMY_DATABASE_URI=postgresql://username:password@localhost:5432/warehouse_db

# JWT Configuration
JWT_SECRET_KEY=your_secure_random_jwt_secret_key_here

# Redis Configuration
REDIS_URL=redis://localhost:6379/0

# Celery Configuration
CELERY_BROKER_URL=redis://localhost:6379/1
CELERY_RESULT_BACKEND=redis://localhost:6379/1

# MQTT Configuration
MQTT_BROKER_URL=your.mqtt.broker.url
MQTT_BROKER_PORT=1883
MQTT_USERNAME=your_mqtt_username
MQTT_PASSWORD=your_mqtt_password

# Client Configuration (points to frontend project address)
CLIENT_BASE_URL=http://localhost:3000
```

### 4. Database Initialization

```bash
# Initialize migration environment
flask db init

# Create initial migration script
flask db migrate -m "Initial migration"

# Apply migrations to database
flask db upgrade
```

### 5. Start Development Server

```bash
flask run
# or
python app.py
```

The API service will run at http://localhost:5000.

### 6. Start Celery Worker (Optional)

```bash
celery -A tasks.celery_worker.celery worker --loglevel=info
```

### 7. Deploy Client Project

Please also deploy the https://github.com/loadstarCN/GoodsMart-WMS-Web frontend project for full functionality:

```bash
# Clone and start client in another terminal
git clone https://github.com/loadstarCN/GoodsMart-WMS-Web.git
cd GoodsMart-WMS-Web
npm install
npm run dev
```

The client will run at http://localhost:3000.

## 🗂️ Project Structure

```
GoodsMart-WMS-Backend/
├── app.py                    # Main application entry point
├── config.py                 # Configuration file
├── .env                      # Environment variables file
├── requirements.txt          # Project dependencies
├── migrations/               # Database migration folder
│   ├── versions/             # Migration scripts for each version
│   ├── alembic.ini           # Alembic configuration
│   └── env.py                # Alembic environment configuration
├── extensions/               # Flask extension initialization
│   ├── __init__.py
│   ├── celery.py             # Celery extension
│   ├── db.py                 # SQLAlchemy database extension
│   ├── jwt.py                # JWT extension
│   └── mqtt.py               # MQTT extension
├── system/                   # System core modules
│   ├── __init__.py
│   ├── auth/                 # Authentication & authorization module
│   │   ├── __init__.py
│   │   ├── models.py
│   │   ├── views.py
│   │   ├── schemas.py
│   │   └── utils.py
│   ├── inventory/            # Inventory management module
│   │   ├── __init__.py
│   │   ├── models.py
│   │   ├── views.py
│   │   ├── schemas.py
│   │   └── utils.py
│   ├── order/                # Order management module
│   │   ├── __init__.py
│   │   ├── models.py
│   │   ├── views.py
│   │   ├── schemas.py
│   │   └── utils.py
│   ├── warehouse/            # Warehouse management module
│   │   ├── __init__.py
│   │   ├── models.py
│   │   ├── views.py
│   │   ├── schemas.py
│   │   └── utils.py
│   └── ...                   # Other modules
├── tasks/                    # Celery task module
│   ├── __init__.py
│   ├── celery_worker.py      # Celery Worker
│   ├── inventory_tasks.py    # Inventory-related tasks
│   ├── order_tasks.py        # Order-related tasks
│   └── notification_tasks.py # Notification-related tasks
├── utils/                    # Utility functions
│   ├── __init__.py
│   ├── validators.py         # Data validators
│   ├── helpers.py            # Helper functions
│   └── exceptions.py         # Custom exceptions
├── tests/                    # Test folder
│   ├── __init__.py
│   ├── conftest.py           # pytest configuration
│   ├── test_auth.py          # Authentication tests
│   ├── test_inventory.py     # Inventory tests
│   └── ...                   # Other tests
└── docs/                     # Documentation directory
    ├── api.md                # API documentation
    ├── deployment.md         # Deployment guide
    └── ...                   # Other documentation
```

## 🔧 Development Guide

### Database Management

#### Initialize Migration Environment
```bash
flask db init
```

#### Create Migration Script
```bash
flask db migrate -m "Descriptive migration message"
```

#### Apply Migrations
```bash
flask db upgrade
```

#### Rollback Migration
```bash
flask db downgrade
```

### Dependency Management

#### Update Dependencies with pip-review
```bash
# Check for updatable packages
pip-review --local

# Automatically update all packages
pip-review --local --auto

# Update requirements.txt
pip freeze > requirements.txt
```

#### Install PostgreSQL Development Packages

**Ubuntu/Debian:**
```bash
sudo apt-get install libpq-dev python3-dev
```

**CentOS/RHEL:**
```bash
sudo yum install postgresql-devel python3-devel
```

### Running Tests

```bash
# Install test dependencies
pip install pytest pytest-cov

# Run all tests
pytest tests/

# Run specific test file
pytest tests/test_auth.py

# Generate test coverage report
pytest --cov=system tests/
```

## 📦 API Functional Modules

### Authentication & Authorization Module
- User registration/login
- JWT Token management
- Permission control
- Role management

### Inventory Management Module
- Product information management
- Inventory query and adjustment
- Inventory alerts
- Batch management

### Order Management Module
- Order creation and processing
- Order status tracking
- Shipping management
- Return processing

### Warehouse Management Module
- Warehouse information management
- Location management
- Inventory counting
- Transfer management

## 🔌 MQTT Integration

### Subscribe to Messages
```python
from extensions import mqtt_client

def handle_sensor_data(topic, payload):
    """Process sensor data"""
    print(f"Received from {topic}: {payload}")
    # Processing logic...

# Subscribe to topic
mqtt_client.subscribe('sensors/#', handle_sensor_data)
```

### Publish Messages
```python
from extensions import mqtt_client

def control_device():
    """Control device"""
    success = mqtt_client.publish(
        'devices/light/control',
        {'action': 'on', 'duration': 30}
    )
    return success
```

## 🚀 Deployment

### Complete System Deployment Requirements

To run the complete GoodsMart WMS system, you need to deploy simultaneously:

1. **This API Service** (current project)
2. **https://github.com/loadstarCN/GoodsMart-WMS-Web**
3. **PostgreSQL Database**
4. **Redis Server**
5. **Optional**: MQTT Broker (for IoT device integration)

### Using Gunicorn (Production Environment)

```bash
pip install gunicorn
gunicorn -w 4 -b 0.0.0.0:5000 app:app
```

### Using Supervisord

Create `/etc/supervisor/conf.d/wms-api.conf`:

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

Management commands:
```bash
# Restart service
sudo supervisorctl restart wms-api

# Check status
sudo supervisorctl status

# Reload configuration
sudo supervisorctl reread
sudo supervisorctl update
```

### Client Project Deployment

Please refer to the README file of the https://github.com/loadstarCN/GoodsMart-WMS-Web project for client deployment.

### Using Docker (Optional)

Dockerfile and docker-compose.yml are provided for containerized deployment of the complete system.

## Error Code Classification System

| Category | Error Code Range | HTTP Status Code | Description |
|----------|------------------|------------------|-------------|
| **General Errors** | 10000-10999 | 400 | General business logic errors |
| **Authentication Errors** | 11000-11999 | 401 | Identity authentication issues |
| **Permission Errors** | 12000-12999 | 403 | Insufficient access permissions |
| **Resource Errors** | 13000-13999 | 404 | Resource does not exist |
| **Data Validation Errors** | 14000-14999 | 400 | Data format or content errors |
| **Inventory Errors** | 15000-15999 | 400/403 | Inventory-related business errors |
| **State Transition Errors** | 16000-16999 | 400 | State transition related errors |

## Detailed Error Code Allocation

### 1. General Errors (10000-10999)
| Error Code | Error Message | Description |
|------------|---------------|-------------|
| 10001 | Invalid file type. Only PNG, JPG, JPEG, and GIF are allowed. | Invalid file type |
| 10002 | OSS upload failed: {e} | OSS upload failed |
| 10003 | Old password is incorrect | Old password incorrect |
| 10004 | Only CSV files are supported. | Only CSV files supported |
| 10005 | Missing required columns: {', '.join(missing_columns)} | Missing required columns |
| 10006 | Row {row_num}: Missing values for {', '.join(missing_values)} | Missing values in row data |

### 2. Authentication Errors (11000-11999)
| Error Code | Error Message | Description |
|------------|---------------|-------------|
| 11001 | Invalid username or password | Incorrect username or password |
| 11002 | Token is invalid | Invalid token |
| 11003 | Unauthorized | Unauthorized access |
| 11004 | No current user found | Current user not found |
| 11005 | Current user is not a staff member | Current user is not staff |
| 11006 | User is not active | User not active |
| 11007 | Company is expired | Company expired |

### 3. Permission Errors (12000-12999)
| Error Code | Error Message | Description |
|------------|---------------|-------------|
| 12001 | Access denied: insufficient permissions | Insufficient permissions (general) |
| 12002 | Your IP is blacklisted. | IP blacklisted |
| 12003 | Your IP is not whitelisted. | IP not whitelisted |
| 12004 | API Key not found. | API key not found |
| 12005 | Current user is not a staff member. | User is not staff type |
| 12005 | Unsupported user type. | Unsupported user type |

### 4. Resource Errors (13000-13999)
| Error Code | Error Message | Description |
|------------|---------------|-------------|
| 13001 | Resource not found | Resource not found (general) |
| 13002 | User not found | User not found |
| 13003 | Goods not found in the specified location | Goods not found in specified location |
| 13004 | GoodsLocation not found | Goods location not found |
| 13005 | Warehouse not found | Warehouse not found |

### 5. Data Validation Errors (14000-14999)
| Error Code | Error Message | Description |
|------------|---------------|-------------|
| 14001 | Invalid format  | Invalid format |
| 14002 | Warehouse is not active | Warehouse not active |
| 14003 | User must provide a valid warehouse_id when force_warehouse is True | Valid warehouse ID required |
| 14004 | Missing 'details' in request data | Request data missing 'details' |
| 14005 | No details provided in request data | No details provided in request data |
| 14006 | The from_location_id and to_location_id cannot be the same | Source and target locations cannot be same |
| 14007 | Invalid status value: {status} | Invalid status value |
| 14008 | Invalid required_permissions format. Must be a string or list of permission strings. | Invalid permission format |
| 14009 | Invalid warehouse ID | Invalid warehouse ID |

### 6. Inventory Errors (15000-15999)
| Error Code | Error Message | Description |
|------------|---------------|-------------|
| 15001 | Stock is not enough | Insufficient stock (general) |
| 15002 | Insufficient stock for goods_id={goods_id} in location_id={location_id} | Insufficient stock for specific location goods |
| 15003 | Insufficient stock to lock | Insufficient stock to lock |
| 15004 | Insufficient locked stock to unlock | Insufficient locked stock to unlock |
| 15005 | Not enough ASN stock. | Insufficient ASN stock |
| 15006 | Not enough received stock. | Insufficient received stock |
| 15007 | Not enough sort stock. | Insufficient sort stock |
| 15008 | Not enough DN stock. | Insufficient DN stock |
| 15009 | Not enough pick stock. | Insufficient pick stock |
| 15010 | Not enough packed stock. | Insufficient packed stock |
| 15011 | Not enough delivered stock. | Insufficient delivered stock |
| 15012 | Not enough DN stock to close. | Insufficient DN stock to close |
| 15013 | Low stock threshold must be non-negative or -1 to disable. | Invalid low stock threshold |
| 15014 | High stock threshold must be non-negative or -1 to disable. | Invalid high stock threshold |
| 15015 | High stock threshold must be greater than low stock threshold. | High stock threshold must be greater than low |

### 7. State Transition Errors (16000-16999)
| Error Code | Error Message | Description |
|------------|---------------|-------------|
| 16000 | Invalid state transition: {action} cannot be performed on resource in {current_state} state | Invalid state transition: cannot perform {action} on resource in {current_state} state |
| 16001 | Cannot update a non-pending resource | Cannot update non-pending resource |
| 16002 | Cannot delete a non-pending resource | Cannot delete non-pending resource |
| 16003 | Cannot add details to a non-pending resource | Cannot add details to non-pending resource |
| 16004 | Cannot update details in a non-pending resource | Cannot update details in non-pending resource |
| 16005 | Cannot delete details from a non-pending resource | Cannot delete details from non-pending resource |
| 16006 | Cannot sync details in non-pending resource | Cannot sync details in non-pending resource |
| 16007 | Cannot process a non-pending resource | Cannot process non-pending resource |
| 16008 | Cannot complete a non-in_progress resource | Cannot complete non-in-progress resource |
| 16009 | Cannot create detail in a non-in-progress resource | Cannot create detail in non-in-progress resource |
| 16010 | Cannot update detail in a non-in-progress resource | Cannot update detail in non-in-progress resource |
| 16011 | Cannot delete detail in a non-in-progress resource | Cannot delete detail in non-in-progress resource |
| 16012 | Cannot create batch in a non-in-progress resource | Cannot create batch in non-in-progress resource |
| 16013 | Cannot update batch in a non-in-progress resource | Cannot update batch in non-in-progress resource |
| 16014 | Cannot delete batch in a non-in-progress resource | Cannot delete batch in non-in-progress resource |
| 16015 | 'details' must be a list | Details must be a list |
| 16016 | Resource has no details | Resource has no details |
| 16017 | Detail is already completed | Detail already completed |
| 16018 | Cannot approve a resource that is not pending | Cannot approve non-pending resource |
| 16019 | Cannot complete a resource that is not approved | Cannot complete unapproved resource |
| 16020 | No differences found in resource details | No differences in resource details |
| 16021 | Cannot receive a non-pending resource | Cannot receive non-pending resource |
| 16022 | Cannot close a non-pending resource | Cannot close non-pending resource |
| 16023 | Cannot update a completed or signed resource | Cannot update completed or signed resource |
| 16024 | Cannot sign a non-completed resource | Cannot sign incomplete resource |
| 16025 | Duplicate goods_id: {goods_id} | Duplicate goods ID |
| 16026 | goods_id and warehouse_id are required to create an inventory record. | goods_id and warehouse_id required for inventory record |
| 16027 | Manager does not belong to the same company | Manager not in same company |
| 16028 | Cannot cancel a non-pending resource | Cannot close non-pending resource |

## 🤝 Contribution Guidelines

We welcome contributions of any kind! Please read our contribution guidelines:

1. Fork this project
2. Create a feature branch (`git checkout -b feature/AmazingFeature`)
3. Commit your changes (`git commit -m 'Add some AmazingFeature'`)
4. Push to the branch (`git push origin feature/AmazingFeature`)
5. Open a Pull Request

## 🆘 Support

If you encounter any issues or have questions, please:

1. Check docs/api.md
2. Search https://github.com/loadstarCN/GoodsMart-WMS-Backend/issues
3. Ensure the client project is properly deployed
4. Submit a new Issue

## 🔗 Related Projects

This project is part of the GoodsMart WMS system. Related project links:

https://github.com/loadstarCN/GoodsMart-WMS - Contains documentation and coordination information for the complete system  
https://github.com/loadstarCN/GoodsMart-WMS-Web - Companion frontend system

Developers are advised to also follow the main repository for the latest system updates and complete documentation.

## 🙏 Acknowledgments

Thanks to all developers who have contributed to this project.

---

**Important Notes**: 
- This is an open-source project, ensure no sensitive information is committed to version control
- Use environment variables to manage sensitive configurations
- This project uses AGPLv3 license, **commercial use is prohibited**, contact the author for commercial licensing if needed
- **Official Client Projects**:
  - https://github.com/loadstarCN/GoodsMart-WMS-Web
  - Mobile Client (Coming Soon)
- Developers can build custom clients based on the API