# New API 兼容范围

当前实现直读 PostgreSQL，不支持 SQLite 或 MySQL。Python 要求 3.12+；
CI 配置覆盖 PostgreSQL 15/16，其他版本需自行验证。尚无按 New API 发布版本验证的兼容矩阵，
不能仅凭协议兼容就断言数据库兼容。

依赖字段：

| 表 | 字段 |
| --- | --- |
| logs | id, created_at, user_id, username, token_id, token_name, model_name, quota, prompt_tokens, completion_tokens, other, type, group, channel_id, channel_name |
| users | access_token, id, username, display_name, group, password, role, status, quota, used_quota, deleted_at |
| tokens | id, user_id, key, name, status, created_time, accessed_time, expired_time, remain_quota, used_quota, unlimited_quota, model_limits_enabled, model_limits, allow_ips, group, cross_group_retry, auto_groups, deleted_at |
| options | key, value |
| channels | id, name, status, tag |

用户管理还读取 `users.remark`；PAT 补建按版本存在的 `access_token_created_at` 写入创建时间。
源库 PAT 须是可回读的原生格式；加密或只保存哈希的 fork 不适用。
KEY 编辑依赖官方令牌接口，自动分组选项依赖其 `auto_groups` 支持。

消费日志为 `type=2`，`quota / 500000` 为消费金额；明细表勾选“失败请求”时读取
`type=5`，从 `other.status_code` 读取错误码；元数据损坏或缺少可解析错误码时保留失败次数，显示“未知”。
消费日志元数据损坏时保留消费金额，无法确认的用量为 `null`，详见[异常日志](calculation.md#异常日志与展示详情)。
认证要求 bcrypt 密码、
role >= 10、status=1 且 deleted_at 为空。请求发生时的 Token 单价从消费日志 `other`
中的 `model_ratio`、`completion_ratio`、`cache_ratio`、`cache_creation_ratio_5m`
（或 `cache_creation_ratio`）还原；表达式计费依赖 `expr_b64` 和 `matched_tier`。
当前档位与分组倍率分别读取 options 的 `billing_setting.billing_mode`、
`billing_setting.billing_expr`、`ModelRatio`、`CompletionRatio`、`CacheRatio`、
`CreateCacheRatio` 和 `GroupRatio`。这些日志字段缺失、表达式不受支持或按次计费时，
Token 单价可能留空，但消费金额仍取日志，不能用当前单价冒充历史价格。
金额单位和 quota 换算必须与实际站点一致。

缓存字段和模型语义见[统计口径](calculation.md)。自定义 fork 改动数据库、
配额单位或缓存语义时，须以虚构样本验证后接入。
