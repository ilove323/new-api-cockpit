# 架构

## 运行拓扑

```text
浏览器
        |
   同源 HTTPS 入口（Nginx 等反向代理）
        |
statistics：一个应用容器
  ├─ Gunicorn：网页与 API
  ├─ 内置余额定时器：北京时间每天 10:00
  └─ 内置配额定时器：每天 / 每周一 / 每月1日 00:00
        |
        ├─ New API PostgreSQL：只读查询；受限函数仅补建缺失 PAT
        ├─ 独立监控 PostgreSQL：预算、归档、报警、规则与操作记录
        ├─ New API 管理 API：用户、KEY 和配额修改
        └─ 飞书 / 钉钉 / SMTP 邮件：独立启停、报警分发
```

两个数据库可以位于同一 PostgreSQL 实例，但必须分库配置。
余额和配额调度由应用内线程运行，使用 PostgreSQL 领导锁协调多个进程。所有时间边界使用北京时间。
四个页面为 `/cockpit/statistics/`、`/cockpit/users/`、`/cockpit/keys/`、`/cockpit/operations/`，共用顶栏与侧边栏。
业务接口统一为 `/cockpit/api/statistics/...`、`/cockpit/api/users/...`、`/cockpit/api/keys/...`、`/cockpit/api/operations`，
页面与外部程序调用同一套接口和权限，不另建一份外部管理逻辑。
部署参数见[部署](deployment.md)，视觉规范见[界面](ui.md)。

## 启动与调度

- `runtime.py` 在启动网页前以事务和数据库锁应用监控迁移，失败则退出；正常 HTTP 请求不执行 DDL。
- Gunicorn 子进程初始化后，`timers.py` 启动余额与配额线程；模块导入、HTTP 请求和 fork 前不启动调度连接。
- 两种定时器各自竞争 PostgreSQL 会话领导锁，同一监控库每种任务只有一个领导者。连接丢失后待命进程可接管。
- 余额启动只安排未来的 10:00，不立即报警；定时配额允许 5 分钟的启动窗口，错过周期不补发。
- 退出时停止领取任务和发起下一组请求，等待当前组保存结果；不自动续跑或重试结果不明确的请求。
- `/healthz` 核对线程与两种领导锁；配置监控库但未就绪返回 `503`。探活不计算费用、不发送报警、不修改额度。
- 未配置监控库时只提供查询，不启动定时器；用户、KEY、配额写操作必须有监控库审计。

## 模块与数据流

| 模块 | 职责 |
|---|---|
| `app.py`、`auth.py` | 四个页面及 API、输入边界、管理员身份校验 |
| `runtime.py`、`gunicorn_conf.py`、`timers.py`、`quota_timer.py` | 启动迁移、进程生命周期、两种内置调度与健康检查 |
| `report.py`、`usage.sql`、`metadata_fallback.py` | 只读用量聚合、异常日志兼容、排名与 Excel |
| `historical_prices.py`、`expression_prices.py` | 还原请求价格、安全解析表达式、匹配当前档位 |
| `report_snapshots.py` | 按管理员与账本隔离、限时保留的不可变展示详情 |
| `balance.py`、`scopes.py`、`catalog_sync.py` | 账本、渠道库存、月度逐渠道费用、余额与报警 |
| `quota.py`、`quota_schedule.py`、`quota_schedule_executor.py` | 手工额度增减、定时规则与逐用户执行 |
| `user_management.py` | 用户/KEY 查询、受限 PAT 补建、官方管理接口与批量改组 |
| `operation_records.py` | 请求发送前审计、规则审计、统一操作记录及游标分页 |
| `notifications.py`、`notification_channels/` | 全局分渠道开关、凭据加密、飞书/钉钉/邮件发送与独立结果 |
| `static/`、`templates/` | 共享界面与各页面交互 |

