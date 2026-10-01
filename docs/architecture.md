# 当前架构

本文描述仓库当前代码的模块职责和数据边界，不作为历史变更记录。
部署参数与入口见[部署](deployment.md)，业务计算见[统计口径](calculation.md)。

## 运行拓扑

```text
浏览器 / API 调用方
        |
   Nginx（可选）
        |
statistics：一个应用容器
  ├─ Gunicorn：网页与 API
  ├─ 内置余额定时器：北京时间每天 10:00
  └─ 内置配额定时器：每天 / 每周一 / 每月1日 00:00
        |
        ├─ New API PostgreSQL：只读日志、用户、配置和渠道
        ├─ 独立监控 PostgreSQL：预算、归档、报警、规则与展示快照
        ├─ New API 管理 API：管理员确认或规则到期时增减用户配额
        └─ 飞书 / 钉钉：已启用的报警通知
```

两个 PostgreSQL 数据库可以位于同一实例，但必须分库配置。
统计程序不直接使用 Redis，不需要消息队列或额外的后台应用容器。
网页路径为 `/statistics/`、`/quota/`；健康检查为 `/healthz`。
所有时间边界统一使用北京时间。

## 启动、调度与退出

1. Docker entrypoint 执行 `runtime.py`，在独立监控库内按编号应用未执行的迁移；失败则不启动网页。
2. Gunicorn 加载 `gunicorn_conf.py`；子进程初始化完成后，`timers.py` 启动两种定时器线程。
3. 每个网页进程均可待命；每种定时器各自竞争一个 PostgreSQL 会话领导锁，只有持锁者运行对应任务。
4. 领导连接丢失或进程退出后，其他网页进程可以接管；余额、通知、迁移和配额的锁不混用。
5. 收到退出信号后停止领取新任务与发起下一组请求，并留出保存当前组结果的时间。

模块导入和 HTTP 请求不会启动定时器，fork 前不建立调度连接。
容器退出会同时暂停网页和定时任务；关闭浏览器不会停止已启用的配额规则。
未配置 `MONITOR_DATABASE_URL` 时不启动定时器，仍可使用基础统计与手工配额操作。

余额启动只安排未来的 10:00，不立即查询消费或报警。
配额新建/重新启用从下个周期执行；到期任务有最多 5 分钟的启动窗口，错过的周期不补发，
中断或结果不明确的请求不自动续跑、重试或回滚。
等待期间只检查连接和规则元数据，不按分钟轮询消费或用户额度。

