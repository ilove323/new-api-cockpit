# v2 运行、HTTP 与持久化扩展契约

本页补充原有 [HTTP](../ai-assistant-v1/chat-api.md)、[存储](../ai-assistant-v1/chat-storage.md)和[运行](../ai-assistant-v1/chat-runtime.md)规范。既有 owner/session/stamp/同源、分页高水位、单写者锁和幂等规则不变；不开放 PAT、管理员代查或任意 provider JSON。

## 1. 运行请求

`POST /cockpit/api/chat/conversations/{cid}/runs` 保留原必填字段，新增可选字段：

> 最新页面默认显式提交 `candidate_count:1`、`context_strategy:"adaptive"`、`auto_memory:true`，文件与联网模式提交 `web_enabled:true`（服从站点能力限制），不再提供四个选择控件。下列 API 缺省值保持兼容，不等同于当前页面默认行为；旧多候选记录仍可选择后续分支。见[前端契约](ui.md)。

| 字段 | 类型/默认 | 硬限制 |
|---|---|---|
| `candidate_count` | int，1 | 1—4；`mode=files` 只能 1 |
| `web_enabled` | bool，false | 只能在 files Agent 模式为 true |
| `auto_memory` | bool，false | 请求为 true 时启用，正常回答之后最多一次提取；当前页面默认提交 true |
| `context_strategy` | recent/adaptive，recent | 新 UI 显式发送 adaptive；旧调用不带字段保持 recent 行为 |

未知字段、bool 代替整数、跨模式参数均拒绝。幂等摘要纳入全部非默认扩展选项；显式默认值与原请求等价，不因前端升级破坏旧请求 ID 重放。

普通 Chat 的多候选使用原生 n；Responses/Anthropic 在同一 run 中顺序执行最多四次单候选调用。候选各有独立 `message_id/candidate_index`，不是四条用户问题，不执行四次 Agent 文件副作用。

## 2. 新增本人 HTTP 操作

### 选择候选

`POST /conversations/{cid}/runs/{rid}/candidate`

```json
{"expected_revision": 15, "candidate_index": 1}
```

成功返回 `{"run": RunSummary, "conversation": ConversationSummary}`。只能选择最新已完成多候选轮次中实际完成的回答，且无在途/后续运行；新轮次接受后不可改旧选择。候选序号从 0 开始，UI 从 1 显示。未选上一轮分支时，新提交 409 `CHAT_CANDIDATE_REQUIRED`。版本冲突不自动重试。

### 撤销自动 Memory

`POST /conversations/{cid}/runs/{rid}/memory/undo`

```json
{"context_revision": 3, "expected_revision": 19}
```

第一个是当前个人设置版本，第二个是当前聊天版本，不能拿冻结快照版本替代。
审计从服务端本人聊天事件读取，客户端不传 before/after 正文。仅当当前 MEMORY 仍等于审计 after 的正文、哈希和版本时撤销；后续手工/自动修改、重复撤销或其他账号均拒绝。
成功返回 `{"context": 当前UserContext, "conversation": ConversationSummary}`，不触发模型。

两操作与其他聊天写入一样要求本人会话、`X-Cockpit-Session`、`X-Cockpit-Chat` 与同源校验；随机 UUID 不是授权。普通/超级管理员同样只有本人权限。

## 3. 扩展事件（v=1 兼容读取，严格白名单）

| type | 新增/可选 data |
|---|---|
| `run.accepted` | 可选 `protocol`；可选 `options`，只记录非默认的四个运行扩展选项 |
| `run.dispatching` | 可选 `purpose=main/summary/native_compact/memory`（缺省 main）、`candidate_index`、`candidate_message_ids` |
| `assistant.delta/message` | 可选 `candidate_index`，其他原字段不变 |
| `assistant.reasoning` | `message_id,text,kind=raw/summary,field=reasoning_content/reasoning/thinking`；可选 candidate_index |
| `assistant.protocol` | `message_id,protocol,state`；可选 candidate_index。state 按协议专门白名单验证，不是任意上游响应 |
| `candidate.selected` | `candidate_index`；可在该轮终态之后出现，不恢复运行 |
| `run.usage` | 原 usage 三整数及 request_index，可选 purpose，与请求派发去重对应 |
| `run.notice` | `code,message`；有界安全信息，不带异常局部变量、凭据或上游原始错误正文 |
| `context.compacted` | `mode=summary/native,covered_run_ids,source_sha256,checkpoint`；新生成窗口/检查点 |
| `context.selected` | 同上；复用已有覆盖检查点，不产生辅助模型调用 |
| `memory.proposed` | `intent`；写个人 manifest 前提交的完整变更意向，不是完成确认 |
| `memory.updated` | `audit`；包含本人、来源、fact IDs、前后 MEMORY 正文/哈希/版本，精确结构见 [Memory](memory.md) |
| `memory.reverted` | `after_sha256,context_revision,memory_revision`；可在终态之后出现，不能覆盖后续个人修改 |

