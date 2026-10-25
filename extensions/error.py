from flask_restx import ValidationError
from sqlalchemy.exc import DataError, IntegrityError
from werkzeug.exceptions import HTTPException

# 业务码登记（出口单证 / 发货，完整列表见 README「Business codes」）：
# 16063  400  报关快照结构不合法：details {field}；行的 unit_value 小数位超过币种小数位（JPY 等 0 位，其它 2 位）
#             时另带 {currency, max_decimal_places}
# 16069  409  （已不再使用：完成发货不查单证）原：海外件发货前单证缺失 / 过期，details {missing_documents, outdated}
# 16076  409  有未结束的自动运单（pending / active / unknown / cancelling）时改箱子 / 改报关快照
#             （reason CARRIER_SHIPMENT_OPEN），或签发与当前单证（随运单提交的）内容不一致的单证（reason DOCUMENT_DATA_CHANGED）：
#             details {reason, status, carrier, carrier_shipment: {id, status, tracking_number}}
# 16078  409  有有效自动运单时运单号 / 承运商与运单不一致：details {tracking_number, carrier（插件代码）,
#             shipment_carrier_id（建这张运单的承运商）[, carrier_id（请求里的承运商）]}；
#             用了本 DN 已取消的自动运单号：details {tracking_number: null, cancelled_tracking_number, carrier,
#             shipment_carrier_id, status: 'cancelled', record_status: 'cancelled' | 'dismissed'}（已确认作废的运单号同样拦）
# 16079  409  自动运单进行中 / 结果不明 / 正在取消或取消结果不明（pending / unknown / cancelling）：不能建单、存运单号、
#             完成发货（不带运单号也拦），也不能再点取消（取消进行中）：details {unresolved}
# 16080  409  （已不再使用：单证不印运单号）完成发货后的运单号与当前 CI 上印的 AWB 不一致
# 16088  409  任务已停用（所属 DN / ASN 已取消），不能再操作：details {task_id}
# 16089  409  拣货任务一件都没拣，不能完成（拣不到货请取消 DN）
# 16090  409  DN 已有未完成（pending / in_progress）的有效发货任务（手工新建接口已去掉，目前不会出现）：
#             details {delivery_task_id, status}
# 16091  409  有报关快照或有运单记录（任一来源）的 DN 只能对当前发货任务完成发货 / 存运单号 / 修改：
#             details {delivery_task_id, current_status}
# 16092  409  （已不再使用：单证不印承运商）完成发货的承运商与当前 CI 上印的不一致
# 16093  409  取消运单 / 确认作废请求里带的目标与当前记录不符（页面上的记录已变，先重新读取）：
#             details {expected: 请求里的值, actual: 服务端当前的值}；取消比 tracking_number（规范化后），
#             确认作废比 unresolved_id（记录 id）
# 16094  409  确认作废带运单号的记录时，承运商没有确认这张运单已取消 / 不存在（明确拒绝取消或结果不明），不能作废：
#             details {unresolved, carrier, tracking_number, result: refused | unclear, http_status, transaction_id,
#             errors, alerts, maybe_processed}
# 16095  502  承运商已建好运单但 WMS 写库失败（含期间 DN 已发货等业务异常），已按补偿规则处理：
#             details {carrier, action: 'create', tracking_number, transaction_id, error: {code, message},
#             compensation: {attempted, cancelled, cancel_transaction_id, error}, unresolved, maybe_processed}
#             （unresolved 为 null = 已在承运商那边取消；否则为带运单号的结果不明记录，需重试取消 / 确认作废）
# 16124  400  承运商在线对接的设置不合法（PUT / POST test /carrier/<id>/integration）：field = adapter / enabled /
#             settings.<key> / secrets.<key>
# 16125  409  承运商还有未结束的自动运单（pending / active / unknown / cancelling）时删除对接或换插件（取消运单要用这套设置）：
#             details {shipments: [{id, dn_id, status, tracking_number}]}
# 16130  409  上门揽收前置条件不满足（承运商没有可用的对接 / 不支持预约揽收 / 揽收地址或电话放不进承运商的格式 /
#             揽收方式不是 api 却要插件预约 PICKUP_METHOD_NOT_API）：details {blockers}
# 16131  400  揽收预约 / 手工记录参数不合法：field = carrier_id / shipment_ids / pickup_date / ready_time / close_time /
#             remarks / notification_email / confirm / warehouse_id / confirmation_code
# 16132  409  所选运单不能预约揽收：details {shipments: [{shipment_id, tracking_number, reason: NOT_FOUND | NOT_ACTIVE |
#             OTHER_CARRIER | DN_SHIPPED | ALREADY_SCHEDULED, ...}]}
# 16133  409  揽收预约当前状态不能取消（不是 scheduled / 揽收日已过）或确认作废（不是结果不明）：
#             details {pickup_id, status, reason, pickup_date}
# 16135  400  承运商对接方式（waybill_method / pickup_method / tracking_method）、集荷安排 pickup_schedule、公开查询页
#             tracking_url_template、自动签收天数 auto_sign_days 不合法，或所选方式现在不可选（api 要在线对接已启用且插件
#             支持；file 要有文件预设或模板）；文件方式的 file_preset（预设不存在）/ file_template（合并后的模板不合法）/
#             file_account（不是模板声明的账号项、不是文字、超长）不合法：field = 字段名（pickup_schedule.weekdays、
#             file_template.export.columns[3].value、file_account.<key> 等）
# 16136  400  手工完成发货的 shipped_at（实际交承运商的时刻）晚于现在：field shipped_at（格式不对为 16050）
# 16137  409  文件方式导出：有行有问题，整批不导出、不建记录：details {rows: [{dn_id, order_number, problems: [{column
#             （列名 / options.<key> / null）, column_index?, code, message, ...}]}], total}；code 见 README
#             （DN_NOT_FOUND / OTHER_WAREHOUSE / NOT_PACKED / OTHER_CARRIER / WAYBILL_EXISTS / OPTION_INVALID / REQUIRED /
#             TOO_LONG / UNENCODABLE / CHARACTER_NOT_ALLOWED …）
# 16138  400 / 409  文件方式用不了：400 = 请求不合法（carrier_id / dn_ids / warehouse_id / dry_run / limit 不合法，导入文件
#             没有 / 为空 / 超过 5 MB / 读不了）；409 = 承运商不是文件方式、没有可用的模板、必填账号项没填、模板不能导入：
#             field = carrier_id / dn_ids / file / file_account.<key> …
# 16139  400  运单服务选项不合法（PUT /dn/<id>/shipping-options：不是承运商声明的键、select 值不在列表、日期 / 金额 /
#             文字格式不对；创建 DN 的 shipping_options 同样校验已声明键的取值）：field = options.<key> / options /
#             shipping_options.<key>
# 14019  400  出口资料 / 结构化地址（收件人、仓库、DN 的 ship_to）的字段不是文字或超长，zip_code 与 postal_code 不一致：
#             field = 字段名（ship_to.latin.name 等）；14020 = 国家代码不是有效的 ISO 3166-1 alpha-2
# 14022  400  仓库时区（warehouses.timezone）不是合法的 IANA 时区名（如 Asia/Tokyo、America/Los_Angeles）：field = timezone
#             （doc/timezone-utc.md）
# 16076 另有 reason SHIPPING_OPTIONS_LOCKED（建运单 / 导出后改运单服务选项）/ SHIP_TO_LOCKED（建运单 / 导出后改 DN 的
#             收件地址 PUT /dn/<id>/ship-to）；16075 另用于作废导出时没有已导出的文件运单（details.waybill）
# 16117  409  单证排不下：某块文字比一页还高（实际是收件人各字段都很长，如各约 240 个 CJK 字符），reportlab LayoutError。
#             不签发、不作废旧单证，请缩短收件人信息：details {document: 'CI' | 'PL', reason: 'LAYOUT_OVERFLOW'}
#
# 业务码登记（权限 / 平台 / 主数据）：
# 16100  400  商品规格数值不合法：须为有限数；0 < weight ≤ 99999.999（kg，3 位小数）；
#             length / width / height 为正整数毫米且 ≤ 2147483647；price / discount_price 为 0 ~ 9999999999.99。
#             field = 字段名；details {field, row?}（row 只在 CSV 导入时有，为文件行号）
# 16101  400  CSV 文件内商品编码重复：details {code, rows: [首次出现的行号, 重复的行号]}
# 16102  400  数据库拒绝的数据值（DataError：超出列范围 / 类型不符等），只返回通用文案
# 16103  400  不能分配已停用的角色（已持有的停用角色可以保留）：field = roles，details {roles: [...]}
# 16104  403  只有公司管理员（company_all_access）或平台管理员能新建 / 删除仓库、修改仓库负责人（manager_id）
# 16105  403  没有 company_all_access 的调用方只能新建 / 修改 / 删除绑定到自己的 API Key
# 44004  409  主数据仍被单据 / 库存等记录引用，不能删除（请改用停用）：
#             details {entity: supplier|carrier|recipient|goods|warehouse|department, id, references: {表名: 行数}}
# 44005  409  与已有数据冲突（IntegrityError：唯一键重复 / 外键仍被引用等），只返回通用文案
# API Key 所属公司停用 / 过期 → 401 11007、绑定用户停用或已删除 → 401 11006（与登录同码）
#
# 业务码登记（库存与作业流程）：
# 16110  409  DN 有未结束的承运商自动运单（pending / active / unknown / cancelling），不能取消 DN：先在单证卡片取消运单。
#             details {shipment_id, status, tracking_number}
# 16111  409  调整单已取消（is_active=false），不能再做任何操作：details {adjustment_id}
# 16112  409  调整单当前状态不能取消（只有 pending / approved 可取消）：details {adjustment_id, status}
# 16113  409  调整单完成不了：负向调整的库位上已没有该商品的库存记录，或明细没有库位（请取消后按当前库存重新建单）：
#             details {detail_id, goods_id, location_id, adjustment_quantity}
# 16114  409  盘点单还有未录入的明细，不能完成（盘到 0 件也要明确提交）：details {detail_ids: [...]}
# 16115  400  数值字段不合法（ASN 明细 weight / volume 须为 ≥ 0 的数；库位 width / depth / height / capacity 须 > 0；
#             空串按 null 处理）：field = 字段名
# 16116  409  分拣任务的 asn_id 不能改（会造成一张 ASN 两个分拣任务）：details {asn_id}
# 16120  404  移库的来源库位没有这个商品：details {goods_id, from_location_id}（原 14003，14003 现在只表示缺仓库 id）
# 16121  409  分拣 / 拣货 / 打包追加批次：同一任务下 client_batch_id 已用过、但明细（商品 / 数量，拣货另比库位）不同：
#             details {client_batch_id, batch_id}。明细相同的重复提交不报错，200 返回已存在的批次
# 16122  409  审批人最后增 / 改 / 删过这张调整单的明细，不能自己审批（职责分离）：details {adjustment_id, details_modified_by}
#             （创建人审批仍是 403 16040）
# 16123  400  client_batch_id 不合法：须为 1~64 个字符的字符串（field = client_batch_id）
# 44009  409  库位有历史记录（上架 / 下架 / 移库 / 拣货 / 盘点 / 调整 / 库存快照）引用，不能删除（请改用停用）：
#             details {entity: 'location', id, references: {记录类型: 行数}}
# 15016  400  上到 standard / return 库位的数量超过待上架的良品部分（sorted_stock − sorted_damage_stock；破损品只能上到
#             damaged 库位）：field = quantity，details {goods_id, location_type, quantity, good_pending, damaged_pending}
#
# 业务码登记（库存底座：库存流水 / 历史库存 / 库存分析 / 补货阈值，doc/库存数值系统说明.md 第九–十三节）：
# 15017  400  补货阈值批量设置有错误行（PUT /inventory/thresholds、/thresholds/by-filter），全部不改：
#             details {errors: [{index, goods_id, goods_code, field, code, message}]}，code 为 15013（low < -1）/
#             15014（high < -1）/ 15015（high ≤ low）/ 16030（商品不在本公司）/ 15017（非整数、重复商品、没给值）；
#             请求体不合法、超过 1,000 个商品、suggest 参数不合法时 field = items / filter / suggest.<key>
# 15018  400  从破损 / 退货库位移回 standard 库位（移库）没有填原因：field = reason，details {from_location_id,
#             from_location_type, to_location_id, to_location_type}（reason 优先，没给时非空的 remark 也算原因；原因写进
#             库存流水的备注）；reason 不是文字 / 超过 255 字也是 15018
# 15019  400  库存流水 / 按库位 / 历史库存 / 库存分析接口的查询参数不合法（日期格式、未来日期、超过 400 天、未知的库存类别、
#             group_by / status / sort 取值、没指定仓库）：field = 参数名
# 15020  400  库存流水导出超过 50,000 行，请缩小筛选范围：details {total, max_rows}
# 绕过 StockService 直接改库存（STOCK_GUARD = raise）→ StockGuardViolation（未捕获异常，500 50000；生产默认只记日志）
# 16162  400  拣货只能从 standard 库位拣（批次提交 / 改量 / 完成复核）：field = location_id，details {location_id, location_type}
#
# 业务码登记（账号 / 内置权限角色 / 商品导入）：
# 10015  400  新密码不合规（8–16 位，含大小写字母、数字、特殊符号 @$!%*?&，不能有其它字符；system/user/password_policy.py）：
#             field = password / new_password，details {min_length, max_length, special_characters}
# 10016  400  商品导入文件不是 UTF-8 文本：field 无，details {reason: NOT_UTF8}（其余导入错误沿用 10004 / 10005 / 10006 /
#             10012 / 10014 / 16100 / 16101，details 带 row / rows / code / columns / value）
# 11011  409  内置权限（system/user/builtin.py）不能改代码 / 删除：details {permission, reason: RENAME | DELETE}
# 11012  409  内置角色不能改名 / 停用 / 删除（权限照常分配）：details {role, reason: RENAME | DEACTIVATE | DELETE}
#
# 业务码登记（收货规格采集：warehouse/common/spec_capture.py、warehouse/asn/spec_capture.py；17xxx = 入库规则）：
# 17020  400  规格采集设置不合法（收货通知的 spec_capture；仓库的 default_spec_capture_fields / default_spec_capture_mode /
#             spec_capture_enforcement）：field = spec_capture / spec_capture.fields / spec_capture.mode / 仓库字段名
# 17021  409  分拣完成被拦（仓库 spec_capture_enforcement = block）：分拣到的行还有待采集项、也没填跳过原因：
#             details {asn_id, enforcement, lines: [{asn_detail_id, goods_id, goods_code, goods_name, spec_pending}]}
# 17022  400  规格采集请求不合法：既没有值也没有跳过原因；长宽高没给齐（field dimensions，details {missing}）；remeasure 模式
#             没给齐要求的项（field spec_capture，details {missing}）；note 不是文字或超过 255 字（field note）
# 17023  409  只有已收货（received）或已完成（completed，补采）的收货通知能采集规格：details {asn_id, status}

