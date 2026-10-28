# 架构

> 当前开发分支包含默认关闭的 AI 助手，并将聊天/生图业务分别归档到独立功能目录。
> [聊天开发说明](chat-internals.md)与[配置说明](chat.md)保留完整边界；本地实现/离线验证不代表已发布或部署。

## 运行拓扑

```text
浏览器
        |
   同源 HTTPS 入口（Nginx 等反向代理）
        |
statistics：网页与调度容器
  ├─ Gunicorn：网页与 API
  ├─ 内置余额定时器：北京时间每天 10:00
  └─ 内置配额定时器：每天 / 每周一 / 每月1日 00:00
        |
        ├─ New API PostgreSQL：只读查询；受限函数仅补建缺失 PAT
        ├─ 独立监控 PostgreSQL：预算、归档、报警、规则与操作记录
        ├─ New API 管理 API：用户、KEY 和配额修改
        ├─ New API 模型 API：同步测试生成；结果只返回浏览器
        └─ 飞书 / 钉钉 / SMTP 邮件：独立启停、报警分发

同源入口的两条图片付费端点
        |
image-studio：独立同步请求池
  ├─ 监控 PostgreSQL：跨进程并发锁，不新增工作站表
  ├─ image-studio-receipts：隔离 Redis，短期派发回执
  └─ New API 图片 API：本人 KEY；结果只返回浏览器

可选 /cockpit/api/chat/ 前缀
        |
chat：独立有限运行池（默认关闭）
  ├─ 本机绑定 ./data/chat：每用户/每聊天事件、文件、快照
  ├─ 每用户个人 MEMORY/SOUL/IDENTITY 与文本 Skill
  └─ New API 三协议：本人已有 KEY；无新的数据库/Redis
```