用量报表保留日志金额，不按当前模型价格重计费；公式详情读取查询时的快照，Excel 独立实时查询。
月度费用按“月份 + 渠道 ID”保存，查询时按渠道当前归属汇总；改标签不重拉历史日志，不重建分组费用副本。
已归档月份取监控库，本月每次实时计算。每日多账本检查只在本次调用中共享一份本月汇总，不跨调用缓存。
实际操作最多 5 个并发一组，当前组结果全部保存后才发下一组；预览与未发起名单不进入操作记录。
具体业务规则见[统计口径](calculation.md)、[余额监控](monitoring.md)、[用户与令牌](users.md)、[配额](quota.md)。

## 数据库职责

New API 查询字段见[兼容范围](compatibility.md)。普通查询使用只读事务；
唯一直接写入例外是源库受限 PAT 补建函数，由 `users` 表所有者单独安装。
用户资料、密码、KEY、分组与额度均经官方管理 API 修改，不通过 SQL 更新。

监控库中的活动表：

| 表或表组 | 用途 |
|---|---|
| `schema_migrations` | 已应用的迁移文件名 |
| `balance_scopes`、`balance_channel_inventory`、`channel_catalog_sync_state` | 账本、渠道当前名称/归属与完整发现标记 |
| `balance_settings`、`balance_settings_audit` | 预算、阈值、起始月份、开关、版本与审计 |
| `balance_channel_archive_months`、`balance_month_channels`、`balance_months` | 归档月份、逐渠道原始费用与完整性校验总额 |
| `balance_state`、`balance_alerts`、`balance_daily_runs` | 最近检查结果、当前报警、每日运行记录 |
| `notification_settings`、`notification_feishu_settings`、`notification_dingtalk_webhook_settings`、`notification_email_settings` | 每个通知渠道的开关、版本、发送状态与独立加密凭据 |
| `quota_schedule_rules`、`quota_schedule_rule_groups` | 定时规则与用户组 |
| `quota_schedule_runs`、`quota_schedule_run_items`、`quota_schedule_run_targets` | 周期快照、已发起结果与内部待执行名单 |
| `user_management_operations`、`user_management_operation_items`、`user_management_operation_targets` | 实际管理员、已发起请求审计、预览冻结名单 |
| `report_snapshots`、`report_snapshot_rows` | 展示公式快照，非账单或余额缓存 |

迁移目录见[数据库初始化与迁移](deployment.md#数据库初始化与迁移)。迁移文件是升级链，不能删除或重编号。
应用只使用上表列出的活动结构；已有库的其他表和数据不因应用升级被自动清空。

## 认证与审计

网页登录复用 New API 会话；`/cockpit/login` 和静态资源公开，业务页面与会话 API 请求每次经官方 `/api/user/self` 验证会话及管理员身份。
浏览器在官方路径刷新 HttpOnly Cookie，再将短期访问凭据交给 Cockpit 保存为 `/cockpit` 范围的 HttpOnly Cookie；会话由 New API 管理。
业务 API 同时接受管理员 PAT Bearer，每次从源库读取其 ID、用户名及角色并验证启禁状态；
余额与报警接口仅接受 PAT。PAT 不能换取网页 Cookie，不能使用客户端指定的身份执行或记录操作。
敏感写请求检查同源 JSON 与对应请求头；前端不自动重发写请求，账号切换时阻止旧页面继续提交。详见[登录与会话](authentication.md)。
PAT 每次从源库读取，不放在浏览器、配置或监控库；缺失时才经受限函数补建。
通知 Secret 用独立密钥加密，密钥与监控库均需备份。
完整 KEY 仅在明确查看后显示，窗口关闭后清除，不进入 URL 或持久缓存。
普通管理员仅查看自身操作，超级管理员可查看全部；统一记录和逐条详情采用同一权限边界。
不保存密码、PAT、完整 KEY 或远端原始响应；未曾保存的操作不补造记录。
记录显示目标用户的用户名与 ID；批量目标按用户去重，定时规则显示规则 ID 和目标用户组。
操作记录保留目标快照，缺少用户名时批量解析，不重写已保存记录，不为展示生成 PAT。
