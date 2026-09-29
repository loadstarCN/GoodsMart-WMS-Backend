# 手工联调脚本

这里的脚本会**直接读写真实数据库 / 调用真实 SMTP 或 Webhook 地址**，不是自动化测试：

- `webhook_smoke.py`：用当前配置创建一个临时 API Key 并打印其明文，用来联调 webhook 推送。跑完务必删除该 Key。
- `webhook_real_smoke.py`：会改写 `system_name='goodsmart'` 的 API Key 配置。

它们此前放在仓库根目录、文件名以 `test_` 开头，容易被当成 pytest 用例误跑。现在移到这里，且不在 `tests/` 目录下，`pytest tests/` 不会收集到。
运行前请确认 `FLASK_ENV` 指向的是你想操作的环境。