class APIException(Exception):
    """API异常基类"""
    def __init__(self, message, biz_code, status_code=400, field=None, details=None):
        """
        :param message: 错误消息
        :param biz_code: 业务错误码
        :param status_code: HTTP状态码 (默认400)
        :param field: 相关字段 (可选)
        :param details: 结构化的附加信息 (可选，dict/list，响应里原样输出为 details)
        """
        super().__init__(message)
        self.biz_code = biz_code
        self.status_code = status_code
        self.field = field
        self.details = details
        self.message = message

    def __str__(self):
        return f"{self.status_code} {self.__class__.__name__}: {self.message}"

# 400 Bad Request
class BadRequestException(APIException):
    """400 错误"""
    def __init__(self, message, biz_code=40000, field=None, details=None):
        super().__init__(message, biz_code, 400, field, details)

# 401 Unauthorized
class UnauthorizedException(APIException):
    """401 错误"""
    def __init__(self, message, biz_code=41000, field=None, details=None):
        super().__init__(message, biz_code, 401, field, details)

# 403 Forbidden
class ForbiddenException(APIException):
    """403 错误"""
    def __init__(self, message, biz_code=42000, field=None, details=None):
        super().__init__(message, biz_code, 403, field, details)

# 404 Not Found
class NotFoundException(APIException):
    """404 错误"""
    def __init__(self, message, biz_code=43000, field=None, details=None):
        super().__init__(message, biz_code, 404, field, details)

