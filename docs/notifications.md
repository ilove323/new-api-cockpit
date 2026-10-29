# 通知渠道

在统计页齿轮的“报警渠道 · 全局”中，先选择渠道，再设置“启用渠道通知”。
开关、配置版本和最近发送状态都属于所选渠道；飞书、钉钉、企业微信、邮件可以同时开启。
所有账本共用这组渠道配置，不随账本切换；每个账本自己的监控开关和额度仍然独立。

修改后点击“保存渠道”。“发送测试消息”只测试当前渠道的已保存配置，即使该渠道关闭也可以测试；
有未保存修改时不能测试。切换下拉框只读取配置，不开关其他渠道，也不发送通知。
报警与测试消息包含从 New API `options.SystemName` 实时读取的站点名；报警还包含账本名称。

## 飞书企业自建应用

填写 App ID、App Secret、接收目标类型及 ID。
群聊使用 `chat_id`（`oc_` 开头），个人使用企业内的 `user_id`，不使用 `open_id`。
应用需启用机器人能力，申请 `im:message:send_as_bot` 权限并发布，完成租户审批。
发送群消息前将机器人加入目标群，向个人发送时需满足应用可用范围，并开通“获取用户 user ID”权限。
仅发送通知，不配置事件订阅、回调或长连接；服务需能访问 `https://open.feishu.cn`。
详见[发送消息](https://open.feishu.cn/document/server-docs/im-v1/message/create)和
[自建应用访问凭证](https://open.feishu.cn/document/server-docs/authentication-management/access-token/tenant_access_token_internal)。

## 钉钉自定义 Webhook 机器人

填写目标群机器人提供的完整 Webhook URL。
安全设置启用了“加签”时，同时勾选“启用加签”并填写 `SEC` 开头的密钥。
使用自定义关键词时，关键词需能匹配通知内容，例如“余额”。
仅向 Webhook 发送文本消息，不申请企业应用凭证、不接收事件，也不配置回调；
服务需能访问 `https://oapi.dingtalk.com`。详见
[自定义机器人接入](https://open.dingtalk.com/document/robots/custom-robot-access)和
[自定义机器人安全设置](https://open.dingtalk.com/document/robots/customize-robot-security-settings)。

## 企业微信群机器人

在统计页齿轮 → **报警渠道 · 全局**中选择“企业微信群机器人”。

1. 在目标企业微信群的聊天信息中打开“消息推送”，创建自定义消息推送并复制完整 Webhook 地址。入口可能随客户端版本变化，参见[腾讯官方指引](https://intl.cloud.tencent.com/zh/document/product/1254/78645?lang=zh)。
2. 粘贴 `https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=…` 完整地址。不填写 CorpID、AgentID、应用 Secret 或钉钉加签密钥。
3. 按需填写 @成员 UserID 或手机号，一行一个，也支持逗号、分号分隔；UserID 不是成员昵称，手机号须与群成员关联。填写 `@all` 会提醒所有人；均留空则不 @。Cockpit 每类最多接受 100 项，重复项去重。
4. 保存渠道，再点击“发送测试消息”；确认后勾选“启用渠道通知”并再次保存。测试也会使用已保存的 @名单。保存配置本身不发送消息。

仅支持官方 HTTPS 群机器人地址、文本消息和向群内发送通知，不提供个人应用消息、接收消息或回调接口。服务器需能直接访问 `qyapi.weixin.qq.com:443`，不经过环境代理、不跟随 HTTP 重定向。
发送同时检查 HTTP 状态和企业微信业务错误码；失败只显示安全错误信息，不返回完整 Webhook、群密钥或远端错误正文。接口说明见[群机器人配置](https://developer.work.weixin.qq.com/document/path/91770)。

Webhook 加密保存，重新编辑时不回显；地址留空沿用已保存值，@名单留空则清除提醒对象。关闭渠道后配置仍保留，可与其他通知渠道同时使用。
每次报警或测试只发一条消息，文本超过 2048 个 UTF-8 字节时报告失败，不静默截断账务内容或拆成多次发送。错误或超时不会自动重发；超时可能已经送达，可先检查群消息及最近发送状态。

## 邮件通知

填写 SMTP 主机、端口、加密方式、发件邮箱和收件邮箱，可设置发件人名称。
主机只填写域名或 IP，不带 `smtp://`、路径或端口；邮箱只填写地址，发件人名称另填。
支持 ASCII 邮箱地址和中文发件人名称、主题及正文。最多 100 个收件人，页面每行一个，也支持逗号或分号分隔；重复地址只发送一次。

| 加密方式 | 连接方式 | 常用端口 |
|---|---|---|
| SMTP（不加密） | 普通 SMTP | 25 |
| SMTP + STARTTLS | 建立 SMTP 连接后必须升级 TLS | 587 |
| SMTPS（SSL/TLS） | 建立连接时直接使用 TLS | 465 |

端口可以自定义。默认 STARTTLS；两种 TLS 模式均校验证书与主机名，不支持时不降级为明文。
普通 SMTP 的邮件内容以及认证凭据可能以明文传输，公网建议使用 STARTTLS 或 SMTPS。
传输实现使用 Python 标准库，协议行为见 [smtplib 文档](https://docs.python.org/3.12/library/smtplib.html)。

- **认证发信**：勾选“启用 SMTP 认证”，填写用户名和密码或邮箱授权码。
- **匿名发信**：不勾选认证；应用不发送 SMTP AUTH，不使用已保存的密码。邮件服务器必须允许应用来源中继，否则会被拒绝。
- 服务器需要能访问所填主机和端口。邮件服务器接受消息不等于最终入箱，退信、垃圾邮件和域名发信策略由邮件系统处理。
- 部分收件人被拒绝时显示失败，其他收件人可能已经收到；超时或断连也可能已投递，不自动重试。

## 凭据与数据库

在安装依赖的环境中生成一次加密密钥，写入部署环境 `.env` 的 `NOTIFICATION_ENCRYPTION_KEY`：

```bash
.venv/bin/python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'
```

密钥必须持久保留并安全备份，不要每次部署重新生成。
飞书 App Secret、钉钉 Webhook 和加签密钥、企业微信 Webhook、邮件密码均加密保存在独立监控库；
API 不回传凭据或密文。编辑凭据时留空沿用原值。
更改飞书 App ID 后需重新填写 Secret；更改邮件主机、端口、加密方式或用户名后需重新填写密码，避免向新服务器发送旧凭据。
匿名邮件不需要 SMTP 密码；未保存凭据时不要求通知加密密钥。

`notification_settings` 以 `channel` 为主键，每个渠道一行，保存独立开关、版本号与发送状态。
凭据及其他提供方字段分别保存在 `notification_feishu_settings`、
`notification_dingtalk_webhook_settings`、`notification_wecom_webhook_settings`、`notification_email_settings`。
同时保存不同渠道互不覆盖；同一渠道使用版本号防止覆盖其他管理员的修改。

## 发送时机

每天北京时间 10:00、点击铃铛或调用 `/cockpit/api/statistics/alert` 的成功检查中，
账本余额低于阈值且该账本监控启用时，向所有已启用通知渠道分别发送一次。
关闭的渠道不接收自动报警；一个渠道失败不会阻止其他渠道，发送状态分别记录。
反复检查会再次发送；普通页面加载、`/cockpit/api/statistics/balance` 查询和保存渠道不发送。
余额恢复清除本地报警，不发送恢复消息，已发出的通知不会撤回。

发送失败不回滚余额检查，不做每分钟重试，下次正常检查再尝试。
飞书访问凭证按有效期缓存；钉钉按配置生成 HMAC-SHA256 签名。
邮件使用 UTF-8 纯文本正文，不发送附件。所有渠道都不记录凭据或远端原始响应。

分发与加密位于 `src/new_api_cockpit/notifications.py`；渠道实现独立放在
`notification_channels/feishu_app.py`、`dingtalk_webhook.py`、`wecom_webhook.py`、`email_smtp.py`。
通知配置不影响消费金额、月度归档、用户额度或 Excel 导出。

企业微信通过追加的 `012_wecom_webhook.sql` 迁移初始化，首次升级默认关闭、配置为空；不覆盖飞书、钉钉、邮件或余额设置。只变更 Cockpit 监控库，不需要 New API 新权限、镜像升级或新增环境变量，继续使用已有的 `NOTIFICATION_ENCRYPTION_KEY`。