run summary 新增 `candidate_count,selected_candidate,main_model_requests,aux_model_requests`；发生自动写入/撤销后有相应布尔状态。
公开推理只显示实际返回文本/摘要，UI 不投影 opaque state 为内容。原生状态与所在 accepted 的协议一致；跨轮还核对模型/token ID/能力版本。

`run.dispatching` 在网络前持久化，是**可能已派发**的保守不确定边界，不是付费成功回执；DNS/连接失败或在边界处取消时，不能凭该事件断言确实收费。
`model_requests` 计全部主/辅助派发，主/辅助分别最多 6/4，合计最多 10。usage 只按上游实际明确返回的数值求和；完整标记须每次派发均有 usage，不把未知当 0。真实费用以 New API 为准。

## 4. 工作上下文不可变文件

`contexts/<rid>.json` 仍是个人设置/Skill 冻结快照，不被摘要或 Memory 更新回写。
新增 `contexts/<uuid>.working.json`，引用为：

```json
{"file":"contexts/11111111-1111-4111-8111-111111111111.working.json","sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","size_bytes":1024}
```

引用由服务端生成，不能当外部下载路径。文件包装包含 v、instance、owner、cid、来源 rid 和整个 checkpoint，最多 4 MiB；底层规范化 JSON、大小、归属与 SHA256 校验后才能读，必须由已提交 `context.compacted` 事件引用。
写文件后才提交引用，未提交文件重启后隔离，不冒充检查点；原始 JSONL 历史不压缩、不覆盖、不截断。

可复用历史检查点包含 version、mode、四项 binding、covered_run_ids、源哈希与自身哈希，以及 summary 或完整 native window。
当前运行内压缩的窗口可以留审计副本，但标 `active_only=true`，不冒充跨轮覆盖证明、不参与未来检查点选择。
原生输出窗口整体保留消息/推理/工具/compaction 项及顺序；不能只抽取其中一个 encrypted item。

算法与源覆盖校验见[上下文](context.md)。必留近期/当前内容或待压缩前缀超限直接报错；不静默裁减，不在失败后换摘要重收费，不在读取、重启或恢复期间派发模型。

## 5. Memory 提交与崩溃边界

提取仅限本轮真实 user 消息，排除网页/助手/工具；模型提案先严格校验、来源逐字匹配、手工区保留、已知凭据拒绝，再持久化意向。
写永久 Memory 必须调用现有 UserContext 双版本 CAS 和存储/锁/配额，不能由文件工具定位个人路径。完成后审计必须匹配意向的 before/after 哈希与来源。

事件日志和个人 manifest 是两个权威文件，不能声称跨文件事务完全原子。若崩溃发生在 manifest 提交后、完成审计前，意向保留，可核对个人版本/哈希；未确认意向不自动执行、重放或收费，UI 不冒充成功。只有已确认审计提供一键版本保护撤销。

## 6. 运行限制与验收

常量在 `features/chat/config.py`；网络独有参数在 `web_tools.py` 的显式限制表。主循环和所有辅助步骤共用整轮 180 秒截止、停止、owner/KEY 复核与空间预留。

- 主/辅助/总请求：6 / 4 / 10；自动压缩最多 3；Agent 工具最多 16。
- 候选最多 4，工具 schema 最多 8，配置输入最多 2 MiB，模型请求体最多 4 MiB，配置输出最多 32768 Token。
- 公开 reasoning 合计最多 256 KiB；单个 replay DTO 最多 512 KiB；可读摘要最多 16 KiB。
- 公网工具只允许受控 GET，15 秒、2 MiB 响应、32 KiB 返回、64 KiB 下载、3 次重定向；完整规则见[联网](network.md)。
- 本地真实 socket、跨进程/临时文件、合成模型和真实浏览器分别验证组件、集成及界面，不证明真实渠道计费兼容或生产部署完成；验证入口见[贡献指南](../../../CONTRIBUTING.md#ai-助手离线回归)。

旧程序可能不认识新增事件/字段。升级与降级先停写、一致备份，使用兼容二进制或恢复升级前数据；不能盲目换回旧镜像继续写扩展日志。

补充资源边界：adaptive 来源覆盖最多 16384 轮且 UUID 元信息最多 640 KiB，与 recent 选择上限 1024 轮分开；超出明确 413，保留原始历史且不派发收费调用。达到三次主动压缩后，若当前窗口仍在硬输入预算内则继续主调用并给出提示；只有硬预算不够才停止，不能因75%主动阈值误终止本可继续的运行。
