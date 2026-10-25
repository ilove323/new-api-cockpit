# AI 助手 v1 与生图模块边界设计

> 本目录保留聊天基础 HTTP、存储、运行及模块边界契约；多协议、联网、自动记忆、压缩和当前前端行为由[扩展契约](../ai-assistant-v2/README.md)补充。
> 功能配置见[AI 助手](../../chat.md)，开发检查见[贡献指南](../../../CONTRIBUTING.md#ai-助手离线回归)。站点部署和真实渠道验收不由文档状态推断。

## 1. 目标与确认范围

新增 `/cockpit/chat/`「AI 助手」页面，使用**当前用户自己的已有 KEY**，提供普通聊天和小型文件助手。
不自动创建 KEY，不自动改分组/额度，不使用管理员公共 KEY。
历史聊天、运行事件和文件存放在 Compose 所在目录的 `./data/chat`，绑定到容器；不新增聊天数据库、SQLite、Redis或向现有业务表写聊天内容。
提供本人的 Skill 列表/内容查看/显式载入，以及可管理的 `MEMORY.md`、`SOUL.md`、`IDENTITY.md`；将“初始输入”落实为可见、可编辑的个人身份与风格设置，不隐藏在代码里。

聊天与生图遵循相同的代码组织规则：**独立功能目录 + 明确的公共接口 + 不引用其他业务模块**。
保留现有项目、镜像和统一登录。现在不设计拆仓库、独立发行包、跨服务 RPC 或未来迁移流程。

### 两个不同的“独立”

- **独立请求池**：不同容器/工作进程承接慢请求，避免聊天、生图拖住统计和管理页面。
- **模块边界独立**：聊天、生图分别管理自己的业务规则和内部文件，只通过约定接口使用登录身份、本人 KEY、必要的 New API 信息。

前者不自动保证后者。当前生图已归档至独立功能目录，保留旧模块兼容别名；依赖方向和不可变项见[模块边界](module-boundaries.md)。
生图仍需要源库路由查询、监控库并发锁和专用 Redis，不是无数据库依赖的独立产品。

## 2. 必须满足的用户隔离与历史可见性

1. 所有访问以**服务端验证的 New API 用户 ID**为准；客户端不得传入 `user_id`、用户根目录或租户 ID 来决定权限。
2. 用户 A 无法枚举、读取、修改、删除、取消或下载用户 B 的聊天和文件。猜中 UUID 也无效；普通管理员、超级管理员使用聊天接口时同样只能看自己。
3. 一个用户的每个聊天有独立目录与文件作用域。某聊天的模型工具不能读取同用户其他聊天的文件；用户可自行下载后再上传。
4. 历史归属绑定稳定用户 ID，不绑定用户名、KEY、模型、浏览器、登录 session 或容器进程。
5. 同一用户重新登录、换 KEY、刷新、重建容器后，均能分页查看**全部未明确删除的聊天**。失败、中断、空聊天也必须出现，不能只列成功记录或第一页。
6. 列表缓存损坏可由目录和事件恢复；损坏的聊天显示“记录待修复”，不能静默消失。明确删除的会话不再展示；备份中的旧副本需要单独管理。
7. 站点文件和备份并非天然加密，宿主机管理员仍可能读取；应用层隔离不能冒充主机管理员也无法访问。

实现和验收必须覆盖“两用户 × 多聊天 × 多进程 × 重启”，不能仅用一个管理员账号证明隔离。

## 3. 功能边界（含已纳入本轮实施的扩展）

| 已有功能 / 本轮范围 | 仍不实现 |
|---|---|
| 新建、重命名、分页浏览、删除自己的聊天；流式 Markdown 回答、代码复制 | 分享链接、协作聊天、管理员代查他人聊天 |
| 按账号选择本人 KEY、模型；普通聊天 / 文件与联网模式 | 自动配 KEY、自动换组、自动付费探测模型 |
| 停止本轮、显示工具步骤、查看中断记录和实际返回的 Token 用量 | 断线自动重发、任务队列、脱离页面的长期后台任务 |
| 上传 UTF-8 文本/Markdown/CSV/JSON/代码，读取与检索，生成可下载的新文件 | PDF/Office/OCR/图片理解、运行代码、shell；联网执行仅限本轮受控公网工具 |
| 4 个工具：`list_files`、`read_file`、`search_files`、`write_file` | MCP、插件市场、运行时子代理、RAG/向量库 |
| 本人显式载入的文本Skill；本人可见可编辑的 MEMORY/SOUL/IDENTITY | 跨聊天检索、技能脚本执行/自动安装 |
| Chat Completions；本轮扩展 Responses / Anthropic / reasoning 连续调用 | 失败后偷偷切协议或隐式重试 |

多协议、公开 reasoning、历史多候选、受控联网、自动 Memory 与自适应压缩的当前行为以[扩展契约](../ai-assistant-v2/README.md)为准；代码实现和离线测试不等于真实渠道验收。

这是“聊天 + 私有文件工作区”，**不是可以在服务器执行命令的完整 Codex**。上传附件、模型输出都不执行。
图片工作站仍按原功能提供服务，不把它的历史或图片存储混入聊天目录。

## 4. 调研结论及采用边界

调研日期 2026-10-15；只参考公开的一手文档与代码，没有安装或运行第三方 harness。

| 项目 | 观察 | 本项目采用什么 |
|---|---|---|
| [DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness) | Developer Preview；安全说明明确尚不适合直接当作经过安全审计的生产沙箱。会话使用 JSONL，可选压缩；其他存储可选 JSON/SQLite，不等于“聊天必须进数据库” | 顺序事件日志、版本头、单写者/跨进程锁、崩溃恢复；不整套引入框架 |
| [Pi](https://github.com/earendil-works/pi) | 编码工具默认 read/bash/edit/write；会话 JSONL 使用事件关系维护历史 | 少工具、可读事件；本项目不照搬多用户服务器上的 bash |
| [Open WebUI 数据库文档](https://docs.openwebui.com/tutorials/maintenance/database/) | 默认 SQLite 保存账号、聊天、设置等；文件另存。挂载 data 不等于“不使用数据库” | 借鉴聊天交互，不引入第二套账号和聊天数据库 |
| [LibreChat 架构](https://www.librechat.ai/docs/development/architecture) | 完整多用户聊天产品，使用 MongoDB 等配套服务 | 借鉴历史与工具步骤展示，不嵌入整个服务体系 |

关键代码依据（固定提交，避免把未来变化当成本次结论）：

- DeepSeek `571902028a75c64c1d767f4ae5fd099485f0ddf0`：[持久化](https://github.com/deepseek-ai/deepseek-harness/blob/571902028a75c64c1d767f4ae5fd099485f0ddf0/docs/subsystems/persistence.md)、[JSONL 包](https://github.com/deepseek-ai/deepseek-harness/blob/571902028a75c64c1d767f4ae5fd099485f0ddf0/packages/session/session-persistence-jsonl/README.md)、[默认配置](https://github.com/deepseek-ai/deepseek-harness/blob/571902028a75c64c1d767f4ae5fd099485f0ddf0/packages/bundle/base/cordis.patch.yml)、[安全说明](https://github.com/deepseek-ai/deepseek-harness/blob/571902028a75c64c1d767f4ae5fd099485f0ddf0/SAFETY.md)。默认配置选择 JSON 存储；挂载 SQLite 查询插件不等于默认打开数据库。
- Pi `b45e0ebcc5ae2fc42a6f2f23104d10e656e6bedb`：[四工具](https://github.com/earendil-works/pi/blob/b45e0ebcc5ae2fc42a6f2f23104d10e656e6bedb/packages/coding-agent/src/core/tools/index.ts)、[会话格式](https://github.com/earendil-works/pi/blob/b45e0ebcc5ae2fc42a6f2f23104d10e656e6bedb/packages/coding-agent/docs/session-format.md)。本项目首版是线性聊天，不引入其会话分支树。
- [DeepSeek 官方工具调用说明](https://api-docs.deepseek.com/guides/tool_calls/)：模型提出调用，应用执行工具并回传结果；不需要为了四个工具引入通用代理框架。

## 5. 文档顺序与唯一权威位置

| 文档 | 内容 |
|---|---|
| [模块边界](module-boundaries.md) | 当前耦合、目标目录、依赖方向、公共契约、生图等价整理 |
| [HTTP 与界面契约](chat-api.md) | 完整路由、身份、请求/返回、错误、SSE、历史分页和界面状态 |
| [文件存储与恢复](chat-storage.md) | 目录、JSONL 格式、索引、锁、幂等、恢复、配额和删除 |
| [个人上下文与管理](user-context.md) | Skill查看/载入、MEMORY管理、SOUL/IDENTITY、版本与运行快照 |
| [运行循环与工具](chat-runtime.md) | 唯一限制表、模型能力、流式传输、工具 schema、停止、安全、部署增量 |
| [贡献与验证](../../../CONTRIBUTING.md#ai-助手离线回归) | 单元、跨进程、协议、前端和可选浏览器回归入口 |

字段定义以 API/存储文档为准；数值限制只在运行文档集中定义，其他文档引用其符号。
本文档示例中的用户、会话、模型均为虚构测试值；没有真实 KEY 或环境配置。

## 6. 维护约束

保持独立功能目录与注入式接口，不让聊天或生图导入统计、配额、教程等平级业务。整理目录不改变统计口径、审计、图片解码、路由契约或回执。
设计变化应同步契约与回归测试；真实渠道、计费、搜索服务与部署环境另行验收，不能用离线夹具通过代替。