# 409 Conflict
class ConflictException(APIException):
    """409 错误（资源仍被引用 / 状态冲突）"""
    def __init__(self, message, biz_code=44000, field=None, details=None):
        super().__init__(message, biz_code, 409, field, details)

# 500 Internal Server Error
class InternalServerError(APIException):
    """500 错误"""
    def __init__(self, message, biz_code=50000, field=None, details=None):
        super().__init__(message, biz_code, 500, field, details)

# 502 Bad Gateway
class BadGatewayException(APIException):
    """502 错误（上游外部服务返回错误，如承运商接口）"""
    def __init__(self, message, biz_code=50200, field=None, details=None):
        super().__init__(message, biz_code, 502, field, details)

# 504 Gateway Timeout
class GatewayTimeoutException(APIException):
    """504 错误（上游外部服务超时）"""
    def __init__(self, message, biz_code=50400, field=None, details=None):
        super().__init__(message, biz_code, 504, field, details)


# 承运商运单（自动建运单，warehouse/dn/carrier_services.py；插件见 warehouse/carrier/integrations）业务码
CARRIER_BIZ_CODES = {
    16072: "建单前置条件不满足（409，details.blockers）",
    16073: "承运商返回错误 / 结果不明（502，details.errors / transaction_id / maybe_processed / unresolved）",
    16074: "承运商超时或时间预算用完（504，details.maybe_processed / unresolved / budget_exhausted）",
    16075: "没有有效运单可取消 / 没有结果不明的记录可确认作废（409）",
    16076: "有进行中 / 有效 / 结果不明的运单时不能改箱子或报关数据（409）",
    16077: "label_format 不合法或该承运商不支持（400）",
    16078: "运单号与有效自动运单不一致，或有自动运单时新建 / 删除发货任务（409）",
    16079: "有进行中 / 结果不明的建单记录：不能再建单、不能保存运单号（409，details.unresolved）",
    16137: "文件方式导出有行有问题，整批不导出（409，details.rows）",
    16138: "文件方式用不了：请求 / 导入文件不合法（400）或承运商不是文件方式 / 模板不全（409），field",
    16139: "运单服务选项不合法（400，field = options.<key>）",
    16124: "承运商在线对接的设置不合法（400，field）",
    16125: "承运商还有未结束的自动运单，不能删除对接或换插件（409，details.shipments）",
}

