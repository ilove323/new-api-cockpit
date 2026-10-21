# 工作上下文 v2：纯规划器与运行时接入契约

本页对应 `src/new_api_cockpit/features/chat/working_context.py`。这是离线实现与集成契约，不表示真实渠道、付费调用或部署验收。规划器不读取文件、令牌、个人设置或其他聊天，不发起模型调用，不改写原始事件。原有 `context.build_messages` 保持不变。

## 1. 两类历史，两个策略

- **完整存档**：调用者授权后传入当前聊天的完整已提交事件前缀；UI 继续展示所有运行、候选、失败、工具与公开 reasoning。规划器不裁剪或回写存档。
- **模型输入**：只包含可安全重放的语义、完整工具配对及兼容的原生状态。原始个人快照不重放；只使用当前传入的个人快照。
- `request.context_strategy='recent'`（缺省）：从新到旧选择预算内完整轮次，在首个不合适的轮次处停止，保留旧数量上限；但候选选择、失败语义和原生绑定使用 v2 投影。这不是自适应摘要。
- `request.context_strategy='adaptive'`：不采用旧的“只留最近 N 轮”限制。预算阈值以下使用完整可重放语义；接近预算时压缩旧前缀，或复用覆盖可验证的检查点。不能静默跳过无法装下的旧历史。

预算是排序键、`ensure_ascii=False`、无多余空格、禁止 NaN 的 UTF-8 JSON 字节数，不是假称精确的 token 数。`policy.max_input_bytes` 默认 64 KiB，最大为服务端 `EXTENDED_CONTEXT_BYTES`。`input_bytes(messages)` 测量消息数组；存在独立原生窗口时测量 `{'messages': messages, '_window': window}`，包括整个窗口及重放 DTO，保守地计算重复的便携字段。

主动阈值为 `max_input_bytes * SUMMARY_TRIGGER_PERCENT // 100`，目前 75%。低于/等于阈值不触发收费辅助调用。高于阈值且有可压缩旧前缀时尝试压缩；没有旧前缀、必留近期轮次不可再拆分、或辅助输入装不下但原文仍在硬预算内时，继续使用可执行原文，不因优化而报错或偷偷丢弃。硬预算之外必须压缩成功或明确失败。

## 2. 导出的 Python 接口

```python
plan(history_events, snapshot, request, policy, checkpoint=None) -> dict
project_rounds(history_events, request, policy) -> list[dict]
history_rounds = project_rounds
finalize(planned, result) -> dict
plan_active(messages, policy, binding, *, window=None) -> dict
input_bytes(messages, window=None) -> int
history_schema() -> dict
search_history(history, query, *, offset=0, limit=10,
               case_sensitive=False, max_bytes=16384) -> dict
```

模块常量包括 `VERSION=1`、`MAX_COMPACTION_CALLS=3`、`SUMMARY_KEYS`、`SUMMARY_PREFIX`、`PARTIAL_PREFIX`。三次限制是给运行时的**每个运行总压缩次数上限**，不是规划器暗中执行三次请求。

### 输入约定

- `history_events`：同一聊天、按提交顺序排列、当前新运行 `run.accepted` **之前**的完整事件列表。父任务负责身份、聊天与文件归属校验；混入不同 `conversation_id` 的列表会被拒绝。无聊天 ID 的最小离线/v1 测试事件仍可投影。
- `snapshot`：原有 `personal-context-v1` 快照，沿用 `context.render_personal_block` 的校验。
- `request`：扁平的本轮请求；`text/attachment_ids/attachments` 与旧上下文接口一致；额外使用 `model/token_id/capability_revision/context_strategy`。
- `policy`：已选择并鉴权的能力策略，使用 `protocol/max_input_bytes/compaction`。协议缺省 `chat_completions`，压缩策略缺省 `summary`。
- `checkpoint`：父任务从本人的当前聊天存储中读取并验证引用的 JSON 对象；不是客户端可任意提供的跨聊天检查点。
- `binding`：恰好 `protocol,model,token_id,capability_revision` 四个键的字典。当前协议取自已确认的能力策略。缺少模型/令牌/能力身份的最小旧调用可进行普通文本投影，但不能重放不透明状态或创建检查点。

### `plan` 与 `finalize` 的同形返回值

