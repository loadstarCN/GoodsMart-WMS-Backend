"""发货核心（与承运商、国内外无关），设计见 doc/shipping-flow.md。

- common：DN 加锁、当前发货任务、发货后锁定、业务日期时区
- route：国内 / 跨境（发货国 → 收件国、是否要报关）
- waybills：运单记录（与来源无关）、手填运单号的 manual 记录
- stage：列表用的发货阶段（SQL 判定）
- flow：出库单流程汇总（GET /warehouse/dn/<id>/shipping-flow）
- auto_sign：自动视为签收（carriers.auto_sign_days）
- options：运单服务选项（DN.shipping_options，按承运商声明的 service_options 校验，建运单 / 导出后锁定）
"""
