# Cycle Count 模块

## 概述
Cycle Count 是一种分阶段的库存盘点方法，通过周期性检查部分库存，确保数据准确性。本模块支持创建盘点任务、记录盘点结果并跟踪操作人员和任务创建者。

---

## 数据模型

### `CycleCountTask`
- **字段说明**:
  - `id`：任务 ID。
  - `task_name`：任务名称。
  - `scheduled_date`：计划盘点日期。
  - `status`：任务状态（Scheduled, In Progress, Completed）。
  - `created_by`：创建者 ID。
  - `created_at`：创建时间。
  - `updated_at`：更新时间。

### `CycleCountResult`
- **字段说明**:
  - `id`：盘点结果 ID。
  - `task_id`：关联的任务 ID。
  - `goods_id`：关联的商品 ID。
  - `location_id`：关联的货位 ID。
  - `actual_quantity`：实际盘点数量。
  - `system_quantity`：系统记录数量。
  - `adjustment_quantity`：调整的差异数量。
  - `status`：盘点状态（Pending, Completed）。
  - `operator_id`：操作人 ID。
  - `updated_at`：结果更新时间。

---

## API 接口
### 1. 创建盘点任务
**POST** `/api/cyclecount/tasks`
```json
{
  "task_name": "January Cycle Count",
  "scheduled_date": "2025-01-15T00:00:00",
  "created_by": 1
}
