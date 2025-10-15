以下是基于新规则的更新版文档（关键修改部分用 **加粗** 标出）：

---

# 统一权限验证系统 - 使用说明（更新版）

## 系统功能概述
支持用户角色和第三方系统权限的统一验证。  
**权限验证基于扁平化权限字符串（如 "all_access"），用户通过角色继承权限，第三方系统通过 API Key 直接配置权限列表。**

---

## 1. 权限格式定义

### 用户权限规则
- **权限为字符串标识**，格式分为两类：
  - **全局权限**：`all_access`（全系统权限）、`company_all_access`（企业级全权限）
  - **操作级权限**：`<资源>_<动作>`，例如：
    - `asn_edit`（入库单编辑）
    - `inventory_read`（库存读取）
    - `user_delete`（用户删除）

### 第三方系统权限
**API Key 直接存储权限字符串列表**（非模块化结构），例如：
```json
["all_access", "asn_edit", "inventory_read"]
```

---

## 2. 用户角色和权限

### 角色定义
**角色与权限直接绑定**（不再关联模块），预定义角色示例：
```python
# 系统角色
admin = Role(name="admin", permissions=["all_access"])
api = Role(name="api", permissions=["api_access"])

# 企业级角色
company_admin = Role(name="company_admin", permissions=["company_all_access"])
warehouse_admin = Role(name="warehouse_admin", permissions=["asn_edit", "inventory_manage"])

# 操作级角色
warehouse_operator = Role(name="warehouse_operator", permissions=["asn_view", "inventory_read"])
```

### 用户分配角色
用户通过角色继承权限（支持多角色叠加）：
```python
user = User(email="operator@example.com")
user.roles.append(warehouse_admin)
user.roles.append(warehouse_operator)
```

---

## 3. 第三方系统权限

### API Key 权限管理
**直接配置权限字符串列表**：
```python
api_key = APIKey(
    key="example_key",
    system_name="WMS Integration",
    permissions=["asn_edit", "inventory_read"]  # 简化的权限列表
)
```

### 权限验证方法
**检查权限字符串是否存在**：
```python
class APIKey(db.Model):
    # ...
    def has_permission(self, permission):
        """直接验证权限字符串"""
        return permission in self.permissions
```

---

## 4. 接口保护

### 装饰器用法
**直接传递所需权限字符串列表**：
```python
@permission_required(["all_access", "company_all_access", "asn_edit"])
def manage_asn():
    # 需要拥有任意一个列出的权限
    pass
```

### 接口保护示例
```python
# 入库单管理接口
@api_ns.route('/asn')
class ASNResource(Resource):

    @permission_required(["asn_edit", "company_all_access"])
    def post(self):
        """创建/编辑入库单"""
        return {"message": "操作成功"}, 200

    @permission_required(["asn_view", "inventory_read"])
    def get(self):
        """查看入库单"""
        return {"data": [...]}, 200

# 全局管理接口
@api_ns.route('/system/config')
class SystemConfig(Resource):

    @permission_required(["all_access"])
    def get(self):
        """获取系统配置（仅超管）"""
        return {"config": [...]}, 200
```

---

## 5. 测试权限系统

### 用户权限测试
```python
user = User.query.filter_by(email="admin@example.com").first()
assert user.has_permission("all_access")  # 通过角色继承

# 检查操作权限
if user.has_permission("asn_edit"):
    print("用户有入库单编辑权限")
```

### API Key 测试
```python
api_key = APIKey.query.filter_by(key="demo_key").first()

# 验证全局权限
if api_key.has_permission("all_access"):
    print("API Key 拥有全系统权限")

# 验证具体操作权限
if api_key.has_permission("inventory_read"):
    print("API Key 可读取库存")
```

---

## 6. 常见问题（更新）

### Q1: 如何新增权限类型？
**直接在角色或 API Key 的权限列表中添加新字符串**，例如新增 `report_export` 权限：
```python
finance_role = Role(name="finance", permissions=["report_view", "report_export"])
```

### Q2: 如何实现权限继承？
通过角色层级实现（需自定义逻辑）：
```python
# 示例：公司管理员自动继承仓库管理员权限
company_admin.permissions += warehouse_admin.permissions
```

### Q3: 为什么 API Key 权限验证失败？
检查权限字符串是否 **完全匹配**（大小写敏感），建议统一使用小写+下划线格式。

---

## 总结

### 核心变化
| 项目         | 旧规则                     | 新规则                     |
|--------------|---------------------------|---------------------------|
| **权限结构**  | 模块化（module+action）    | 扁平化权限字符串           |
| **角色定义**  | 关联模块                  | 直接绑定权限列表          |
| **API Key**  | JSON 嵌套权限             | 简化的权限字符串列表       |

### 优势
1. **简化配置**：移除了模块化层级，权限管理更直观
2. **灵活扩展**：新增权限只需添加字符串，无需修改数据结构
3. **统一验证**：用户角色和 API Key 使用相同校验逻辑

更新后的系统将更易维护，建议结合数据库迁移工具更新现有权限数据。