两个数据库可以位于同一 PostgreSQL 实例，但必须分库配置。
余额和配额调度由应用内线程运行，使用 PostgreSQL 领导锁协调多个进程。统计/调度时间边界使用北京时间；聊天事件时间为UTC。
`/cockpit/` 下包含用量统计、渠道质量、智力测试、生图工作站、用户管理、令牌管理、操作记录、API 文档和模型使用，
共用顶栏与侧边栏；完整页面地址见 [README](../README.md#功能)。
业务接口统一为 `/cockpit/api/` 下的 `statistics`、`quality`、`intelligence`、`image-studio`、`chat`、`users`、`keys`、`operations`、`manual` 模块，
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
- `COCKPIT_PROCESS_ROLE=image` 不执行迁移或调度，仅处理图片生成和编辑；探活检查并发锁和持久回执，不发送模型请求。
- `COCKPIT_PROCESS_ROLE=chat` 不执行迁移或调度，仅处理显式聊天API及自身探活；网页仍由web提供。SIGTERM取消有界在途运行，不自动恢复收费请求；未知role启动失败。

## 模块与数据流

| 模块 | 职责 |
|---|---|
| `app.py`、`auth.py` | 页面及 API、输入边界、官方会话校验和默认管理员权限；普通用户入口显式列举 |
| `openapi.py`、`static/api-docs*.js` | 显式接口契约、请求示例、单次调试与操作确认；不替代后端授权 |
| `runtime.py`、`gunicorn_conf.py`、`timers.py`、`quota_timer.py` | 启动迁移、进程生命周期、两种内置调度与健康检查 |
| `quality.py`、`quality.sql`、`static/quality.js` | 只读渠道尝试去重、当前标签对比、结果分类、性能健康、耗时分位数和历史价格查询 |
| `intelligence.py`、`intelligence_stream.py`、`static/intelligence.js`、`static/intelligence-preview.js` | 流式鹈鹕骑车 SVG 测试、本人专用 KEY 复用与自适应沙箱预览，不保存结果 |
| `manual.py`、`static/manual*.js` | 本人令牌和模型查询、十二类工具的详细接入教程、脱敏预览及显式含 KEY 复制，不生成内容或保存凭据 |
| `features/account_mcp/` | 无状态 MCP HTTP；New API 校验本人 PAT 与权限，复用统计 SQL 并在源查询强制 user_id，仅提供本人余额和用量 |
| `integrations/new_api/`、`platform/` | 注入式本人令牌、模型限制/目录、源库只读连接和已验证身份契约；旧personal_tokens是兼容别名 |
| `features/image_studio/{service,contracts,source_routes,capacity,http}.py` | 生图独立业务目录与Blueprint、版本化路由契约、输入角色编译、权限复核及付费派发；旧模块兼容别名 |
| `features/image_studio/{transport,results,receipts}.py` | 单次有界请求、安全图片结果解析和Redis去重回执，原协议/锁号保持不变 |
| `features/chat/` | 用户隔离的JSONL/commit存储、三协议有限循环、受控文件/联网工具、公开 reasoning/候选、工作检查点、个人文档/Skill及自动 Memory 审计；不引用其他业务模块 |
| `composition.py` | 宿主组合入口，给平级功能目录注入本人KEY/源库/登录等窄服务，不把业务逻辑搬回宿主 |
| `static/studio-domain.js`、`studio-request.js`、`studio-results.js` | 编辑规则、冻结提交、候选/版本及 Blob 生命周期 |
| `static/studio.js`、`studio-canvas.js` | DOM 状态投影、Konva 工具、图层导出与局部像素合成 |
| `frontend/studio/`、`static/studio-upstream.js` | 固定版本的画板与视图源码、标注擦除和图层合成适配，构建为本地静态资源 |
| `static/studio-lightbox.js` | 候选大图缩放、拖动、双指操作与独立对比，不修改主图 |
| `report.py`、`usage.sql`、`metadata_fallback.py` | 按请求价格和日志倍率拆行，原始用量聚合、异常日志兼容、排名与 Excel |
| `channel_flow.py`、`static/channel-flow.js` | 第四个排名标签按用户拆分全部启用渠道的历史消费，只读聚合、按需查询，不读取 KEY |
| `historical_prices.py`、`expression_prices.py` | 还原请求价格、安全解析表达式、匹配当前档位 |
| `report_snapshots.py` | 按管理员与账本隔离、限时保留的不可变展示详情 |
| `balance.py`、`scopes.py`、`catalog_sync.py` | 账本、渠道库存、月度逐渠道费用、余额与报警 |
| `quota.py`、`quota_schedule.py`、`quota_schedule_executor.py` | 手工额度增减、定时规则与逐用户执行 |
| `user_management.py` | 管理员用户/KEY 管理、受限 PAT 补建、批量改组；普通用户测试 KEY 的本人会话调用与审计 |
| `operation_records.py` | 请求发送前审计、规则审计、统一操作记录及游标分页 |
| `notifications.py`、`notification_channels/` | 全局分渠道开关、凭据加密、飞书/钉钉/邮件发送与独立结果 |
| `static/`、`templates/` | 共享界面与各页面交互 |

用量报表的 Token 和金额均来自日志，分组倍率使用请求当时的记录；不同价格或倍率分别成行。
当前价格只用于档位匹配，不改变历史用量。公式详情读取查询时的快照，Excel 独立实时查询。
月度费用按“月份 + 渠道 ID”保存，查询时按渠道当前归属汇总；改标签不重拉历史日志，不重建分组费用副本。
已归档月份取监控库，本月每次实时计算。每日多账本检查只在本次调用中共享一份本月汇总，不跨调用缓存。
实际操作最多 5 个并发一组，当前组结果全部保存后才发下一组；预览与未发起名单不进入操作记录。

工作站的 GPT Image Playground 画板与视图工具直接编译，编辑器交互和 InvokeAI 图层合成通过本地适配层接入。
运行镜像不需要 Node 或 React；登录、本人 KEY 和请求派发不使用上游服务。固定版本和维护方式见[工作站源码](image-studio-sources.md)。
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
| `user_management_operations`、`user_management_operation_items`、`user_management_operation_targets` | 实际操作者、已发起请求审计、预览冻结名单 |
| `report_snapshots`、`report_snapshot_rows` | 展示公式快照，非账单或余额缓存 |

迁移目录见[数据库初始化与迁移](existing-installation.md#数据库初始化与迁移)。迁移文件是升级链，不能删除或重编号。
应用只使用上表列出的活动结构；已有库的其他表和数据不因应用升级被自动清空。

## 认证与审计

网页登录复用 New API 会话；`/cockpit/login` 和静态资源公开，每次会话请求经官方 `/api/user/self` 验证身份和账号状态。管理路由默认要求管理员；普通用户允许模型使用、辅助工具、登录后的插件下载、智力测试、生图工作站、启用后的AI助手及其本人接口、会话桥接和入口重定向，后端使用显式端点白名单。
浏览器在官方路径刷新 HttpOnly Cookie，再将短期访问凭据交给 Cockpit 保存为 `/cockpit` 范围的 HttpOnly Cookie；会话由 New API 管理。
聊天外的业务 API 同时接受管理员 PAT Bearer，每次从源库读取其 ID、用户名及角色并验证启禁状态；
余额与报警接口仅接受 PAT。PAT 不能换取网页 Cookie，不能使用客户端指定的身份执行或记录操作。
敏感写请求检查同源 JSON 与对应请求头；前端不自动重发写请求，账号切换时阻止旧页面继续提交。详见[登录与会话](authentication.md)。
管理员管理 KEY 使用所属用户 PAT，每次从源库读取，缺失才经受限函数补建；普通用户测试使用自己的会话，不补建 PAT。后端取出的 PAT 不返回浏览器，不写入配置或监控库。
文档调试可由管理员手动输入 PAT，仅留在页面内存；不会从数据库取出 PAT 展示给前端。

模型使用通过当前身份调用官方 `/api/token/` 和 `/api/token/:id/key`，上游及 Cockpit 都核对归属，不补建 PAT。模型查询用所选 KEY 调用 `/v1/models`，受令牌分组、模型和 IP 限制；不请求公开价格页。KEY 只短暂用于服务器查询和用户明确复制，不进入教程预览、URL、日志或存储，无新表和调度任务。
通知 Secret 用独立密钥加密，密钥与监控库均需备份。
完整 KEY 仅在明确查看后显示，窗口关闭后清除，不进入 URL 或持久缓存。
普通管理员仅查看自身操作，超级管理员可查看全部；统一记录和逐条详情采用同一权限边界。
不保存密码、PAT、完整 KEY 或远端原始响应；未曾保存的操作不补造记录。
记录显示目标用户的用户名与 ID；批量目标按用户去重，定时规则显示规则 ID 和目标用户组。
操作记录保留目标快照，缺少用户名时批量解析，不重写已保存记录，不为展示生成 PAT。

## 渠道质量分析

`/cockpit/quality/` 独立于用量和账本计算。正常查询在源库单个只读快照内聚合路由尝试、费用和分位数，
不加载账本切换控件或解析 `scope_id`，不按计费分组、档位或余额渠道排除名单限制结果。
默认查询只读取源库的当前启用渠道；接口选择全部历史渠道时，才读取监控库存补充已删除渠道的名称和标签。
当前渠道信息优先于库存。分析只用已有日志，不启动采样或调度任务，也不新增表。
性能健康复用相同只读快照：平均延迟从有效耗时合计和样本数计算，高频模型从完整渠道结果合并计数后排序，不平均各行比例或延迟。
HTTP 接口为 `/cockpit/api/quality/summary` 和 `/cockpit/api/quality/trends`。规则和边界见[渠道质量](quality.md)。

## 智力测试

`/cockpit/intelligence/` 发起流式生成，请求内完成模型目录核验、专用 KEY 准备和调用，生成部分最多 300 秒。
OpenAI/Claude SSE 在当前请求内解析为状态与文本，不转发思考原文；完成后才允许预览。非 SSE API 客户端收集同一次调用后返回 JSON。
当前用户在不同 Web 进程间通过 session advisory lock 串行使用其测试 KEY；不创建后台任务、线程池或新表。
生成结果只返回浏览器。预览把当前 HTML 同源 POST 到独立沙箱响应，不放在 URL、文件或服务端缓存里。
尺寸测量脚本是随包分发的固定资源，每个应用进程加载一次；生成内容不参与缓存。
只有 KEY 创建/配置沿用现有操作审计。管理员复用原 PAT 补建能力；普通用户使用自己的已验证会话调用官方令牌接口，不补建 PAT，不增加源库函数、写权限或表。
接口在 `/cockpit/api/intelligence/` 下，细节见[智力测试](intelligence.md)。


## 私有聊天与个人上下文

聊天核心不引用统计、配额、教程、智力测试或生图业务；通过宿主注入已验证身份、本人KEY和会话复核。生图采用同等平级边界，仍保留源库路由检查、监控库并发锁和隔离Redis回执。
聊天只支持单宿主机本地POSIX目录，owner从服务端用户ID导出，管理员没有跨用户入口；会话事件/文件按cid独立，个人设置显式在同一用户聊天间复用。
事件先fsync并发布commit高水位再显示；会话索引由本人目录对账，坏记录仍可见。跨进程flock、空间预留、幂等ID和黏性取消阻止并发重复派发；崩溃只能恢复记录为中断，不能重发模型。
个人文档与 Skill 采用版本 manifest，运行保存不可变快照；文件工具只作用于当前聊天，文件模式可启用受控公网工具。没有 shell 或代码执行；自动 Memory 仅由服务端受控提取与版本检查写入。
配置、限制及备份见[AI 助手](chat.md)，开发检查见[贡献指南](../CONTRIBUTING.md#ai-助手离线回归)。

## 文档与内置客户端安装包

文档分为模型使用、辅助工具、API 文档三个平级入口。辅助工具承接原账户 MCP 说明与 VS Code 插件安装、账户、KEY 和配置草稿教程；API 文档管理员权限不变。镜像单独用 Node 阶段打包 VSIX，运行层只保留安装包和校验清单；登录下载接口校验本地包，不在线拉取插件。详见[辅助工具](tools.md)。
