根据您提供的代码和需求，已更新以下 `README` 文档。特别注意了 `status` 字段的更新，以及其对应的状态生命周期和字段说明部分。

---

# Sorting 模块文档

## 概述
Sorting（分拣）模块负责管理货物分拣任务及其明细。分拣是仓库管理中的关键环节，通过此模块可以追踪任务状态、分拣商品及数量等详细信息。

---

## 目录
1. [数据模型](#数据模型)
2. [字段说明](#字段说明)
3. [状态生命周期](#状态生命周期)
4. [更新机制](#更新机制)
5. [API 接口](#api-接口)
6. [示例用例](#示例用例)

---

## 数据模型

### **SortingTask**
表示分拣任务的总体信息。

| 字段名称          | 类型         | 描述                                       |
|-------------------|--------------|------------------------------------------|
| `id`             | `Integer`    | 主键。                                     |
| `asn_id`         | `Integer`    | 关联 ASN 的外键。                         |
| `status`         | `String`     | 分拣任务状态（pending, in_progress, completed）。|
| `created_by`     | `Integer`    | 创建者的用户 ID。                          |
| `created_at`     | `DateTime`   | 创建时间戳。                               |
| `updated_at`     | `DateTime`   | 最后更新时间戳。                           |

### **SortingTaskDetail**
表示分拣任务中的具体商品。

| 字段名称          | 类型         | 描述                                       |
|-------------------|--------------|------------------------------------------|
| `id`             | `Integer`    | 主键。                                     |
| `sorting_task_id`| `Integer`    | 关联 SortingTask 的外键。                 |
| `goods_id`  | `Integer`    | 关联 Goods 的外键。                   |
| `sorted_quantity`| `Integer`    | 本次分拣的商品数量。                       |
| `damage_quantity`| `Integer`    | 本次分拣的损坏商品数量。                   |
| `sorting_time`   | `DateTime`   | 分拣时间。                                 |
| `operator_id`    | `Integer`    | 分拣操作员的用户 ID。                      |

---

## 字段说明

### **SortingTask 字段**
- **`asn_id`**：将分拣任务与对应的 ASN 关联。
- **`status`**：分拣任务的状态。
  - `pending`：任务已创建，但尚未开始。
  - `in_progress`：任务正在进行中，可能部分商品已完成分拣。
  - `completed`：任务已完成，所有商品分拣和处理完毕。
- **`created_by`**：创建任务的用户。
- **`created_at`**：任务的创建时间。
- **`updated_at`**：任务的最后更新时间。

### **SortingTaskDetail 字段**
- **`sorting_task_id`**：将任务明细与对应的分拣任务关联。
- **`goods_id`**：将任务明细与Goods关联。
- **`sorted_quantity`**：当前分拣任务中已分拣的商品数量。
- **`damage_quantity`**：当前任务中损坏的商品数量。
- **`sorting_time`**：完成分拣的时间。
- **`operator_id`**：执行分拣操作的用户。

---

## 状态生命周期

### **SortingTask 状态转换**

1. **`pending`**（初始状态）：
   - 任务已创建，但尚未开始。
2. **`in_progress`**：
   - 任务正在进行中，可能部分商品已完成分拣。
3. **`completed`**：
   - 任务已完成，所有商品分拣和处理完毕。

---

## 更新机制

### **SortingTask 更新**
- **任务状态更新**：
  - 当任务开始时，将 `status` 更新为 `in_progress`。
  - 当所有明细记录完成后，将 `status` 更新为 `completed`。

### **SortingTaskDetail 更新**
- **分拣数量更新**：
  - 在分拣过程中，更新 `sorted_quantity` 和 `damage_quantity`。
  - 更新 `sorting_time` 以记录操作时间。

---

## API 接口

1. **SortingTask 列表**
   - **GET /sorting/tasks**：获取所有分拣任务。
   - **POST /sorting/tasks**：创建新分拣任务。

2. **SortingTask 详情**
   - **GET /sorting/tasks/<task_id>**：获取特定分拣任务的详细信息。
   - **PUT /sorting/tasks/<task_id>**：更新特定分拣任务。
   - **DELETE /sorting/tasks/<task_id>**：删除特定分拣任务。

3. **SortingTaskDetail 管理**
   - **GET /sorting/task-details/<detail_id>**：获取特定任务明细。
   - **PUT /sorting/task-details/<detail_id>**：更新特定任务明细。

---

## 示例用例

1. **创建分拣任务**
   - 使用 **POST /sorting/tasks** 接口创建新任务，并指定关联的 ASN。

2. **执行分拣操作**
   - 更新 `SortingTaskDetail` 的 `sorted_quantity` 和 `damage_quantity` 字段，记录分拣进度。

3. **完成分拣任务**
   - 当所有任务明细完成后，更新 `SortingTask` 的状态为 `completed`。

4. **查询任务和明细**
   - 使用 **GET /sorting/tasks** 和 **GET /sorting/task-details** 接口查询任务和商品的分拣记录。

---