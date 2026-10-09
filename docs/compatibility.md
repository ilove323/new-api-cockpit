# New API 兼容范围

支持 PostgreSQL 后端，Python 要求 3.12+。CI 覆盖 PostgreSQL 15/16；
接入其他版本或自定义 fork 时，请核对下列字段、登录协议和计费单位。SQLite、MySQL 暂不支持。

依赖字段：

| 表 | 字段 |
| --- | --- |
| logs | id, created_at, user_id, username, token_id, token_name, model_name, quota, prompt_tokens, completion_tokens, other, type, group, channel_id, channel_name |
| users | access_token, id, username, display_name, group, role, status, quota, used_quota, deleted_at |
| tokens | id, user_id, key, name, status, created_time, accessed_time, expired_time, remain_quota, used_quota, unlimited_quota, model_limits_enabled, model_limits, allow_ips, group, cross_group_retry, auto_groups, deleted_at |
| options | key, value |
| channels | id, name, status, tag；智力测试还读取 type, models, group, priority |

用户管理还读取 `users.remark`；PAT 补建按版本存在的 `access_token_created_at` 写入创建时间。
源库 PAT 须是可回读的原生格式；加密或只保存哈希的 fork 不适用。
KEY 编辑依赖官方令牌接口，自动分组选项依赖其 `auto_groups` 支持。

消费日志为 `type=2`，`quota / 500000` 为消费金额；明细表勾选“失败请求”时读取
`type=5`，从 `other.status_code` 读取错误码；元数据损坏或缺少可解析错误码时保留失败次数，显示“未知”。
消费日志元数据损坏时保留消费金额，无法确认的用量为 `null`，详见[异常日志](calculation.md#异常日志与展示详情)。
网页登录依赖 New API 的短期访问 JWT、`POST /api/user/auth/refresh`、
`POST /api/user/auth/logout` 与 `GET /api/user/self`；只允许 role >= 10、status=1 的有效会话。
登录协议按 v1.0.0-rc.40 对应源码核对，接入版本须提供上述 JWT 会话接口。
会话验证通过官方 API 完成，无需读取 `user_sessions` 或 Redis，也无需额外数据库权限。

请求发生时的 Token 单价从消费日志 `other`
中的 `model_ratio`、`completion_ratio`、`cache_ratio`、`cache_creation_ratio_5m`
（或 `cache_creation_ratio`）还原；表达式计费依赖 `expr_b64` 和 `matched_tier`。
当前档位与分组倍率分别读取 options 的 `billing_setting.billing_mode`、
`billing_setting.billing_expr`、`ModelRatio`、`CompletionRatio`、`CacheRatio`、
`CreateCacheRatio` 和 `GroupRatio`。这些日志字段缺失、表达式不受支持或按次计费时，
Token 单价可能留空，但消费金额仍取日志，不能用当前单价冒充历史价格。
金额单位和 quota 换算必须与实际站点一致。

缓存字段和模型语义见[统计口径](calculation.md)。自定义 fork 改动数据库、
配额单位或缓存语义时，须以虚构样本验证后接入。

渠道质量额外读取 `logs.request_id`、`is_stream`、`use_time`、`completion_tokens`；缺失时降级显示，不强制增加源库字段。
精确渠道尝试依赖 `other.admin_info.request_policy` 的 attempt/channel_id/elapsed_ms/decision；
首响应依赖 `other.frt`，异常流式状态依赖 `other.stream_status`。没有这些字段时不能还原相同质量口径，见[渠道质量](quality.md)。

智力测试依赖管理员模型目录 `/api/models/`（含渠道模型）、所属用户的官方令牌管理接口及管理员指定渠道能力。
Anthropic 类型 14 使用 `/v1/messages`，其他渠道使用 `/v1/chat/completions`。
目录仅列支持文本生成且当前管理员有可用渠道的模型；对应渠道不接受上述协议时会直接报错，不切换协议重试。
Claude 必需的 `max_tokens` 由 New API 常规转发按配置补齐，请求体透传模式需由上游提供默认值，见[智力测试](intelligence.md)。
