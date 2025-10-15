以下是更新后的 **ASN 模块文档**，已根据最新的模型字段进行了修改，特别是 `status` 字段的说明和其他字段的变更：

---

# ASN 模块文档

## 概述
ASN（Advanced Shipping Notice，高级送货通知）模块用于管理入库运输通知及其明细。它跟踪供应商的预期和实际货物，包括商品信息、数量和物流相关内容。

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

### **ASN**
表示送货通知的总体信息。

| 字段名称               | 类型         | 描述                                       |
|------------------------|--------------|--------------------------------------------|
| `id`                   | `Integer`    | 主键。                                     |
| `supplier_id`          | `Integer`    | 关联供应商的外键。                         |
| `tracking_number`      | `String`     | 快递单号。                                 |
| `carrier_id`           | `Integer`    | 关联承运商的外键。                         |
| `asn_type`             | `String`     | ASN 类型。可选值为：`inbound`, `return_from_customer`, `transfer`。 |
| `status`               | `String`     | ASN 状态。可选值为：`pending`, `received`, `closed`。 |
| `expected_arrival_date`| `Date`       | 预计到达日期。                             |
| `actual_arrival_date`  | `Date`       | 实际到达日期。                             |
| `remark`                | `String`     | ASN 备注信息。                             |
| `is_active`            | `Boolean`    | 是否有效，默认为 `True`。                  |
| `created_by`           | `Integer`    | 创建者的用户 ID。                          |
| `created_at`           | `DateTime`   | 创建时间戳。                               |
| `updated_at`           | `DateTime`   | 最后更新时间戳。                           |

---

### **ASNDetail**
表示每种商品的具体信息。

| 字段名称               | 类型         | 描述                                       |
|------------------------|--------------|--------------------------------------------|
| `id`                   | `Integer`    | 主键。                                     |
| `asn_id`               | `Integer`    | 关联 ASN 的外键。                         |
| `goods_id`             | `Integer`    | 关联商品的外键。                          |
| `quantity`            | `Integer`    | 预期商品数量。                             |
| `actual_quantity`     | `Integer`    | 实际到货商品数量。                         |
| `sorted_quantity`     | `Integer`    | 已分拣商品数量。                           |
| `damage_quantity`     | `Integer`    | 损坏商品数量。                             |
| `weight`         | `Float`      | 商品重量（公斤）。                         |
| `volume`         | `Float`      | 商品体积（立方米）。                       |
| `remark`                | `String`     | 明细备注信息。                             |
| `created_by`           | `Integer`    | 创建者的用户 ID。                          |
| `create_time`          | `DateTime`   | 创建时间戳。                               |
| `update_time`          | `DateTime`   | 最后更新时间戳。                           |

---

## 字段说明

### **ASN 字段**
- **`supplier_id`**：将 ASN 与供应商关联。
- **`tracking_number`**：（可选）允许通过承运商跟踪运输。
- **`carrier_id`**：将 ASN 与承运商关联。
- **`asn_type`**：ASN 类型，标识入库、退货或调拨。可能的值：
  - `inbound`：入库 ASN
  - `return_from_customer`：从客户处退回 ASN
  - `transfer`：调拨 ASN
- **`status`**：跟踪 ASN 的进度。可能的值：
  - `pending`：ASN 已创建但尚未接收货物。
  - `received`：货物已到达并正在处理。
  - `closed`：货物处理已完成，所有商品数量已确认并处理完毕。
- **`expected_arrival_date`**：货物预计到达的日期。
- **`actual_arrival_date`**：货物实际到达的日期，到货后更新。
- **`remark`**：关于 ASN 的自由文本信息。
- **`is_active`**：指示 ASN 是否处于活动状态，默认值为 `True`。

### **ASNDetail 字段**
- **`quantity`**：预期商品总数量。
- **`actual_quantity`**：实际接收到的商品数量。
- **`sorted_quantity`**：已分拣商品数量。
- **`damage_quantity`**：运输过程中损坏的商品数量。
- **`weight`**：商品的总重量（单位：公斤）。
- **`volume`**：商品的总体积（单位：立方米）。
- **`remark`**：特定商品的附加信息。

---

## 状态生命周期

### **ASN 状态转换**

1. **`pending`**（初始状态）：
   - ASN 已创建，但货物尚未接收。
   - 状态可以通过接收货物转换为 `received`。
   
2. **`received`**：
   - 货物已到达仓库，并开始处理。
   - 更新 `actual_arrival_date`。
   - 可以进一步转换为 `closed`。
   
3. **`closed`**：
   - 货物处理（包括分拣）已完成。
   - 所有商品的 `actual_quantity` 必须等于 `quantity`。
   - 完成后，ASN 被标记为 `closed`，并且无法再更改。

---

## 更新机制

### **ASN 更新**
- 字段如 `tracking_number`、`carrier_id` 和 `remark` 可以在 ASN 状态为 `closed` 前更新。
- 收货后需要更新 `actual_arrival_date`。
- 状态只能向前转换（`pending` → `received` → `closed`），不可回退。

### **ASNDetail 更新**
- 收货时更新 `actual_quantity`。
- 分拣时更新 `sorted_quantity` 和 `damage_quantity`。
- 如果父 ASN 状态为 `closed`，则限制更新。

---

## API 接口

1. **ASN 列表**
   - **GET /asn**：获取所有 ASN。
   - **POST /asn**：创建新 ASN。

2. **ASN 详情**
   - **GET /asn/<asn_id>**：获取特定 ASN 的详细信息。
   - **PUT /asn/<asn_id>**：更新特定 ASN。
   - **DELETE /asn/<asn_id>**：删除特定 ASN。

3. **ASNDetail 管理**
   - **GET /asn/details/<asn_detail_id>**：获取特定 ASN 明细。
   - **PUT /asn/details/<asn_detail_id>**：更新特定 ASN 明细。

---

## 示例用例

1. **创建 ASN**
   - 使用 **POST /asn** 接口创建新 ASN，并包含必要的供应商和商品信息。

2. **接收货物**
   - 更新 ASN 状态为 `received`，填写 `actual_arrival_date` 和 ASN 明细中的 `actual_quantity`。

3. **分拣商品**
   - 更新 ASN 明细中的 `sorted_quantity` 和 `damage_quantity` 以反映分拣进度。

4. **关闭 ASN**
   - 在所有商品完全分拣并处理后，将 ASN 标记为 `closed`。
