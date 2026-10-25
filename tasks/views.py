from flask_restx import Resource, Namespace
from extensions import authorizations
from .snapshot import run_inventory_metrics, run_inventory_snapshot
from system.common import permission_required

api_ns = Namespace('task', description='Task management APIs', authorizations=authorizations)


@api_ns.doc(security="jsonWebToken")
@api_ns.route('/inventory_snapshot')
class InventorySnapshotTask(Resource):
    @permission_required(["all_access", "tasks_execute"])
    def post(self):
        """触发库存快照任务（同步执行）"""
        result = run_inventory_snapshot()
        return {"message": result}, 200


@api_ns.doc(security="jsonWebToken")
@api_ns.route('/inventory_metrics')
class InventoryMetricsTask(Resource):
    @permission_required(["all_access", "tasks_execute"])
    def post(self):
        """重算库存分析（库龄、近 N 天出库、周转、ABC、呆滞；先补生成缺的日终结存）"""
        return {"message": run_inventory_metrics()}, 200
