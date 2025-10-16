## Usage Instructions
[中文 README](./README-zh.md)

### Asynchronous Task Execution
Example asynchronous task code can be found in the example.py file. Task functions should be decorated with the `@shared_task` decorator.

The system will automatically scan and import all functions decorated with the `@task` decorator in the tasks directory. For specific path configuration, please refer to the `/config.py` file.

Task management interfaces are defined in the views.py file.

The celery_worker.py file contains a standalone Celery example script.

## Celery Worker Startup Instructions
### Startup Methods
#### Using Flask Context (Recommended)
Execute the following command in the project root directory to start the Celery Worker with Flask context:

#### Linux Systems
```
celery -A app.celery worker --loglevel=info
```

#### Windows Systems
Since Windows systems do not natively support Gevent or Eventlet, you need to install Eventlet:
```
pip install eventlet
celery -A app.celery worker --loglevel=info -P eventlet
```

### Standalone Startup (Without Flask Context)
When Flask context is not required, you can run the Celery Worker independently. Please refer to the example code in the celery_worker.py file.

#### Linux Systems
```
celery -A tasks.celery_worker.celery worker --loglevel=info
```

#### Windows Systems
Similarly, Windows systems require Eventlet installation:
```
pip install eventlet
celery -A tasks.celery_worker.celery worker --loglevel=info -P eventlet
```

### Flower Monitoring
Startup command:
```
celery --broker=redis://username:password@host:port/database flower --basic-auth=username:password --port=5555
```