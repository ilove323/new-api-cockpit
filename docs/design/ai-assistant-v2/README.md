# AI 助手扩展：多协议、联网、自动记忆与长上下文

> 当前源码的扩展契约。保留用户隔离、完整历史和独立生图模块，不新增聊天数据库。使用与配置见[AI 助手](../../chat.md)，开发检查见[贡献指南](../../../CONTRIBUTING.md#ai-助手离线回归)；离线通过不代表真实渠道或计费验收。

## 已确认范围

> 当前界面行为：Skills 使用专用窗口并持续载入；KEY/模型/模式改为当前账号浏览器缓存的全局选择，不随聊天切换重置。移除候选数、上下文策略、联网和自动 Memory 四个控件。页面固定单回答、自适应上下文，自动 Memory 默认开启；文件与联网模式默认提供受控联网工具。旧多候选/API 参数继续兼容。以下多候选仍是后端兼容能力，不代表当前页面继续提供数量选择，详见[前端契约](ui.md)。

| 编号 | 要求 | 交付与验收 |
|---|---|---|
| P01 | Responses API | 固定 New API 上游 `/v1/responses`；文本、工具调用、用量、取消、异常、无重试闭环 |
| P02 | Anthropic 原生协议 | `/v1/messages`、原生工具结果、thinking/signature/redacted_thinking 原样衔接 |
| P03 | 专用 reasoning 工具衔接 | Chat 的 reasoning_content、Responses 的 reasoning items、Anthropic 的 thinking blocks 按原协议保存与重放；不跨模型/令牌复用不透明状态 |
| P04 | reasoning 保存与展示 | 只保存并展示上游实际公开返回的文本；区分原文、摘要、不可读的加密连续状态；不伪造/解密隐藏推理 |
| P05 | 普通聊天多候选 | 用户已确认：普通聊天可多候选，Agent 仅一个执行分支；候选独立展示，选定回答用于后续上下文，计费与请求数可核对 |
| N01 | 网络搜索 | 服务端配置搜索后端，Agent 提供并执行搜索工具；不是要求用户额外部署模型 |
| N02 | HTTP 抓取 | 公网 HTTP(S) GET、正文提取、来源 URL；拒绝内网/元数据地址/凭据/环境代理/重定向绕过，受总时间与大小限制 |
| N03 | URL 下载 | 下载到本人当前聊天工作区，不接受本地目标路径、不执行内容；当前文件解析范围仍为受支持的 UTF-8 文本 |
| M01 | 自动提取长期记忆 | 当前页面默认启用；从当前聊天中用户明确表述的长期事实/偏好提取，不把网页、工具指令或模型猜测当成用户事实 |
| M02 | 自动保存和更新 MEMORY | 有界、可追溯，保留手工内容；版本冲突不覆盖，不自动修改 SOUL/IDENTITY；本人可查看并撤销自动更新 |
| C01 | 自动上下文摘要 | 到阈值前压缩较早历史，保留近期完整轮次、当前任务、个人设置和工具调用配对；不覆盖原始历史 |
| C02 | 完整历史与按需原文 | 放得下即使用完整可重放对话；放不下用已覆盖旧历史的摘要与近期原文，Agent 可检索本人当前聊天原文；不得静默仅取最近几轮冒充全量 |

## 核心决策

1. **存档与模型上下文分开。** JSONL/文件保存完整 UI 历史、候选、工具、公开 reasoning 与运行状态；模型输入只是一个明确记录来源的投影。失败/中断的用户输入和安全的部分回答可保留语义，但不重放残缺工具调用。
2. **一个有限 Agent 循环，三个协议适配器。** 固定上游地址、本人 KEY、鉴权、落盘、取消、总预算统一；协议层转换消息、工具 schema、结果、thinking 与 SSE。不能把 Chat 的 `messages/tool` 直接换个 URL 发给 Responses 或 Anthropic。
3. **普通多候选、Agent 单分支。** Chat Completions 可使用其原生 `n`；不支持原生 `n` 的协议由一个用户运行内有界多次调用实现，UI 明确额外调用数。不能隐藏费用或让候选重复执行有副作用工具。
4. **公开文本与连续状态分开。** reasoning_content / thinking / reasoning summary 按实际来源标注；签名和 encrypted_content 作为鉴权的协议状态，不在 UI 假装成思考原文。原生状态只重放到兼容的原协议、模型、本人令牌。
5. **长上下文采用 Codex 式工作状态管理。** 用户明确更倾向 Codex：完整存档与工作上下文分离，尽量完整、达到阈值前自动压缩并继续。对明确支持 `/responses/compact` 的 Responses 渠道，优先原生压缩，保存并原样重放接口返回的整个窗口，不从中任意抽取一条 encrypted_content。压缩状态绑定兼容的协议、模型、本人令牌与能力版本，不能跨模型/协议硬塞。未提供原生压缩的渠道明确采用受控可读摘要，不在原生请求失败后偷偷切换并重复收费。可读摘要标记模型生成并保存覆盖范围/源哈希。历史不删除、不静默丢弃。超长旧历史超出单轮压缩预算时明确失败，不无限收费。
6. **Memory 不是聊天摘要。** 摘要属于当前聊天；长期记忆属于本人个人设置。自动写入使用可核对的结构化变更和版本比较，手工编辑优先；旧运行快照不被回写。
7. **联网执行不携带业务凭据。** 网页请求不转发 Cookie/KEY/认证头；DNS 全部解析地址检查后固定数值地址连接，TLS 校验原主机，逐跳验证重定向。只有受信任、运维配置的搜索后端可以使用独立搜索凭据。
8. **辅助调用受同一运行约束。** 摘要和自动记忆均使用当前用户明确选择且有权限的模型/KEY；记录 purpose 与真实 usage，独立限制辅助次数，同时受总截止、停止和存储预算限制。刷新、读历史和重启不触发收费辅助任务。
9. **兼容已有历史。** 新代码读取已有 v1 事件；新增字段、事件和协议状态有严格版本/白名单/大小校验。旧代码不能读取新增事件，升级后的降级必须先停写并使用兼容版本或恢复升级前备份，不能盲目回滚镜像。

## 外部实现依据与边界

- [Codex compaction](https://developers.openai.com/cookbook/examples/gpt-5/codex_prompting_guide#compaction)：可将较长上下文压缩后继续；不是无限全量重发。
- [Responses compaction](https://developers.openai.com/api/docs/guides/compaction)：原生压缩状态是不可读的连续状态；网关是否支持该接口需独立确认。本项目跨三协议的可读摘要不假设网关一定支持 `/responses/compact`。
- [OpenAI function calling](https://developers.openai.com/api/docs/guides/function-calling)、[reasoning continuity](https://developers.openai.com/api/docs/guides/reasoning#keeping-reasoning-items-in-context)：工具由应用执行，相关 reasoning/function items 需要一起衔接。
- [Anthropic thinking](https://platform.claude.com/docs/en/build-with-claude/thinking)、[streaming](https://platform.claude.com/docs/en/build-with-claude/streaming)：完整 thinking/redacted blocks 与签名不可在工具连续调用时丢失或改写；可见文本不一定是完整原始思考。
- [LibreChat summarization](https://www.librechat.ai/docs/configuration/librechat_yaml/object_structure/summarization)：阈值、保留近期原文、滚动摘要与工具结果投影；完整存档保持不变。
- [LibreChat memory](https://www.librechat.ai/docs/features/memory)：自动记忆提取与会话摘要是两个不同机制，不因采用其思路而引入 MongoDB 或其运行时。

这些是一手文档依据，不是本项目已经完成的真实上游生成验收。离线测试使用合成 KEY 与上游响应，不应访问真实用户数据或发起付费请求。

## 分阶段验收

1. 三协议/公开 reasoning/签名连续性/候选的离线真实 HTTP 与碎片 SSE 测试；取消、坏流、超限、异常不重试。
2. 旧历史恢复、用户/聊天隔离、候选选择和上下文来源、辅助计费与幂等测试。
3. SSRF、DNS rebinding、重定向、超时、过大正文、HTML 清理、文本下载落盘和跨用户拒绝测试。
4. 自动记忆提取、保留手工内容、版本冲突、撤销、凭据拒绝与压缩覆盖完整性测试。
5. 前端候选/思考/联网/记忆与摘要状态、刷新完整历史；全量回归和生图保护检查。
6. wheel/镜像资源与隔离执行验证；真实渠道兼容、搜索服务配置和正式部署另行验收，不用模拟通过冒充上线成功。

## 实现契约索引

- [HTTP/事件/存储与预算](contracts.md)
- [联网工具与安全边界](network.md)
- [自动 Memory 与撤销](memory.md)
- [Codex 式工作上下文](context.md)
- [前端交互与费用提示](ui.md)
- [开发与离线验证](../../../CONTRIBUTING.md#ai-助手离线回归)

真实渠道、搜索后端和版本部署需由使用方按实际配置验证；当前前端固定单回答，后端保留历史多候选兼容。