| 字段 | 意义 |
| --- | --- |
| `version`, `strategy`, `binding`, `budget` | 规划版本、`recent/adaptive/active`、当前四项绑定、硬预算 |
| `messages` | 可执行的便携消息列表；**需要压缩时为 `None`，禁止直接调用主回答** |
| `window` | 完整 Responses 原生输入窗口或 `None`；与 `messages` 分开传递 |
| `included_run_ids` | 本轮语义所涉及的历史轮次；自适应包含摘要覆盖轮次与近期原文，recent 仅包含选中轮次 |
| `recent_messages` | 系统＋当前个人设置＋必留近期原文＋当前用户输入，不含待生成摘要/新窗口；active 则是必留的当前意图与最新完整交换 |
| `recent_run_ids` | 仍以原文重放的轮次；active 为 `[]` |
| `compact_required`, `compact_mode` | 是否必须先调用辅助模型，以及 `summary/native`；未触发时 mode 为 `None` |
| `compact_messages` | 可读摘要的一次完整辅助输入，其他模式为 `None` |
| `compact_input` | 原生压缩的一次完整 Responses `input` 数组，其他模式为 `None` |
| `compact_covered_run_ids`, `source_sha256` | 计划新覆盖或已复用的旧前缀及源哈希；无覆盖时为空数组及空覆盖的哈希 |
| `history_sha256` | `plan` 当前全部投影轮次的源哈希，便于跟踪；active 无此字段 |
| `checkpoint` | 可持久化的新检查点/实际复用的检查点，其他情况 `None` |
| `checkpoint_status` | `absent/ignored_recent/accepted/incompatible/stale/invalid/created/active_only`，描述验证/使用结果，不是鉴权结论 |
| `persist_checkpoint` | 历史规划为 `True`；active 为 `False`，不得作为以后按轮覆盖检查点复用 |

`finalize` 保留压缩输入和覆盖元数据用于核对，设置 `compact_required=False` 并产生可执行 `messages/window`。此时 `compact_mode` 仍保留实际模式；是否可执行只看 `compact_required` 与 `messages`。函数会复制输入，不修改传入计划、事件、快照、窗口或模型结果。

`project_rounds` 每个元素包含：`run_id/messages/complete/status/candidate_count/selected_candidate/source_sha256`。`complete` 表示经过工具配对和终止检查的成功完整轮次，不把一个仅标记 completed 但结构不完整的记录算作完整轮次。

## 3. 事件投影与原生状态

### 兼容事件

- `run.accepted`：使用 `text/attachment_ids`；可选 `options.candidate_count`；原生绑定字段正是 `protocol/model/token_id/capability_revision`。
- `assistant.message`：可选 `candidate_index`；使用 `message_id/text/tool_calls/complete/finish_reason`。
- `assistant.protocol`：`message_id/protocol/state`，可选 `candidate_index`。先按 message ID 与选中候选关联，再校验状态。
- `assistant.reasoning`：公开 reasoning 独立存档/展示，不因该事件生成额外助手答案。协议真正需要连续状态时，以经过校验的 `assistant.protocol` 重放。
- `candidate.selected`：`candidate_index`，可出现在 `run.terminal` **之后**。只把选中候选用于下一轮。最新成功运行是未选择的多候选时，`recent/adaptive` 都抛 `CHAT_CANDIDATE_REQUIRED`，即便后面存在失败运行也不能绕过。更老的未选多候选只保留用户语义。
- `file.added`：只投影附件的 ID、名称、大小摘要；不自动读取正文，不把路径或文件体放入提示。

失败、取消、中断，以及 malformed/incomplete 的轮次保留用户输入与安全的公开回答文本，并加上 `PARTIAL_PREFIX`，明确它不是已完成事实/工具成功证明。对同一消息优先使用 canonical `assistant.message`，仅在其不存在时合并已公开的 `assistant.delta`。这些部分轮次不携带工具调用、工具结果或 `_native`。失败多候选没有选中分支时，不擅自选择其中一个部分回答。

完整成功轮次沿用 v1 严格工具验证：调用 ID/工具名/参数一致、每个调用恰好有执行事件和结果、没有重复或悬空调用、完成理由与调用一致、最终回答已结束。任何错误均退化为上述有标记的便携语义，不发送坏工具协议。压缩保留的是整个轮次，不从中裁掉工具配对的一半。

仅当旧 accepted 绑定与当前四项绑定**完全一致**时，才能添加：

```python
message['_native'] = {'protocol': current_protocol, 'value': validated_state}
```

`state` 使用 `protocols.validate_state` 校验。Responses/Anthropic 的原生完整块还必须与便携的文本与工具调用一致且没有 incomplete 状态，避免原生替换抹去文本或引入额外工具。关联不到、候选不符、重复状态、格式不符或绑定改变时仅去掉状态，保留便携文本和完整工具配对。旧 accepted 缺少协议时绝不猜测模型名来复用状态。不会解密或虚构 reasoning。

## 4. 摘要压缩与检查点

### 可读摘要

保留至少最后 **2 个成功完整轮次**，以及它们之后所有部分轮次、当前用户输入、当前个人设置和系统规则。只有更早的连续前缀可被压缩。每次摘要输入包括 `prior_summary`、尚未覆盖的 `older_prefix` 便携原文及累计 `covered_run_ids`；不会把旧个人快照或加密状态塞进可读摘要。工具资料明确标为不可信来源。

