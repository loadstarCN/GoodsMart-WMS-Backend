以下是根据您提供的代码更新后的 README 文档：

---

### DN 模块

#### 模块描述  
DN 模块用于管理出库单及其相关的明细记录，通过该模块实现出库任务的创建、更新、查询和删除操作。DN 模块作为仓库管理的重要部分，与拣货任务、打包任务紧密关联，用于记录订单的发货过程。

---

#### 数据表结构

##### **DN 表**

| 字段名称               | 类型          | 说明                                 |
|------------------------|---------------|--------------------------------------|
| id                     | Integer       | 主键 ID                              |
| recipient_id           | Integer       | 收货人 ID                            |
| shipping_address       | String(255)   | 发货地址                             |
| expected_shipping_date | Date          | 预计发货日期                         |
| carrier_id             | Integer       | 承运商 ID                            |
| order_number           | String(50)    | 客户订单编号                         |
| packaging_info         | String(255)   | 包装信息                             |
| special_handling       | String(255)   | 特殊处理要求                         |
| dn_type                | String(20)    | 出库单类型（shipping, return_to_supplier, transfer） |
| status                 | String(20)    | 出库单状态（pending, picked, packed, delivered, completed） |
| remark                  | String(255)   | 备注                                 |
| is_active              | Boolean       | 是否有效                             |
| created_by             | Integer       | 创建者 ID                            |
| created_at             | DateTime      | 创建时间                             |
| updated_at             | DateTime      | 更新时间                             |

##### **DN Detail 表**

| 字段名称              | 类型         | 说明                                  |
|-----------------------|--------------|---------------------------------------|
| id                    | Integer      | 主键 ID                               |
| dn_id                 | Integer      | 关联主表 DN 的 ID                     |
| goods_id              | Integer      | 商品 ID                               |
| quantity              | Integer      | 计划出库数量                          |
| picked_quantity       | Integer      | 已拣选数量                            |
| packed_quantity       | Integer      | 已打包数量                            |
| delivered_quantity   | Integer      | 已发货数量                            |
| remark                 | String(255)  | 明细备注                              |
| created_by            | Integer      | 创建者 ID                            |
| create_time           | DateTime     | 创建时间                             |
| update_time           | DateTime     | 更新时间                             |

---

#### API 接口说明

##### **DN 接口**

| HTTP 方法 | 路由           | 功能                       | 权限              |
|-----------|----------------|----------------------------|-------------------|
| `GET`     | `/dn/`          | 获取所有出库单              | 需要登录           |
| `POST`    | `/dn/`          | 创建新的出库单              | `admin` 或 `settings` |
| `GET`     | `/dn/<id>`      | 获取单个出库单详情           | `admin` 或 `settings` |
| `PUT`     | `/dn/<id>`      | 更新出库单信息              | `admin` 或 `settings` |
| `DELETE`  | `/dn/<id>`      | 删除出库单                  | `admin` 或 `settings` |

##### **DN Detail 接口**

| HTTP 方法 | 路由                  | 功能                       | 权限              |
|-----------|-----------------------|----------------------------|-------------------|
| `GET`     | `/dn/details`         | 获取所有出库单明细          | 需要登录           |
| `POST`    | `/dn/details`         | 创建新的出库单明细          | `admin` 或 `settings` |
| `GET`     | `/dn/details/<id>`    | 获取单个明细详情            | `admin` 或 `settings` |
| `PUT`     | `/dn/details/<id>`    | 更新出库单明细信息          | `admin` 或 `settings` |
| `DELETE`  | `/dn/details/<id>`    | 删除出库单明细              | `admin` 或 `settings` |

---

#### 状态字段说明

- **`status` (出库单状态)**：
  - `pending`：出库单已创建，等待拣货任务。
  - `picked`：拣货任务已完成，等待打包。
  - `packed`：打包完成，等待发货。
  - `delivered`：已发货，等待客户签收。
  - `completed`：订单已完成。

---

#### 数据流转示例

1. **创建出库单**：
   - 接收客户订单后，创建对应的出库单。
   - 状态默认为 `pending`。

2. **关联拣货任务**：
   - 为出库单分配拣货任务，状态更新为 `picking`。

3. **完成拣货任务**：
   - 拣货完成后，状态更新为 `picked`。

4. **完成打包任务**：
   - 打包完成后，状态更新为 `packed`。

5. **发货**：
   - 订单已发货，状态更新为 `delivered`。

6. **订单完成**：
   - 客户签收货物后，状态更新为 `completed`。

---

#### 注意事项

1. **关联模块**：
   - 出库单依赖于 **Recipient（收货方）**、**Goods（商品）**、**Carrier（承运商）** 模块。
   - 每个出库单应唯一关联一个收货方。

2. **任务追踪**：
   - 出库单与拣货任务、打包任务紧密关联。

3. **状态更新规则**：
   - 确保状态流转符合实际业务逻辑，不允许直接跳过状态。

---

#### 扩展功能建议

- **自动任务分配**：
  - 系统可以根据出库单自动创建拣货任务和打包任务。
- **发货追踪**：
  - 为每个出库单集成物流追踪功能。
- **数据统计**：
  - 分析出库效率和损坏率，优化出库流程。

---

#### 状态流转图

```plaintext
创建出库单 → 待拣货 (pending)
       ↓
分配拣货任务 → 拣货完成 (picked)
       ↓
完成拣货任务 → 已打包 (packed)
       ↓
发货 → 已发货 (delivered)
       ↓
客户签收 → 订单完成 (completed)
```

---

如果有进一步的修改需求，欢迎告诉我！