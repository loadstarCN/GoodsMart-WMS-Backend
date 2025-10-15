# Delivery 模块

## 模块描述
Delivery 模块用于管理发货记录，记录每次发货的相关信息，包括收货人、运输信息、状态等。

---

## 数据模型字段说明

### Delivery 表
| 字段名称                 | 类型        | 描述                                   |
|--------------------------|-------------|----------------------------------------|
| `id`                    | Integer     | 主键                                   |
| `dn_id`                 | Integer     | 关联的 Delivery Note（DN） ID           |
| `recipient_id`          | Integer     | 收货人（Recipient） ID                  |
| `shipping_address`      | String      | 发货地址                                |
| `expected_shipping_date` | Date       | 预计发货日期                            |
| `actual_shipping_date`  | Date        | 实际发货日期                            |
| `transportation_mode`   | String      | 运输方式（如快递、货运等）                |
| `carrier_id`            | Integer     | 承运商 ID                              |
| `tracking_number`       | String      | 快递单号                                |
| `shipping_cost`         | Float       | 快递费用                                |
| `order_number`          | String      | 客户订单编号                            |
| `status`                | String      | 状态（pending: 待发货, shipping: 发货中, completed: 已完成） |
| `remark`                 | String      | 备注                                   |
| `created_by`            | Integer     | 创建者 ID                               |
| `created_at`            | DateTime    | 创建时间                                |
| `updated_at`            | DateTime    | 更新时间                                |

---

## API 接口

### 获取所有发货记录
**GET** `/api/delivery/`  
获取分页后的发货记录列表。

### 创建发货记录
**POST** `/api/delivery/`  
创建新的发货记录。

### 获取发货记录详情
**GET** `/api/delivery/<int:delivery_id>`  
根据 `delivery_id` 获取单条发货记录详情。

### 更新发货记录
**PUT** `/api/delivery/<int:delivery_id>`  
根据 `delivery_id` 更新发货记录。

### 删除发货记录
**DELETE** `/api/delivery/<int:delivery_id>`  
根据 `delivery_id` 删除发货记录。

---

## 状态流转
- **`pending`**: 待发货
- **`shipping`**: 发货中
- **`completed`**: 已完成

```markdown
状态流转示例:
1. 创建发货记录，状态为 `pending`。
2. 设置快递单号后，状态更新为 `shipping`。
3. 确认货物送达，状态更新为 `completed`。