`/healthz` 检查当前网页进程的两种线程及监控库内两种领导锁。
配置监控库但调度未就绪时返回 `503`；调度就绪不等于某条任务已执行成功。
部署与自定义 Gunicorn 配置要求见[用户配额](quota.md#部署与数据库)。

## 数据流

### 用量报表与 Excel

`app.py` 接收时间、账本和用户等筛选条件，`scopes.py` 解析账本的渠道范围，
`report.py` 在 New API 只读事务中聚合日志。`historical_prices.py` 与 `expression_prices.py`
还原请求价格、匹配当前档位；消费金额始终来自日志，不重写消费记录。
`metadata_fallback.py` 为无法正常解析的元数据提供只读流式兼容路径。

网页列表按需关联 `report_snapshots.py` 保存的不可变公式详情；悬浮时不重查价格或日志。
Excel 独立查询并生成全部工作表，不受网页分令牌模式或开发者列影响；所有外部字符串写作文本。
详见[统计口径](calculation.md)、[API](api.md)和[性能机制](performance.md)。

### 分组账本、归档与报警

账本为“全部”、New API 渠道标签和“未分组”。用量与预算使用当前所选账本；通知配置全局共用。
`scopes.py` 同步当前渠道名称与归属，保留已删除渠道的库存信息。
`balance.py` 按“月份 + 渠道 ID”保存费用，查询时按渠道当前归属汇总。
已归档月份取监控库，本月每次从 New API 实时计算；标签变更不重拉历史费用。

每天 10:00 检查已启用账本，并在需要时补齐已结束月份的归档。
铃铛和 `/statistics/api/alert` 可即时检查；`/statistics/api/balance` 只计算余额，不发送通知。
每个账本只保存一条当前报警，余额恢复后删除；通知失败不撤销归档或报警。
详见[余额监控](monitoring.md)与[通知渠道](notifications.md)。

### 用户配额

手工操作先预览，管理员确认后按最多 5 人并发一组逐用户调用 New API 管理接口。
定时规则绑定用户组及执行管理员，任务开始时冻结规则并生成逐人记录；执行时重新检查用户与权限。
定时执行发送请求前持久化“请求中”，组内结果全部保存后才开始下一组。

PAT 从 New API 用户表按管理员读取，不发给浏览器，不保存在规则或 `.env`。
不通过 SQL 修改用户额度；远端接口没有本功能所需的幂等键，因此结果不明确时必须人工核对。
详见[用户配额](quota.md)。

## 模块职责

| 模块 | 职责 |
|---|---|
| `app.py`、`auth.py` | 网页/API 路由、输入边界、管理员身份校验 |
| `runtime.py`、`gunicorn_conf.py`、`timers.py` | 启动迁移、进程生命周期、内置定时器与健康状态 |
| `balance.py`、`balance_worker.py` | 余额、归档、报警、余额时间边界及失败记录 |
| `scopes.py`、`catalog_sync.py` | 渠道标签账本、渠道库存同步与显式完整发现 |
| `report.py`、`usage.sql`、`metadata_fallback.py` | 用量聚合、异常日志兼容、排名和 Excel |
| `historical_prices.py`、`expression_prices.py` | 请求历史价格、表达式安全解析与档位匹配 |
| `report_snapshots.py` | 有效期及所有者受限的展示详情快照 |
| `quota.py` | 手工额度校验、权限复核、五人并发调用 |
| `quota_schedule.py`、`quota_schedule_executor.py`、`quota_worker.py` | 规则、周期领取、逐人执行记录与内置配额调度循环 |
| `notifications.py`、`notification_channels/` | 通知配置、凭据加密与飞书/钉钉发送 |
| `static/`、`templates/` | 页面、局部多选筛选、明细展示及设置交互 |

模块名中的 `worker` 是内部辅助/调度代码，不代表需要另起应用容器。

## 数据库职责

New API 原库只读 `logs`、`users`、`options`、`channels`，字段要求见[兼容范围](compatibility.md)。
监控库承担以下职责：

| 表或表组 | 当前用途 |
|---|---|
| `schema_migrations` | 记录已应用的迁移文件 |
| `balance_scopes`、`balance_channel_inventory`、`channel_catalog_sync_state` | 账本、渠道 ID/名称/当前归属与完整发现标记 |
| `balance_settings`、`balance_settings_audit` | 各账本预算、阈值、起始月份、开关、配置版本与审计 |
| `balance_channel_archive_months`、`balance_month_channels` | 已归档月份及逐渠道原始费用；月度账本读数使用当前渠道归属 |
| `balance_months`、`balance_month_scopes`、`balance_scope_backfill` | 汇总及归档辅助记录；不能替代逐渠道费用的查询依据 |
| `balance_excluded_channels` | 余额计算使用的渠道排除记录，不删除原始费用 |
| `balance_state`、`balance_alerts`、`balance_daily_runs` | 各账本最近检查结果、当前报警与每日运行记录 |
| `notification_settings`、`notification_feishu_settings`、`notification_dingtalk_webhook_settings` | 全局通知选择、发送状态与各渠道加密凭据 |
| `quota_schedule_rules`、`quota_schedule_rule_groups` | 定时配额规则及所选用户组 |
| `quota_schedule_runs`、`quota_schedule_run_items` | 每周期规则快照、任务及逐用户执行结果 |
| `report_snapshots`、`report_snapshot_rows` | 限时展示公式，非历史账单或余额缓存 |

迁移由 `migrations/001`～`008` 顺序构建当前结构；已应用的脚本不重复执行。
迁移文件属于运行代码，不能因文档不保留历史而删除或重新编号。

## 认证与安全边界

- 网页、静态资源及内部管理接口使用 New API 管理员 Basic Auth，校验密码哈希、角色、启用与未删除状态。
- 对外余额与报警接口使用管理员 PAT Bearer；模型调用 Key 不用于管理认证。
- 网页设置与额度操作检查 JSON 和对应请求来源标记，敏感配置不通过 API 回传。
- 通知凭据使用 `NOTIFICATION_ENCRYPTION_KEY` 加密，仅保存到独立监控库；密钥需另行备份。
- 配额仅通过 New API 官方管理接口修改，当前代码不提供额外 RBAC、登录会话或 MFA 绕过机制。
- 源库连接采用只读事务，监控库承担应用写入；文档与 Git 不保存生产地址、凭据或真实用户账单。
