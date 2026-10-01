# 升级与备份

## 未发布：单容器内置定时器

当前程序只需要 `statistics` 一个容器。配置独立监控库后，网页进程自动管理余额和配额定时器，
不再部署 `balance-worker`、`quota-worker` 或开启 `quota-schedules` profile。
仅合并定时器不新增迁移：复用已有规则、执行记录、归档和配置。
本批性能修复同时包含下文的 `008`；从已发布的 `0.1.3` 升级时启动入口会自动补齐，
已应用 `001`—`008` 的数据库不再改结构，不清空数据。

切换前备份监控库、私有配置和旧镜像，使用**旧版配置**停止全部旧应用/worker：

```bash
# 在统计项目目录操作；不要对 New API 项目执行 down。
docker compose --profile quota-schedules stop
```

确认旧后台进程全部退出后，再更新 Compose 与镜像；私有 override 中也须去掉旧两个 worker 服务。
保留 `.env`、端口与现有网络，启动新版 `statistics`，确认健康检查中的两种定时器都已就绪。
源码部署使用 `docker compose up -d --build statistics`；探活命令与字段见
[运行状态检查](deployment.md#运行状态检查)。`docker compose ps` 应只列出一个统计应用服务。
旧余额 worker 没有新版领导锁；早期配额 worker 还与通知锁冲突，因此不能混用旧、新后台进程。
已有配额领导锁编号保持当前修复后的值，另新增独立余额领导锁，不复用余额/通知事务锁。

网页重启会同时暂停内置任务。余额启动只安排未来的 10:00，不立即查消费；
配额按已有到期窗口处理，错过的周期及中断请求不补发、不自动重试。现有日志和执行记录保留。
回退时先停新版应用，再恢复旧配置和镜像；数据库保留新增表，不自动恢复/覆盖业务数据。

当前改动尚未发布，必须使用包含 `timers`/Gunicorn 生命周期代码的新镜像。
不能把新单容器模板搭配旧 `0.1.3` 发布镜像，后者不会自动运行内置定时器。

## 未发布：选定 BUG 与五项性能修复

本批新增 `008_runtime_optimizations.sql`，仅在独立监控库新增三张表：
`channel_catalog_sync_state`、`report_snapshots`、`report_snapshot_rows`。
不清空、不重新计费、不改写已有费用、配置或配额执行记录，也不修改 New API 原库结构。

Docker 镜像的 entrypoint 在启动应用前执行幂等迁移。
非 Docker 的 Gunicorn 部署需要使用统一启动入口（环境变量沿用现有配置）：

```bash
python -m new_api_statistics.runtime gunicorn --bind 127.0.0.1:8000 new_api_statistics.app:app
```

也可在维护窗口单独执行 `python -m new_api_statistics.runtime`，此命令仅执行迁移，不启动网页或定时器。
随后仍须用上面的统一入口启动 Gunicorn；如果使用自定义 Gunicorn 配置，必须引入
[定时器生命周期钩子](quota.md#部署与数据库)，不能只执行迁移后裸启动 Gunicorn。
正常 API 请求不再执行 DDL；定时规则读取只检查迁移版本，缺失时返回 `503` 并提示启动迁移。
部署仍须先处理上面的旧调度器停止要求；不要让旧、新调度器同时运行。

迁移后的首次渠道同步会一次性发现历史日志里已删除的渠道 ID，成功后记录完成标记。
后续刷新只读当前渠道表，同时保留已保存的删除渠道。普通当月/月度查询发现的渠道继续用于归档，
不会因减少全表扫描而删掉已有库存。需要再次完整发现历史渠道时手动执行：

```bash
docker compose exec statistics python -m new_api_statistics.catalog_sync --full
# 非 Docker：python -m new_api_statistics.catalog_sync --full
```

此命令仅同步渠道库存和当前标签，不覆盖月度金额；来源读取失败时事务回滚，完成标记不前进。
正常刷新不再改动未变更渠道的名称、归属、时间戳或账本 ID 序列。
`last_seen_at` 仅随库存名称/归属变更而更新，是否已删除仍由本次实时渠道目录判断。

报表详情快照有效期 15 分钟，每管理员最多保留最近 10 份；在新建快照时清理过期数据。
没有请求时不运行额外轮询任务，过期快照不可再读。快照是展示辅助数据，不是历史账单或余额缓存。
回退旧镜像时保留新增表和迁移记录，无需删除任何业务数据；重新升级可直接复用。

修复范围和本地回归结果见[优化计划](optimization-plan.md#本次选定修复)。

## 升级到 0.1.3：定时用户配额

新增 `007_quota_schedules.sql`，使用现有监控库增量创建四张定时配额表。
无需清空数据库，也不修改 New API 数据库结构。`0.1.3` 历史发布使用独立配额 worker；
当前未发布版本已改为单容器内置调度，升级方式以本文顶部说明为准。
具体步骤、管理员 PAT 与不自动补发/重试的边界见[用户配额](quota.md#定时额度修改)。
本版还引入请求历史价格拆行、手工批量配额管理和表格金额两位小数展示。
升级时一并更新 Compose 与 Nginx 示例中的 `/quota/` 入口；发布步骤见[0.1.3 发布说明](releases/v0.1.3.md)。

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
docker compose -f compose.release.yml logs --tail=100 statistics
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

## 0.1.3：历史价格明细

此改动不增加监控库迁移。部署前应确认 New API 只读账号可读取 `logs.other`、
`logs.group` 和相关 `options`；升级后明细改按请求发生时的价格归并，
因此同一用户模型可能由一行变成多行。已归档的月度费用、历史消费日志和实际金额不会被改写。
若旧日志没有可还原的价格快照，相应 Token 单价留空；倍率变动但无法用当前匹配档位
求得有效缓存读解时保留原始 Token。相关口径与限制见[统计口径](calculation.md)。
