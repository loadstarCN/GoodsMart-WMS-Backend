# Payment 模块

## 模块概述
`Payment` 模块用于管理与发货相关的费用记录，包括快递费、物流费等。此模块支持记录支付信息、支付方式以及关联发货和承运商的详细数据。

## 数据表设计
### Payment
| 字段名称              | 类型          | 说明                                   |
|-----------------------|---------------|----------------------------------------|
| `id`                 | `Integer`     | 支付记录的唯一标识。                     |
| `delivery_id`        | `Integer`     | 关联发货记录的 ID。                     |
| `amount`             | `Float`       | 支付金额。                               |
| `payment_method`     | `String`      | 支付方式（如银行转账、支付宝等）。         |
| `carrier_id`         | `Integer`     | 承运商的 ID。                           |
| `status`             | `String`      | 支付状态（pending: 未支付，paid: 已支付，canceled: 已取消）。 |
| `payment_time`       | `DateTime`    | 支付完成的时间。                         |
| `remark`              | `String`      | 支付记录的备注信息。                     |
| `created_by`         | `Integer`     | 创建支付记录的用户 ID。                  |
| `created_at`         | `DateTime`    | 创建时间。                               |
| `updated_at`         | `DateTime`    | 更新时间。                               |

---

## API 路由

### **获取所有支付记录**
**URL:** `/payment/`  
**方法:** `GET`  
**权限:** 需要 JWT 身份验证。  

### **创建支付记录**
**URL:** `/payment/`  
**方法:** `POST`  
**权限:** 需要 `admin` 和 `finance` 权限。  

### **获取支付记录详情**
**URL:** `/payment/<payment_id>`  
**方法:** `GET`  
**权限:** 需要 JWT 身份验证。

### **更新支付记录**
**URL:** `/payment/<payment_id>`  
**方法:** `PUT`  
**权限:** 需要 `admin` 和 `finance` 权限。  

### **删除支付记录**
**URL:** `/payment/<payment_id>`  
**方法:** `DELETE`  
**权限:** 需要 `admin` 和 `finance` 权限。  

---

## 模块特点
- **关联发货记录**：每条支付记录关联一条发货记录 (`Delivery`)。
- **支持多种支付方式**：灵活记录银行转账、线上支付等多种支付方式。
- **权限管理**：仅管理员和财务角色可创建、编辑或删除支付记录。
