# M01/M02：有来源的长期记忆提案与安全写入

> 自动记忆模块的实现契约；验证方法见[贡献指南](../../../CONTRIBUTING.md#ai-助手离线回归)。离线测试不能代替真实渠道验收。模块不调用模型、不联网、不创建后台任务、不修改聊天原始历史，也不编辑 SOUL/IDENTITY。

## 责任边界

- 用户明确启用后，父 Runner 才可执行提取；模块自身不读取或默认开启任何偏好开关。
- `user_sources` **只能**由服务端从本人当前聊天的真实用户消息构建，且应为用户明确陈述的长期事实/偏好。严格 DTO 是 `list[{"message_id": str, "text": str}]`。不接受额外的 `role` 或 `origin` 字段，更不能由客户端添加这些字段就把工具、网页、助手、引用内容升级为用户事实。
- 无 principal/chat 参数的纯函数无法证明任意字符串的归属。父层必须先通过 `ChatStore.open_conversation(principal, cid)` 的本人边界读取来源；不可接受客户端任意提交的来源列表。
- 事实标注 `kind: "user_stated"` 仅表示**用户说过**，不表示已独立核实。提取提示要求排除模型猜测、第三方转述、引用指令和临时任务。服务端还要求新增/更新正文与来源摘录逐字相同，且摘录确实出现在对应原文中；它不是事实真实性、长期性或语义意图的证明。没有可靠明确依据时应生成空提案或不做提取。
- 父 Runner 唯一负责付费调用、选定模型/KEY、`purpose="memory", publish=False`、用量、总次数/截止时间/停止、是否已有处理结果，以及审计事件的持久化。刷新、恢复、撤销不会从本模块发起模型调用。
- 本模块只新增一个导入目标：`new_api_cockpit.features.chat.auto_memory`，不改变既有个人设置/聊天存储接口。

## 精确导出接口

### 1. `build_prompt(user_sources, memory_content) -> list[dict]`

返回跨协议可转换的两个普通文本消息：

```json
[
  {"role": "system", "content": "提取约束及严格 JSON 结构说明"},
  {"role": "user", "content": "包含 user_sources、memory_content、managed_facts 的 JSON 文本"}
]
```

内容被明确标记为不可信数据，不是系统指令。调用前检查来源结构、预算、可识别凭据、MEMORY 总大小及受管区格式；异常时不应调用模型。整个 MEMORY 作为参考，其中手工区不可写。

### 2. `parse_proposal(response_text, user_sources, memory_content) -> MemoryProposal`

只接受一个完整 JSON 对象，无 Markdown 围栏、前后解释、多对象拼接、重复 JSON 键、NaN/Infinity 或未知字段：

```json
{
  "v": 1,
  "operations": [
    {
      "op": "add",
      "text": "我长期偏好用中文沟通。",
      "sources": [{"message_id": "真实用户消息ID", "excerpt": "我长期偏好用中文沟通。"}]
    }
  ]
}
```

操作白名单如下，所有列出的字段均为必填，不能增加其他字段：

| op | 精确字段 | 约束 |
|---|---|---|
| `add` | `op,text,sources` | 不接受模型自造 fact ID；服务端确定性分配 |
| `update` | `op,fact_id,text,sources` | 只能使用现存受管事实 ID，保留 ID |
| `delete` | `op,fact_id,sources` | 只能使用现存受管事实 ID；摘录用户明确更正/忘记请求 |

每个操作有 1–4 个不同来源；每个 `excerpt` 必须是对应用户原文中的逐字子串。对 add/update，**每个摘录必须等于事实 text**，不接受借真实摘录夹带额外猜测或翻译/改写。delete 同样要求原文证据，但明确纠正意图还依赖来源筛选和提取指令，不能仅靠字符串出现来证明。

返回的冻结 `MemoryProposal` 字段/属性：

- `base_memory_sha256: str`：提取时完整 MEMORY 的 UTF-8 SHA256；不是可自动更新的基线。
- `operations: tuple`：不可变内部操作对象，字段为 `op, fact_id, text, sources`。delete 的 `text` 为 `None`。
- 每个内部来源对象字段为 `message_id, excerpt, message_sha256`；后者是完整原始用户消息文本的 SHA256。
- `source_ids: tuple[str, ...]`：去重排序的全部来源消息 ID。
- `fact_ids: tuple[str, ...]`：按操作顺序排列的目标事实 ID。

仅使用 `parse_proposal` 的返回值调用后续接口；不要把 HTTP 请求体直接构造成 `MemoryProposal`。数据类冻结用于避免意外改动，不是对任意 Python 调用者的安全沙箱。

新增事实 ID 为 `fact_` 加上以下 canonical JSON 的 SHA256 前 24 个十六进制字符：`{"text": text, "source_ids": sorted(source_message_ids)}`。相同来源/正文/基线的解析、准备、应用产生完全一致的 ID；update 保持 ID。一个提案不能重复操作同一 ID，不能产生重复正文；重复添加既有事实会拒绝，不会隐式合并。

### 3. `prepare_update(memory_content, proposal) -> dict`

纯函数，不读写个人设置；验证提案基线，确定性计算最终受管内容与大小。精确返回：

```text
{
  before: str,
  after: str,
  before_sha256: str,
  after_sha256: str,
  source_message_ids: list[str]
}
```

父层可在写设置之前持久化如下 `memory.proposed` 意图，`v/profile/expected_*` 由父层补齐：

```text
{
  intent: {
    v: 1, profile: "MEMORY.md",
    before, after, before_sha256, after_sha256, source_message_ids,
    expected_context_revision, expected_memory_revision
  }
}
```

`after_sha256` 与 `apply_proposal` 的实际正文计算共享同一准备逻辑。恢复可比较意图与 manifest 当前版本/哈希来识别“已提交但完成事件尚未落盘”，**不能**自动重放旧意图、自动改用新版本或重新调用模型。意图和审计均为本人受保护聊天数据，可能各含完整 before/after，父层应计入存储预留。

### 4. `apply_proposal(user_context, principal, expected_context_revision, expected_memory_revision, proposal, credential="") -> dict`

父层从**同一次实时读取**中取出：

```text
view = user_context.read_context(principal)
memory = next(p for p in view["profiles"] if p["name"] == "MEMORY.md")
expected_context_revision = view["context_revision"]
expected_memory_revision = memory["revision"]
memory_content = memory["content"]
memory_sha256 = memory["sha256"]
```

用该正文构建/解析提案，并保留这两个原始版本；不能提取完毕再悄悄替换为最新版本。应用会实时读取本人上下文，检查两个期望版本和提案基线；最后始终调用：

```text
user_context.update_profile(
    principal, "MEMORY.md",
    expected_context_revision, expected_memory_revision, content
)
```

最终 CAS 在现有 profile 锁、manifest 提交点和存储配额内执行，关闭预读后的竞争窗口。即使空提案/无变化也执行该 CAS，不绕过版本比较。没有私有文件写入、重试、覆盖 SOUL/IDENTITY 或配额旁路。

精确审计返回值：

```text
{
  v: 1,
  kind: "auto_memory_update",
  owner_user_id: int,
  profile: "MEMORY.md",
  changed: bool,
  before: {content: str, sha256: str, context_revision: int, memory_revision: int},
  after:  {content: str, sha256: str, context_revision: int, memory_revision: int},
  source_ids: list[str],
  fact_ids: list[str]
}
```

`source_ids` 包括 delete 的新来源请求；原始用户消息仍在完整聊天存档中。`before/after` 内容保存旧/新事实及其旧/新来源。父层提交后存储 `memory.updated: {audit: 返回值}`，不要把字典转换成另一套不兼容格式。

### 5. `validate_audit(audit, principal) -> None`

供恢复/事件白名单复用的纯验证器；校验通过返回 `None`，失败抛出 `ChatError`。检查精确键、版本、归属、MEMORY 专用目标、正文 SHA256、版本增量、受管格式、手工区完全不变、受影响事实和来源集合的关联。**不**读取当前设置，也不是认证签名或撤销许可。实际撤销还有实时状态和 CAS 检查。

### 6. `undo_update(user_context, principal, audit, context_revision, credential="") -> dict`

- `audit` 必须来自服务端已保存、本人当前聊天可访问的审计事件，不接受客户端提交 before/after 正文。哈希用于完整性核对，不是签名。
- `context_revision` 是客户端明确看到的当前个人设置版本；父 HTTP 层另行校验请求的聊天 `expected_revision`。
- 本模块从审计 `after.memory_revision` 取预期 MEMORY 版本。实时 MEMORY 的版本、正文和哈希必须仍全部等于 audit.after，才可恢复 audit.before。
- 后续手工编辑、后续自动更新、先改再改回同样正文的 ABA 情况都因 MEMORY 版本冲突而拒绝；不覆盖后续手工修改。
- 若只有 SOUL/IDENTITY/技能等其他设置改变，MEMORY 仍完全匹配，则仅在传入新的明确 `context_revision` 时允许撤销，其他设置不变。
- 最终仍调用 `UserContext.update_profile` 双版本 CAS，防止预读后竞争。撤销新增一个版本，不倒退 manifest 的版本号；重复撤销不能再次覆盖。
- 返回原生写入结果，精确字段是 `{"context_revision": int, "changed": bool, "profile": {...原生个人文档DTO...}}`。父层可据此生成 `memory.reverted: {after_sha256, context_revision, memory_revision}`，使用返回 profile 的哈希/版本。

## 受管区格式与手工保护

唯一自动可写区域是完整 MEMORY 中的下列 framing（开头的换行也属于受管区）：

```text
\n<!-- cockpit:auto-memory:v1 -->\n
{canonical JSON: v=1, facts=[...], sha256=SHA256(canonical_json(facts))}
\n<!-- /cockpit:auto-memory:v1 -->
```

每条 fact 的精确字段：`id,kind,text,sources`，其中 sources 精确字段为 `message_id,excerpt,message_sha256`。facts 按 ID 排序，JSON 必须 canonical、无重复键，版本必须为整数 1，摘要必须匹配，不能有重复事实/ID。区块不能嵌套、重复、截断、换未知版本或擅自改空白。检测到任何保留标记但不符合格式时失败关闭，不把坏区块当作手写内容接着追加。摘要用于识别意外编辑，不对主动重新计算摘要的操作者提供密码学认证。

区块外的手工前缀/后缀不修剪、不重排、不替换，UTF-8 字节逐字保留。首次创建追加一个完整 framing；最后一条事实删除时仅移除 framing，保留原手工前后缀。已有个人设置会规范换行；对合法存储中保留的前导 BOM，写入适配会补偿现有 `update_profile` 的单 BOM 规范化，从而不丢失手工字节。若内容无法经现有规范化接口无损保存，拒绝自动写入，不旁路存储接口。

## 安全与容量

- 总 MEMORY（手写区、framing、来源、事实一起）最多 **8192 UTF-8 字节**，并服从更小的现有配置上限。提案解析和准备阶段就模拟最终大小；超限不写入、不删除手工内容腾空间。
- 最多 8 个操作；**每个完整操作的 canonical JSON 最多 512 UTF-8 字节**，不仅仅是 text。text 和摘录另外各限 512 字节、单行、非空且不修剪。长中文文本应提取更短的原文片段，不能改写以压缩。
- 来源最多 64 条，单条 text 最大 32768 字节，所有来源 text 合计最大 65536 字节；message ID 为最多 128 个受限字母数字/`_.:-` 字符，不接受路径。模型响应原始文本最多 8192 字节。
- 复用既有凭据拒绝，再增加 Bearer、常见 password/API key/token/secret 赋值、AWS/GitHub 格式、URL 内嵌认证和敏感查询参数等检查。来源在发送模型前检查；事实/摘录按 JSON 解码后的内容检查，避免转义绕过；错误不回显匹配值。
- 已知当前凭据通过 `credential` 传入 apply/undo，只用于即时验证，不保存该参数。校验同时检查序列化正文和解码后的事实/来源，所以引号、反斜杠等 JSON 转义不能隐藏已知凭据。父层掌握当前凭据时，还应在发送提取输入、持久化 proposed 意图之前检查对应明文，不能只等 apply 才拒绝。
- 这是保守启发式过滤，不是完备 DLP；任意未知、无格式的秘密无法仅靠正则证明不存在。不要声称模块能够自动识别所有秘密。

## 错误与父层处理

| code | 含义 |
|---|---|
| `CHAT_MEMORY_PROPOSAL_INVALID` / 422 | DTO、JSON、证据、操作或来源预算不合法 |
| `CHAT_MEMORY_BLOCK_INVALID` / 409 | 保留受管区被手改、损坏或未知格式；手工检查，不覆盖 |
| `CHAT_MEMORY_CHANGED` / 409 | 提案正文 SHA256 基线已变化 |
| `CHAT_CONTEXT_CHANGED` / 409 | 实时或最终 CAS 的个人设置版本冲突 |
| `CHAT_PROFILE_REVISION_CONFLICT` / 409 | MEMORY 文档版本冲突 |
| `CHAT_MEMORY_UNDO_CONFLICT` / 409 | 审计归属/完整性不符，或 MEMORY 后续已修改 |
| `CHAT_CONTEXT_CREDENTIAL_FORBIDDEN` / 422 | 识别出认证凭据，不回显内容 |
| `CHAT_PROFILE_TOO_LARGE` / 413 | 受管更新后完整 MEMORY 超过 8 KiB |

既有会话认证、存储配额、磁盘故障等 `ChatError` 原样向上抛出，不捕获并伪装成成功。父层可以对提案/证据/CAS 问题追加 `run.notice` 而不使已经完成的回答失败；取消、截止、存储故障应继续传播。父层必须按具体错误码/故障类型区分，不可无差别吞掉所有异常。没有失败自动重试或隐藏付费回退。

## 离线验收

`tests/test_chat_auto_memory_v2.py` 使用真实 `ChatStore`、`UserContext`、文件系统锁/manifest CAS；无网络模型调用。覆盖：本人真实用户事件来源与管理员隔离、确定性提案意图和审计、手工前后缀/BOM 保留、add/update/delete 稳定 ID、受管区损坏失败、双版本/基线冲突、预读后写入/撤销竞争、ABA、既有其他设置保护、审计白名单和篡改拒绝、无来源/猜测/重复 JSON/未知字段、凭据及 JSON 转义、空提案、8 操作/512 字节/8 KiB 精确边界及存储异常传播。

本文件只记录模块契约。父层的显式启用、辅助费用、事件崩溃恢复、HTTP/前端入口和取消/截止/存储预留需在父任务中另行验收。
