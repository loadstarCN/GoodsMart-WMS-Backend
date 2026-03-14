-- ============================================================
-- WMS Webhook 集成迁移
-- 用于 Wholesale API ↔ WMS 对接
-- 执行前请先备份数据库
-- ============================================================

-- 1. ASN 表新增 order_number 字段（关联 Wholesale 到货单号）
ALTER TABLE asns ADD COLUMN IF NOT EXISTS order_number VARCHAR(100);
CREATE INDEX IF NOT EXISTS ix_asns_order_number ON asns (order_number);

-- 2. APIKey 表新增 company_id + Webhook 字段
ALTER TABLE api_keys ADD COLUMN IF NOT EXISTS company_id INTEGER REFERENCES companies(id) ON DELETE SET NULL;
CREATE INDEX IF NOT EXISTS ix_api_keys_company_id ON api_keys (company_id);
ALTER TABLE api_keys ADD COLUMN IF NOT EXISTS webhook_url VARCHAR(512);
ALTER TABLE api_keys ADD COLUMN IF NOT EXISTS webhook_secret VARCHAR(128);

-- 3. 创建 webhook_events 表
CREATE TABLE IF NOT EXISTS webhook_events (
    id SERIAL PRIMARY KEY,
    api_key_id INTEGER NOT NULL REFERENCES api_keys(id) ON DELETE CASCADE,
    event_type VARCHAR(50) NOT NULL,
    payload JSON NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'pending',
    attempts INTEGER NOT NULL DEFAULT 0,
    next_retry_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    sent_at TIMESTAMP,
    last_error TEXT
);
CREATE INDEX IF NOT EXISTS ix_webhook_events_api_key_id ON webhook_events (api_key_id);
CREATE INDEX IF NOT EXISTS ix_webhook_events_event_type ON webhook_events (event_type);
CREATE INDEX IF NOT EXISTS ix_webhook_events_status ON webhook_events (status);

-- 4. 配置 Wholesale API 的 APIKey（示例）
-- UPDATE api_keys
-- SET company_id = 1,
--     webhook_url = 'https://wholesale-api.example.com/api/webhook/wms',
--     webhook_secret = 'your-hmac-secret-here'
-- WHERE key = 'your-wholesale-api-key';

-- 5. 设置定时任务（每分钟推送待发送事件）
-- crontab: * * * * * cd /path/to/GoodsMart-WMS-Backend && flask webhook push
