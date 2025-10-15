根据你提供的最新 `PickingTask` 和 `PickingTaskDetail` 模型，我已经更新了文档内容。以下是更新后的 `Picking` 模块文档：

---

### Picking 模块

#### 模块描述
Picking 模块用于管理仓库中的拣货任务及其详细记录，通过该模块实现拣货任务的创建、更新、查询和删除操作。Picking 模块是发货阶段的重要组成部分，与出库单（DN 模块）紧密结合，用于追踪每个出库任务的拣货进度。

---

#### 数据表结构

##### **PickingTask 表**

| 字段名称         | 数据类型     | 描述                                      |
|------------------|--------------|-------------------------------------------|
| `id`             | `Integer`    | 主键                                      |
| `dn_id`          | `Integer`    | 外键，关联到出库单（DN）                   |
| `status`         | `Enum`       | 当前任务状态（`pending`、`in_progress`、`completed`） |
| `is_active`      | `Boolean`    | 是否激活，默认为 `True`                    |
| `created_by`     | `Integer`    | 外键，关联到任务的创建者（User）          |
| `created_at`     | `DateTime`   | 创建时间                                   |
| `updated_at`     | `DateTime`   | 最后更新时间                               |

##### **PickingTaskDetail 表**

| 字段名称         | 数据类型     | 描述                                      |
|------------------|--------------|-------------------------------------------|
| `id`             | `Integer`    | 主键                                      |
| `picking_task_id`| `Integer`    | 外键，关联到拣货任务                       |
| `location_id`    | `Integer`    | 外键，关联到库位（Location）               |
| `goods_id`       | `Integer`    | 外键，关联到商品（Goods）                  |
| `picked_quantity`| `Integer`    | 已拣货的数量                               |
| `picking_time`   | `DateTime`   | 拣货时间                                   |
| `operator_id`    | `Integer`    | 外键，关联到任务操作员（User）             |

---

#### API 接口说明

##### **Picking Task 接口**

| HTTP 方法 | 路由           | 功能                       | 权限              |
|-----------|----------------|----------------------------|-------------------|
| `GET`     | `/picking/`    | 获取所有拣货任务             | 需要登录           |
| `POST`    | `/picking/`    | 创建新的拣货任务             | `admin` 或 `settings` |
| `GET`     | `/picking/<id>`| 获取单个拣货任务详情          | `admin` 或 `settings` |
| `PUT`     | `/picking/<id>`| 更新拣货任务信息             | `admin` 或 `settings` |
| `DELETE`  | `/picking/<id>`| 删除拣货任务                 | `admin` 或 `settings` |

##### **Picking Task Detail 接口**

| HTTP 方法 | 路由                           | 功能                       | 权限              |
|-----------|--------------------------------|----------------------------|-------------------|
| `GET`     | `/picking/details`            | 获取所有拣货任务明细         | 需要登录           |
| `POST`    | `/picking/details`            | 创建新的拣货任务明细         | `admin` 或 `settings` |
| `GET`     | `/picking/details/<id>`       | 获取单个拣货任务明细详情      | `admin` 或 `settings` |
| `PUT`     | `/picking/details/<id>`       | 更新拣货任务明细信息         | `admin` 或 `settings` |
| `DELETE`  | `/picking/details/<id>`       | 删除拣货任务明细             | `admin` 或 `settings` |

---

#### 状态字段说明

- **`status` (拣货任务状态)**：
  - `pending`：任务已创建，等待执行。
  - `in_progress`：任务正在进行中。
  - `completed`：任务已完成。

---

#### 数据流转示例

1. **创建拣货任务**：
   - 基于出库单创建拣货任务。
   - 状态默认为 `pending`。

2. **执行拣货任务**：
   - 操作员在指定库位进行拣货，状态更新为 `in_progress`。

3. **任务完成**：
   - 拣货完成后，状态更新为 `completed`。

---

#### 注意事项

1. **关联模块**：
   - Picking 模块依赖于 **DN（出库单）**、**Goods（商品）**、**Location（库位）** 模块。
   - 每个拣货任务应唯一关联一个出库单。

2. **任务拆分**：
   - 如果出库单包含多个商品，每个商品可能会被分配到多个库位拣货。
   - `PickingTaskDetail` 表记录每个商品在不同库位的拣货信息。

3. **损坏处理**：
   - 如果拣货过程中发生损坏，原文档中的 `damage_quantity` 字段已经被移除。因此，这部分的处理将需要改为通过业务流程其他模块进行库存调整和损坏记录。

---

#### 状态流转图

```plaintext
创建拣货任务 → 待执行 (pending)
       ↓
开始拣货 → 进行中 (in_progress)
       ↓
完成拣货 → 已完成 (completed)
```