def register_error_handlers(app):
    app.config['PROPAGATE_EXCEPTIONS'] = True

    # 处理自定义API异常
    @app.errorhandler(APIException)
    def handle_api_exception(error):
        response = {
            "status": "error",
            "code": error.biz_code,
            "message": error.message,
        }
        
        if error.field:
            response["field"] = error.field

        if getattr(error, 'details', None) is not None:
            response["details"] = error.details
        
        if app.config.get('FLASK_ENV') == 'development':
            response["debug"] = {
                "exception": type(error).__name__,
                "status_code": error.status_code
            }
             
        return response, error.status_code
    
    # 处理 HTTPException (Werkzeug 标准异常)
    @app.errorhandler(HTTPException)
    def handle_http_exception(error):
        # 将标准HTTP异常转换为自定义格式
        return {
            "status": "error",
            "code": error.code * 100,  # 转换为业务错误码
            "message": error.description
        }, error.code
    
    # 处理ValueError（生产环境不回显异常原文，避免泄露内部信息）
    @app.errorhandler(ValueError)
    def handle_value_error(error):
        response = {
            "status": "error",
            "code": 40000,
            "message": str(error) if app.config.get('FLASK_ENV') == 'development' else "Invalid value"
        }
        
        if app.config.get('FLASK_ENV') == 'development':
            response["debug"] = {
                "exception": type(error).__name__,
                "details": str(error)
            }
        
        return response, 400
    
    # 处理请求验证错误
    @app.errorhandler(ValidationError)
    def handle_validation_error(error):
        errors = []
        if hasattr(error, 'errors') and isinstance(error.errors, dict):
            for field, messages in error.errors.items():
                if isinstance(messages, dict):
                    for sub_field, sub_messages in messages.items():
                        errors.append({
                            "field": f"{field}.{sub_field}",
                            "message": ", ".join(sub_messages)
                        })
                else:
                    errors.append({
                        "field": field,
                        "message": ", ".join(messages)
                    })
        else:
            errors = [{"message": str(error)}]
        
        response = {
            "status": "error",
            "code": 40000,
            "message": "Invalid request parameters",
            "errors": errors
        }
        
        if app.config.get('FLASK_ENV') == 'development':
            response["debug"] = {
                "exception": type(error).__name__,
                "full_details": str(error)
            }
        
        return response, 400

    def _db_error_response(error, status, code, message):
        """数据库约束 / 数据值错误：先回滚（会话还要给请求日志用），只返回通用文案，不回显 SQL"""
        try:
            from extensions.db import db
            db.session.rollback()
        except Exception:  # pragma: no cover - 回滚失败不影响错误响应
            pass
        app.logger.warning(f"{type(error).__name__} mapped to {status}: {getattr(error, 'orig', error)!r}")
        response = {"status": "error", "code": code, "message": message}
        if app.config.get('FLASK_ENV') == 'development':
            response["debug"] = {"exception": type(error).__name__}
        return response, status

    # 唯一键重复 / 外键仍被引用等（并发下没被前置检查拦住的情况）→ 409
    @app.errorhandler(IntegrityError)
    def handle_integrity_error(error):
        return _db_error_response(error, 409, 44005, "The data conflicts with existing records")

    # 超出列范围 / 数据库不接受的值 → 400
    @app.errorhandler(DataError)
    def handle_data_error(error):
        return _db_error_response(error, 400, 16102, "Invalid data value")

    # 处理其他未捕获异常
    @app.errorhandler(Exception)
    def handle_unexpected_error(error):
        app.logger.exception("Unhandled exception occurred")
        response = {
            "status": "error",
            "code": 50000,
            "message": "Internal server error"
        }
        
        if app.config.get('FLASK_ENV') == 'development':
            response["debug"] = {
                "exception": type(error).__name__,
                "details": str(error)
            }
        return response, 500