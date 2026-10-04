# 余额监控与数据库初始化

以下命令均在项目根目录执行。

## 分组账本与余额监控

- 顶部提供“全部”、各渠道标签、以及“未分组”标签页。标签分组直接显示 New API `channels.tag`，与消费日志的用户/令牌分组不是同一概念。
- 当前标签页决定统计、排行、明细、筛选选项、Excel 导出、余额、报警记录及月度归档的范围。“全部”汇总所有渠道，不把各分组账本重复叠加。
- 齿轮中的“余额设置”属于当前账本：独立的总额度、报警阈值、起始月份和“启用余额监控”复选框。新分组默认关闭监控，未配置时仍可查看用量和导出。总额度是独立预算，不修改 New API 用户配额；“全部”的额度也不自动累加其他账本的额度。
- 齿轮中的“告警渠道”全局共用，飞书/钉钉凭据及通知启用状态不随账本切换。通知包含站点和账本名称。
- 渠道列表显示 ID、名称及启用/禁用/已删除状态，不需要逐个勾选；禁用渠道仍计入消费。
- 余额 = 当前账本额度 - 该账本起始月份以来已归档费用 - 该账本本月实时费用。金额取消费日志 `SUM(quota)/500000`，不以模型现价重新计费，也不受页面时间、用户等临时筛选影响。
- 监控库按“月份 + 渠道 ID”保留原始费用。历史月份与当月均按渠道**当前归属**汇总；渠道改标签后历史账本金额随之变化，不重读历史日志，也不改动原始费用。不要把“全部”与各分组金额再相加。
- 只显示 New API 当前渠道中存在的标签；已不存在的标签隐藏且不再独立检查告警，其历史数据、额度设置仍保留。同名标签重新出现后恢复展示。“全部”和“未分组”始终显示。
- 渠道删除后保留最后同步到的标签和名称。未曾保存的历史标签无法恢复：现存渠道历史按当前标签归类，无法确定标签的历史渠道归入“未分组”。不将未知标签猜测成某上游。
- 保存余额设置时补齐起始月份至上月所需归档，失败不将费用当零。历史追溯用于重新读取原始渠道费用：先预览前后金额并确认，才覆盖所选月份的共用渠道归档；仅调整标签不需要追溯。
- 主程序内置余额定时器每天北京时间 10:00 检查所有已启用的账本；月初补齐上月归档。没有每分钟轮询，不在进程启动时立即报警。各账本分别记录每日运行状态，某个账本失败不应阻断其他账本。
- 点击铃铛手动检查当前账本，不占用每日定时检查。关闭该账本余额监控后不进行余额告警检查或发送通知，用量统计和导出不受影响。
- 剩余额度严格小于阈值时报警；每个账本最多保留一条当前报警，下次检查恢复至阈值以上或等于阈值则删除。无已读/未读状态。
- 查询失败保留旧结果并显示失败状态，不显示为零消费。月度归档取决于原日志保留情况，无法恢复已经删除且从未归档的数据。
- 余额设置、警报、月度归档不混入用量 Excel 导出。导出仅包含当前账本的用量数据。

### 表结构与初始化

程序启动入口通过 `schema_migrations` 自动顺序应用未执行的迁移；正常 API 请求不执行迁移。
监控库保存账本和渠道库存、各账本预算/审计/检查状态、逐渠道月度费用、通知配置、
定时配额规则与执行记录，以及限时展示快照。完整表职责见[架构说明](architecture.md#数据库职责)。
余额与配额定时器独立调度，但同属一个应用容器；余额检查时间为每天 10:00。
首次同步渠道标签后建立账本；查询历史账本金额时使用保存的逐渠道费用与当前渠道归属。
无需清空数据库。上线前备份监控库，勿把迁移 SQL 执行到 New API 原库。

### 独立数据库

额度、月度归档、报警和通知渠道配置不能写入 New API 原库。首次部署时，用 PostgreSQL
管理员一次性创建独立数据库和专用账号。先进入 PostgreSQL 容器中的 `postgres` 管理库：

```bash
docker exec -it <PostgreSQL容器名> sh -lc 'psql -U "$POSTGRES_USER" -d postgres'
```

进入 `psql` 后执行以下 SQL。将 `<random-password>` 替换为强随机密码；不要使用示例占位符：

```sql
CREATE ROLE statistics_monitor LOGIN PASSWORD '<random-password>' NOSUPERUSER NOCREATEDB NOCREATEROLE;
CREATE DATABASE new_api_statistics OWNER statistics_monitor;
REVOKE CONNECT ON DATABASE new_api_statistics FROM PUBLIC;
GRANT CONNECT ON DATABASE new_api_statistics TO statistics_monitor;
```

`CREATE DATABASE` 必须在 `postgres` 等其他数据库中执行，不能先连接尚未创建的
`new_api_statistics`。上述 SQL 用于首次创建；如果角色或数据库已经存在，不要重复执行
`CREATE ROLE` 或 `CREATE DATABASE`，应核对现有 owner 和密码。

在 `.env` 添加监控库连接串。连接串中的密码含 `@`、`:`、`/`、`#` 等特殊字符时必须 URL 编码：

```ini
MONITOR_DATABASE_URL=postgresql://statistics_monitor:<random-password>@postgres:5432/new_api_statistics
```

生成一次通知凭据加密密钥，并把命令输出写入同一个 `.env`。不要把密钥提交到 Git：

```bash
docker run --rm python:3.12-slim \
  python -c 'import base64, os; print(base64.urlsafe_b64encode(os.urandom(32)).decode())'
```

```ini
NOTIFICATION_ENCRYPTION_KEY=<上一步生成的值>
```

`PG*` 仍指向 New API 原库，查询使用只读事务。`MONITOR_DATABASE_URL` 必须指向新建的独立库；服务账号不需要建库权限。新库由容器启动入口自动建表，需可创建表及读写自己拥有的表。管理员登录仍读取 New API 的用户和密码，不建立另一套登录账号。

```bash
chmod 600 .env
docker compose up -d --build statistics
docker compose ps
docker compose logs --tail=50 statistics
```

首次启动后，应用会自动创建监控表。可使用 PostgreSQL 管理员验证，正常应能看到
`balance_*`、`notification_settings`、`notification_feishu_settings` 和
`notification_dingtalk_webhook_settings`、`quota_schedule_*` 表，以及
`channel_catalog_sync_state`、`report_snapshots`、`report_snapshot_rows`：

```bash
docker exec <PostgreSQL容器名> sh -lc \
  'psql -U "$POSTGRES_USER" -d new_api_statistics -c "\\dt"'
```

Nginx 统一转发 `/cockpit/`，监控不需要新端口。未配置连接串时原统计功能照常使用，监控不可用。请将独立库与 New API 库分别纳入数据库备份。
