# 通知渠道

以下命令均在项目根目录执行。

## 报警渠道

报警和测试消息均包含站点名称，实时读取 New API 数据库的 `options.SystemName`，与页面名称一致。

齿轮中的“报警渠道”选择“飞书企业自建应用”，填写 App ID、App Secret、接收目标类型及 ID。
群聊使用 `chat_id`（`oc_` 开头），个人使用企业内的 `user_id`，不使用 `open_id`。
额度设置和渠道设置分别保存；更改渠道后先保存，再点击“发送测试消息”。渠道默认关闭。
测试发送不要求开启自动通知，也不会创建余额报警或修改额度。

飞书应用需启用机器人能力，申请 `im:message:send_as_bot` 权限并发布，完成租户审批。
发送群消息前将机器人加入目标群，向个人发送时需满足应用可用范围，并开通“获取用户 user ID”权限。
仅发送通知，不配置事件订阅、回调或长连接；服务需能访问 `https://open.feishu.cn`。
详见[发送消息](https://open.feishu.cn/document/server-docs/im-v1/message/create)和
[自建应用访问凭证](https://open.feishu.cn/document/server-docs/authentication-management/access-token/tenant_access_token_internal)。

选择“钉钉自定义 Webhook 机器人”时，填写目标群机器人提供的完整 Webhook URL。
若机器人安全设置启用了“加签”，同时勾选“启用加签”并填写 `SEC` 开头的加签密钥；
使用自定义关键词安全策略时，关键词需能匹配通知内容，例如“余额”。本实现仅向 Webhook 发送
文本消息，不申请企业应用凭证、不接收事件，也不配置回调；服务需能访问
`https://oapi.dingtalk.com`。详见钉钉开放平台的
[自定义机器人接入](https://open.dingtalk.com/document/robots/custom-robot-access)和
[自定义机器人安全设置](https://open.dingtalk.com/document/robots/customize-robot-security-settings)。

在安装依赖的环境中生成一次加密密钥，写入部署环境 `.env` 的 `NOTIFICATION_ENCRYPTION_KEY`：

```bash
.venv/bin/python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'
```

密钥是应用容器的统一配置，必须持久保留并安全备份，不要每次部署重新生成。
飞书 App Secret、钉钉 Webhook URL 和钉钉加签密钥均加密保存到独立监控数据库，
API 不回传凭据或密文；编辑时留空沿用原值。更改飞书应用 ID 后需重新填写 App Secret。
数据库备份需要配合加密密钥才能恢复通知能力。
公共表 `notification_settings` 仅保存当前启用渠道及最近发送状态；飞书配置保存在
`notification_feishu_settings`，钉钉配置保存在 `notification_dingtalk_webhook_settings`。
切换渠道不会覆盖另一渠道的凭据。

每天北京时间 10:00、点击铃铛或调用 `/cockpit/statistics/api/alert` 的成功检查中，
余额低于阈值且渠道已启用就发送一次通知。
反复点击铃铛会再次发送；普通页面加载和保存渠道不发送。余额恢复清除本地报警，不发送恢复消息，
已发出的渠道消息不会撤回。最新发送时间与失败原因保存在独立库并显示在渠道设置里。
发送失败不回滚余额检查，不做每分钟重试；下次正常检查再尝试。网络超时可能已经送达，故不盲目重发。
飞书访问凭证按有效期缓存，过期刷新；钉钉请求按配置生成毫秒时间戳与 HMAC-SHA256 签名。
请求不跟随重定向，不记录 Webhook、访问令牌、密钥或响应中的敏感信息。

通用配置与分发在 `src/new_api_statistics/notifications.py`；各渠道独立放在同包的 `notification_channels/`，
当前实现为 `feishu_app.py` 和 `dingtalk_webhook.py`。报警配置不影响原统计和 Excel 导出。