模型必须返回严格 JSON 对象，**恰好**六个字段，每项是最多 128 个非空字符串组成的数组：

```json
{"goal":["当前目标"],"constraints":[],"decisions":[],"progress":[],"pending":[],"refs":[]}
```

至少一个数组非空；原始 JSON 文本和规范化对象均不得超过 **16 KiB UTF-8**。拒绝 Markdown 包裹、重复键、额外类别、非字符串、非法 Unicode、全空摘要及超限内容。`refs` 为模型生成的有界来源说明，不被当成外部事实已验证或新的授权。

调用方式：

```python
planned = working_context.plan(history, snapshot, request, policy, checkpoint)
if planned['compact_required'] and planned['compact_mode'] == 'summary':
    result = runner._generate(run, planned['compact_messages'],
                              purpose='summary', publish=False)[0]
    planned = working_context.finalize(planned, result)
```

`finalize` 可直接接受 `_generate` 的单个结果字典（其 `text` 为 JSON，`calls` 为空、`finish='stop'`），或严格 JSON 文本/六类别对象。摘要作为明确标记“模型生成工作摘要”的 **user 资料消息**插入当前个人设置之后，不升级为 system 指令。最终输入也必须在硬预算内。

### 原生压缩

只有显式 `policy.compaction='native'` 且当前协议为 `responses` 才允许。规划器用 `protocols.wire_body` 转换整个旧前缀与已有窗口，得到完整 `compact_input`；父任务以当前本人令牌/所选模型执行一次 `/v1/responses/compact`。输入数组自身必须不超过当前配置的输入预算；不能先发送一个已经超限的全历史去赌原生压缩成功。

`finalize(planned, output_list)` 或 `finalize(planned, {'output': output_list})` 使用 `protocols.validate_window` 校验**整个**返回数组，额外拒绝 incomplete 条目与悬空/重复工具配对，然后原样保留全部条目和顺序。绝不仅保留一个 encrypted item、抽取 hidden 内容或丢掉窗口里的用户消息。最终 `messages + window` 仍要通过完整 JSON 字节预算。

主调用将 `planned['window']` 传入内部 `_window`，由协议层在初始系统消息之后、当前个人设置与近期消息之前插入。非 Responses 不得携带 `_window`。原生失败/无效/过大不会自动降级可读摘要，也不会静默再收一次费。

### 检查点结构与覆盖验证

```python
{
    'version': 1,
    'mode': 'summary',  # 或 native
    'binding': {'protocol': ..., 'model': ..., 'token_id': ...,
                'capability_revision': ...},
    'covered_run_ids': [...],
    'source_sha256': '<64 hex>',
    'summary': {...},   # native 改为 window: 整个输入数组
    'checkpoint_sha256': '<64 hex>',
}
```

覆盖必须是当前事件投影中严格按顺序的连续旧前缀，且不得覆盖本次应保留的最近两个完整轮次。每个轮次源哈希覆盖原始相关事件（accepted/terminal、canonical/delta/reasoning/protocol、候选选择、工具调用与结果）及其引用的附件元数据。累计 `source_sha256` 是有序的 `{run_id,source_sha256}` 列表哈希。包括未选候选在内的源记录一旦改变，会使检查点失效；计费、通知、`context.compacted/context.selected` 等非语义事件不会使它自身反复失效。

`checkpoint_sha256` 覆盖检查点除自身外的所有字段；它是意外损坏检测，不是权限签名。必须由存储层独立验证本人/聊天/引用归属。绑定不兼容、覆盖/源哈希陈旧或检查点损坏时，重新从完整原始语义规划，不把旧摘要跨模型/令牌硬塞；无法在有限辅助预算内重新压缩就安全报错。已复用的检查点没有新旧前缀可压缩时，即便接近主动阈值也不重复收费压缩相同窗口。

父任务存储与事件契约无需增加高水位字段：

```python
reference = store.write_working_context(scope, rid, planned['checkpoint'], lease)
# reference = {file: 'contexts/<uuid>.working.json', sha256, size_bytes}
# context.compacted.data = {
#   mode, covered_run_ids, source_sha256, checkpoint: reference
# }
checkpoint = store.read_working_context(scope, reference)
```

父任务保证引用已经提交、文件/哈希/大小正确、`owner/cid/rid` 一致；持久化完整检查点，而非其中某条加密项。历史语义规划的有效结果才可用于以后轮次复用；`checkpoint=None` 时不写新的覆盖检查点。

## 5. 活动 Agent 的上下文增长

`plan_active(messages, policy, binding, window=...)` 用于父任务在**第二次及以后**的 Agent 模型调用前、工具结果全部解决后检查运行内增长。不要在第一次调用前立刻用它抹去历史规划保留的两个轮次。

