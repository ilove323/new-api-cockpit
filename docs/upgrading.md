# 升级与备份

## 未发布：定时用户配额

新增 `007_quota_schedules.sql`，使用现有监控库增量创建四张定时配额表。
无需清空数据库，也不修改 New API 数据库结构。网页与 `quota-worker` 必须使用包含本功能的同版镜像。
`quota-worker` 为可选服务，需要显式启用 `quota-schedules` profile；未启用时只保存规则，不会执行增减。
具体步骤、管理员 PAT 与不自动补发/重试的边界见[用户配额](quota.md#定时额度修改)。

## 通用升级与备份步骤

升级前记录应用版本或镜像 digest，并分别备份 New API 数据库、监控数据库、
部署 .env 以及 NOTIFICATION_ENCRYPTION_KEY。备份应存放在仓库外。

监控库可以使用 PostgreSQL 管理员导出：

```bash
docker exec <PostgreSQL容器名> sh -lc \
  'pg_dump -U "$POSTGRES_USER" -Fc new_api_statistics' > /安全备份目录/monitor.dump
```

维护者发布镜像后，修改 compose.release.yml 使用的 IMAGE_TAG，再执行：

```bash
docker compose -f compose.release.yml pull
docker compose -f compose.release.yml up -d
docker compose -f compose.release.yml logs --tail=100 balance-worker
```

监控库初始化在事务和 advisory lock 内运行。schema_migrations 保存已执行脚本名称；
脚本只执行一次。001_initial.sql 同时兼容空库和此前无版本表的监控库。
它保留原有预算、归档和渠道配置，并迁移旧通知表、清理已废弃的已读状态。
已有未使用版本表的安装升级时仍需先备份。

后续数据库结构变更应新增编号递增的迁移文件。迁移失败回滚事务，不写入版本记录。
此版本不提供自动降级 SQL；涉及删列等变更时，
仅回退镜像可能不足以恢复服务，应同时恢复升级前监控库和原加密密钥。

应用对 New API 使用只读查询，监控迁移仅作用于 MONITOR_DATABASE_URL 指定库。

## 升级到 0.1.1

`0.1.1` 会自动执行 `002` 至 `004` 迁移，增加渠道排除规则、逐渠道月度归档和
钉钉 Webhook 配置表。首次余额检查或保存设置时，系统会尝试把已有月度总额拆分为
“月份 + 渠道 ID”明细；因此升级前应确认 New API 消费日志仍覆盖累计起始月份。
若重新读取的历史金额低于旧月度归档，自动拆分会失败并保留旧数据，避免静默覆盖。
管理员仍可在页面通过“追溯历史计费”查看逐月差额，并在明确确认后使用当前完整数据覆盖。

旧版钉钉企业应用凭据无法转换成群机器人 Webhook。迁移会删除旧凭据表，并在旧钉钉渠道
处于选中状态时切换到 `dingtalk_webhook`、关闭通知并提示重新配置。飞书配置、预算、警报和
已有月度总额不会因此删除。重新启用前请在“报警渠道”中填写 Webhook URL，并按机器人安全
设置决定是否填写加签密钥。

## 0.1.1 余额 API 变更

对外接口改用 New API 管理员 PAT：报警路径改为 /statistics/api/alert；
/statistics/api/balance 返回实时余额，不再返回网页内部状态。网页状态已同步迁移。
只读账号需具备 users.access_token 查询权限；无需数据库结构迁移。
具体请求和权限范围见 [API 文档](api.md)。

## 升级到 0.1.2

升级前备份独立监控库。启动时自动执行 `005_balance_scopes.sql` 和
`006_scope_visibility.sql`，为“全部”、当前渠道标签和“未分组”建立独立账本与余额设置，
并记录标签是否仍在 New API 中使用。既有预算、报警及设置归入“全部”；不清空旧归档。

月度逐渠道费用仍是历史账务的原始数据。各标签历史金额改为按**当前渠道归属**动态汇总；
已归档月份不会重新拉取消费日志。渠道修改标签时，相应月份金额会在不同账本间移动，
“全部”的金额保持不变。New API 中已不存在的标签从页面隐藏并停止独立告警，
但保留账本和设置，同名标签出现后恢复。

已删除渠道无法再从 New API 取得标签；若统计库以前记录过其归属则保留，
否则归入“未分组”。如需人工指定，只修改监控库的渠道库存归属，
不要修改 New API 历史日志。升级不会自动猜测其原标签。

## 开发分支：历史价格明细（未发布）

此改动不增加监控库迁移。部署前应确认 New API 只读账号可读取 `logs.other`、
`logs.group` 和相关 `options`；升级后明细改按请求发生时的价格归并，
因此同一用户模型可能由一行变成多行。已归档的月度费用、历史消费日志和实际金额不会被改写。
若旧日志没有可还原的价格快照，相应 Token 单价留空；倍率变动但无法用当前匹配档位
求得有效缓存读解时保留原始 Token。相关口径与限制见[统计口径](calculation.md)。
