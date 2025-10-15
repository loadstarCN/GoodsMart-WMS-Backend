from flask_restx import Resource, Namespace
from extensions import authorizations
from .example import example_task
from .snapshot import inventory_snapshot_task
from system.common import permission_required  

api_ns = Namespace('task', description='Task management APIs', authorizations=authorizations)

@api_ns.doc(security="jsonWebToken")
@api_ns.route('/inventory_snapshot')
class InventorySnapshotTask(Resource):
    @permission_required(["all_access","tasks_execute"])  # 添加权限保护
    def post(self):
        """触发异步任务"""
        task = inventory_snapshot_task.apply_async()
        return {"message": "Task started", "task_id": task.id}, 202

@api_ns.doc(security="jsonWebToken")
@api_ns.route('/example')
class ExampleTask(Resource):
    @permission_required(["all_access","tasks_execute"])  # 添加权限保护
    def post(self):
        """触发异步任务"""
        task = example_task.apply_async(args=[30])  # 调用任务，耗时 10 秒
        return {"message": "Task started", "task_id": task.id}, 202

@api_ns.doc(security="jsonWebToken")
@api_ns.route('/status/<task_id>')
class TaskStatus(Resource):
    @permission_required(["all_access","tasks_view"])  # 添加权限保护
    def get(self, task_id):
        """获取任务状态"""
        result = example_task.AsyncResult(task_id)
        if result.state == 'PENDING':
            response = {"state": result.state, "status": "Task is pending..."}
        elif result.state == 'SUCCESS':
            response = {"state": result.state, "result": result.result}
        else:
            response = {"state": result.state, "status": str(result.info)}
        return response