- 先校验输入工具成组完整，再按同一 75% 阈值规划；不能把悬空工具送给下一次模型。
- 保留系统、当前个人设置、最新真实 user 意图，以及最新完整 assistant/tool 交换；较早完整交换可作为整体进入摘要/原生输入。
- 同形计划交给同一 `finalize`，但 `persist_checkpoint=False`、最终 `checkpoint=None`、状态 `active_only`。这是运行内工作窗口，不宣称拥有历史轮次覆盖证明。父任务若为审计保存活动窗口，必须另标 active-only，并排除以后按轮检查点复用。
- 原生旧窗口保持独立，整体参与输入预算和下一次原生压缩；不能把不可读窗口喂给可读摘要或静默丢弃。
- 已有窗口但没有新的旧前缀时，不重复压缩相同窗口。单个必留用户输入或最新完整工具交换超限时明确失败，不切断工具配对。

一次 `plan/plan_active` 最多请求**一次**完整旧前缀压缩；不执行自动递归分块。过大的旧历史装不下单次有界辅助请求则 `CHAT_CONTEXT_COMPACTION_LIMIT`，不截断前缀。父任务最多允许 `MAX_COMPACTION_CALLS=3` 次实际压缩（首次历史压缩与运行内压缩合计），同时服从既有辅助请求上限、总请求上限、截止时间、取消、存储配额与真实 usage 计费。达到次数限制时，仍在硬预算内的原文可直接运行；超硬预算则失败，不能为了继续而丢弃来源。

## 6. 当前聊天原文检索

`history_schema()` 返回全新的 OpenAI function schema：`search_chat_history`；参数只有 `query/offset/limit/case_sensitive`，`additionalProperties=False`，没有用户、聊天 ID、URL、路径或任意执行字段。

父任务路由工具时传入**当前用户当前聊天**已授权的事件，调用 `search_history`，不要允许模型替换数据源。字面子串匹配，不是正则。检索用户输入、助手 canonical/必要时公开 delta、公开 reasoning 与工具参数/结果；不返回 `assistant.protocol`、加密状态或旧个人快照。多候选在原文检索中可以被本人看到，结果保留候选索引，不能因此自动选为后续答案。

返回：

```python
{'ok': True,
 'matches': [{'event_id': actual_seq, 'event_index': zero_based_index,
              'run_id': ..., 'type': ..., 'excerpt': ..., 'candidate_index': ...}],
 'next_offset': next_source_event_index_or_None,
 'truncated': bool}
```

`candidate_index` 仅在源事件存在时出现；最小测试事件没有真实序号时 `event_id=None`，不伪造来源 ID。`offset/next_offset` 按源事件计数，不按命中数。最多 20 条、每条最多 500 字符片段，结果默认 16 KiB、上限为 `TOOL_RESULT_BYTES`；每页扫描受 `TOOL_SEARCH_SCAN_BYTES` 限制。过大单条/扫描无法推进时明确报错，而不是卡在同一游标或越过不可检索来源。

## 7. 失败与验证

主要公开错误：

| code | 含义 |
| --- | --- |
| `CHAT_CANDIDATE_REQUIRED` | 上次成功多候选未选择，409 |
| `CHAT_CONTEXT_INVALID` | 投影/绑定/数据结构无效，503 |
| `CHAT_CONTEXT_TOO_LARGE` | 当前必留输入或单个必留交换超限，413 |
| `CHAT_CONTEXT_COMPACTION_DISABLED` | 需要压缩但策略为 none，413 |
| `CHAT_CONTEXT_COMPACTION_LIMIT` | 旧前缀辅助输入或压缩后工作输入超限，413 |
| `CHAT_CONTEXT_COMPACTION_INVALID` | 模型摘要或原生窗口无效，502；不重试、不降级 |
| `CHAT_HISTORY_RESULT_LIMIT/CHAT_HISTORY_SCAN_LIMIT` | 原文检索结果/单步扫描不能安全满足界限，413 |

离线测试位于 `tests/test_chat_working_context_v2.py`：完整历史与 recent 对照、失败部分语义、完整/坏工具配对、选中候选、原生四项绑定、独立 reasoning、75% 主动阈值、最近两个完整轮次、严格摘要 16 KiB、检查点覆盖/源哈希/完整性/令牌失效、滚动摘要、完整原生窗口、超限安全失败、活动 Agent 完整交换与当前聊天检索。没有用真实 KEY、网络或付费模型请求代替这些测试。

补充资源边界：adaptive 来源覆盖最多 16384 轮且 UUID 元信息最多 640 KiB，与 recent 选择上限 1024 轮分开；超出明确 413，保留原始历史且不派发收费调用。达到三次主动压缩后，若当前窗口仍在硬输入预算内则继续主调用并给出提示；只有硬预算不够才停止，不能因75%主动阈值误终止本可继续的运行。
