# 聊天 HTTP、事件流与界面契约

> 本文描述当前源码契约，验证入口见[开发验证](../../../CONTRIBUTING.md#ai-助手离线回归)。所有数值限制引用[运行限制表](chat-runtime.md#1-固定限制与配置)。

> 2026-10-16 扩展：原有鉴权/分页/幂等保持不变；运行可选参数、候选选择、Memory 撤销和新增 SSE 事件见[扩展契约](../ai-assistant-v2/contracts.md)。新代码读取旧事件；旧代码不保证读取扩展事件。

## 1. 通用规则

- 页面：`GET /cockpit/chat`、`GET /cockpit/chat/`，web 容器提供；其余本页接口前缀统一为 `/cockpit/api/chat`，chat 容器提供。
- 仅接受现有官方验证过的登录会话（Cookie 或 dashboard JWT）；首版不开放管理员 PAT 调用。显式 Bearer 无效时不回退 Cookie。
- 每个 API 请求要求 `X-Cockpit-Session` 匹配当前会话，包括 GET、下载和停止。浏览器使用已有 `CockpitAuth`；无 session stamp 返回 403，账号切换返回 409 `AUTH_SESSION_CHANGED`。
- POST/PUT/PATCH/DELETE 另需 `X-Cockpit-Chat: 1` 和同源验证。JSON 路由必须 `Content-Type: application/json`；唯一例外是附件 multipart。
- cookie/JWT、原始 KEY、user_id、base_url、headers、system_prompt、任意本地路径不能出现在业务请求体。未知字段拒绝，不静默忽略。
- 身份/owner 检查先于读取状态和磁盘。不存在与不属于本人统一 404 `CHAT_NOT_FOUND`，不返回原拥有者、全路径或文件是否存在。
- 管理员也只查询自己的根目录；不存在管理员 bypass、`all_users` 或 `owner_id` 查询选项。
- 所有成功/错误响应含 `Cache-Control: no-store`；文件额外 `X-Content-Type-Options: nosniff`。禁止代理/CDN共享缓存。
- ID 为小写 UUID v4；token_id 为严格整数 1..2147483647（bool 不算 int）。时间 RFC3339 UTC，大小为 UTF-8 字节。
- POST 付费请求永不自动重试；只读 GET 可以由既有认证层刷新后重试一次。models 是不付费 POST，但也不自动重放。

认证适配必须是聊天专用：在宿主 `request_identity` 的聊天端点分支拒绝PAT并标注可信auth_kind=session；聊天检查stamp为必填，不能沿用宿主当前“有就比对、没有也过”的默认行为。
浏览器使用Cookie和包装后的 `window.fetch`，流式响应不能走会整段解析JSON的 `CockpitAuth.json`。
首版聊天前端不提供手填Bearer输入框；外部显式dashboard JWT调用方需自行同时提供其会话stamp。现有 `CockpitAuth.fetchOnce` 的显式Authorization路径会移除stamp，不能直接拿来调用聊天，也不应为此全局改变旧PAT行为。

统一错误包，保持当前项目顶层 error/code 风格：

```json
{
  "error": "本轮已中断，未自动重发；请核对 New API 用量。",
  "code": "CHAT_UPSTREAM_INTERRUPTED",
  "uncertain": true,
  "run_id": "22222222-2222-4222-8222-222222222222",
  "details": {}
}
```

`run_id` 未创建时为 null；details 仅白名单结构（例如 limit、retry_after_seconds），不回显上游错误正文、凭据或用户提示词。
认证错误继续使用宿主现有 AUTH_* 格式，不强求改造其他模块。

## 2. 路由清单

下表路径均接在 `/cockpit/api/chat` 后，除特别说明外返回 JSON。

| 方法 / 路径 | 用途 | 成功状态 |
|---|---|---|
| GET `/config` | 非秘密UI限制与允许文件类型 | 200 |
| GET `/tokens` | 本人全部分组的 KEY 摘要 | 200 |
| POST `/models` | 所选 KEY 的模型与明确能力 | 200 |
| GET `/conversations` | 本人所有未删除聊天，分页 | 200 |
| POST `/conversations` | 创建空聊天，幂等 | 201；已存在 200 |
| GET `/conversations/{cid}` | 本人聊天元数据 | 200 |
| PATCH `/conversations/{cid}` | 仅重命名 | 200 |
| DELETE `/conversations/{cid}` | 明确删除聊天与其文件 | 204 |
| GET `/conversations/{cid}/events` | 获取完整事件/增量，分页 | 200 |
| GET `/conversations/{cid}/files` | 列出该聊天文件 | 200 |
| POST `/conversations/{cid}/files` | 上传一个文本文件 | 201 |
| GET `/conversations/{cid}/files/{fid}/download` | 本人当前聊天文件下载 | 200，attachment |
| POST `/conversations/{cid}/runs` | 接受一轮并流式返回，或幂等命中 | 200，SSE 或 JSON |
| GET `/conversations/{cid}/runs/{rid}` | 刷新/断线后查状态，绝不继续生成 | 200 |
| POST `/conversations/{cid}/runs/{rid}/cancel` | 请求停止本轮 | 202；已终态 200 |

首版没有删除单条消息、修改历史、分支、单文件删除、后台恢复运行接口。
HEAD/OPTIONS 不得绕过同一路由授权；不配置跨域 CORS。

## 3. KEY 与模型

`GET /config` 返回 `{schema_version:1, limits:{...}, allowed_file_extensions:[...], modes:["chat","files"]}`。
limits仅暴露前端需要的用户输入/文件/页数/工具/Skill限制，键为运行表常量的小写形式；不包含数据路径、连接地址、凭据、其他用户用量或完整能力配置文件。

`GET /tokens` 返回 `{tokens: TokenSummary[], token_manager_path: "/keys"}`，字段见[公共契约](module-boundaries.md#42-本人-key两个功能各自-ports-中定义同一兼容协议)。
不可用 KEY 可以显示但禁止选择；列表只含摘要，不含原始 KEY。

`POST /models` 请求：

```json
{"token_id": 123}
```

响应示例（假设站点已显式配置该测试模型）：

```json
{
  "models": [
    {
      "id": "example-chat-model",
      "protocol": "chat_completions",
      "chat_enabled": true,
      "tools_enabled": true,
      "reason": "",
      "capability_revision": "sha256:example-fixture"
    }
  ],
  "token_manager_path": "/keys"
}
```

示例 revision 是占位测试值，真实值必须是 `sha256:` + 64 位十六进制。
无协议证据的模型列出但 `chat_enabled=false`，明确说明“协议未确认”；工具能力未知默认 false。
不能通过模型名字猜测 tools 支持；配置规则见运行文档。每轮重新核对模型仍属于该 KEY 的可用目录。

## 4. 聊天与列表

### 4.1 创建与重命名

`POST /conversations`：

```json
{"conversation_id":"11111111-1111-4111-8111-111111111111","title":"分析本周材料"}
```

UUID 由浏览器在点击新建时生成，只是幂等标识，不是授权凭证。
title 可省略或空串，默认“新聊天”；去首尾空白后最多 TITLE_CHARS，不允许控制字符。
相同 owner/cid 已存在时返回现有元数据 200，**不覆盖现有标题/消息**；删除 tombstone 存在返回 410，不复活。
同 UUID 在另一用户命名空间不授予任何访问权；系统不进行跨用户目录查询。

`PATCH /conversations/{cid}`：`{"title":"新的标题","expected_revision":3}`。
标题修改只允许这两个字段；revision 不匹配 409 `CHAT_REVISION_CONFLICT`。运行中返回 409 `CHAT_CONVERSATION_BUSY`。

同一路由新增互斥的 Skills 设置请求：

```json
{"expected_revision":3,"context_revision":2,"loaded_skill_ids":["22222222-2222-4222-8222-222222222222"]}
```

最多 3 个本人活跃模板 ID，有序且唯一；不得混入 title 或 owner 字段。当前聊天持久保存，发送、刷新及个人设置刷新不清空。空数组移除全部，不删除个人模板库或历史快照。设置不调用模型、不取 KEY；以 conversation revision 和个人 context_revision 双版本校验，冲突不覆盖，运行中拒绝。成功仍返回 `{conversation}`，没有变化不新增事件；载入前检查本人归属、真实内容及合成上下文预算。旧运行请求的 skill_ids/幂等语义保持兼容。
首条用户输入可生成一个本地截断标题（仅当前 title 仍为“新聊天”时），不为标题额外调用模型。
本地自动改名在run.accepted之后以conversation.renamed提交，并按全会话seq进入该次SSE；不能跳过一个seq让页面只看到后续运行事件。

### 4.2 元数据

创建、单条详情、PATCH 返回相同形状：

```json
{
  "conversation": {
    "id": "11111111-1111-4111-8111-111111111111",
    "title": "分析本周材料",
    "created_at": "2026-10-15T02:00:00Z",
    "updated_at": "2026-10-15T02:01:00Z",
    "revision": 3,
    "last_seq": 12,
    "message_count": 2,
    "active_run_id": null,
    "last_run_status": "completed",
    "storage_state": "ok",
    "last_selection": {"token_id":123,"model":"example-chat-model","mode":"chat"},
    "loaded_skill_ids": []
  }
}
```

storage_state=`ok/repaired/corrupt`；corrupt 仍列出，可查看已经验证的完整前缀，但禁止发送/上传/工具执行，界面提示联系维护者从备份修复。
`last_selection` 保留作为该聊天历史运行的选择记录，不能绕过重新授权；最新页面的全局 KEY/模型/模式使用当前账号浏览器缓存，不再按该字段覆盖表单。KEY 删除/失效不影响历史读取。
message_count 为用户消息 + 完成/部分助手消息数量，不含工具步骤；revision 每个持久事件推进到其 seq，last_seq 与其一致。

### 4.3 全部历史的分页语义

`GET /conversations?limit=50&cursor=...`；limit 1..LIST_PAGE_MAX，默认 LIST_PAGE_DEFAULT。
默认**按创建时间倒序、id 倒序**稳定排序，避免活动聊天 updated_at 变化造成漏页；不默认隐藏失败/中断/空聊天。
首次列举选择当前列表高水位，下一页 cursor 编码 `{v:1, high_water:[created_at,id], after:[created_at,id]}` 的 base64url JSON。
cursor 是校验过的分页位置，不是权限证据，不含 owner；编码前后均最多1024字节、v固定1、时间/UUID严格校验，始终在当前 owner 根目录内过滤。
新建聊天出现在重新加载的第一页，UI 可在创建成功后立即插入；旧页快照不混入新记录。

```json
{
  "conversations": [],
  "next_cursor": null,
  "has_more": false,
  "total": 0,
  "warnings": []
}
```

非空返回的每项形状与 conversation 一致；`total` 是本次响应时在高水位内的本人未删除数量；分页期间删除会使其减少，不承诺数据库级冻结快照。
has_more=true 必须有 next_cursor，前端“加载更多”一直到 false；不得只 fetch 前 50 项或以可变 updated_at 作游标。
损坏、缺失或合法但漏项的索引均须与本人目录集合核对后修复，不能返回假空列表；根目录不可读返回 503，单个会话损坏以条目+warnings 呈现，不影响其他聊天。
首版不做服务端全文搜索，避免搜索条件让用户误以为历史丢失。

### 4.4 明确删除

DELETE body 为 `{"expected_revision":12,"confirm":true}`；必须弹窗说明会删除该聊天全部记录、上传与输出。
运行中不允许删除，先停止并确认终态。版本冲突返回 409。删除成功 204；已有本人 tombstone 再删仍 204，不存在且无 tombstone 返回 404。
删除影响当前存储，不承诺立即擦除宿主备份或 New API/模型供应商日志。

## 5. 历史事件读取

`GET /conversations/{cid}/events?after_seq=0&limit=200&until_seq=80`。
after_seq 默认为 0；limit 1..EVENT_PAGE_MAX；until_seq 初次可省略，由后端固定为commit.json已发布的seq，不以最后有LF的行替代已提交边界。
后续分页传同一 until_seq，直到 has_more=false，再用新的高水位轮询增量。

```json
{
  "events": [],
  "until_seq": 0,
  "next_after_seq": 0,
  "has_more": false,
  "storage_state": "ok"
}
```

events 每项是[事件格式](chat-storage.md#3-事件格式与数据字典)的完整对象，且不含内部 request hash、凭据或本地路径。
until_seq 超过当前已提交seq或after_seq > until_seq 返回400。未发布commit的事件（即使已经写入LF）不向GET暴露。
界面按 seq 去重、按 run_id 聚合；`assistant.message` 是对同 message_id 的最终快照，不与 delta 再拼接一遍。
查看历史不触发模型请求、不续跑工具，也不依赖当初 KEY 仍存在。

## 6. 文件

### 上传

`POST /conversations/{cid}/files`，multipart 只有 `file` 和 `client_file_id` 两部分。
client_file_id 为 UUID v4，映射成 fid；同 ID 同内容哈希返回已有文件 200，不重复存储；同 ID 不同内容返回 409。
原始文件名只作为展示，文件内容必须通过类型、UTF-8、NUL、大小和额度检查；详见 workspace 规则。
运行中禁止上传，避免工具看到变化中的工作区；上传完再发送消息。

```json
{
  "file": {
    "id":"33333333-3333-4333-8333-333333333333",
    "kind":"upload",
    "name":"周报.md",
    "mime":"text/plain; charset=utf-8",
    "size_bytes":1200,
    "sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    "created_at":"2026-10-15T02:00:10Z",
    "logical_name":null,
    "version":null,
    "source_run_id":null,
    "download_path":"/cockpit/api/chat/conversations/11111111-1111-4111-8111-111111111111/files/33333333-3333-4333-8333-333333333333/download"
  }
}
```

GET files 返回 `{files: FileSummary[], total: int}`，数量受 FILES_PER_CONVERSATION 上限限制，返回全部；含上传和每个输出版本，不只最后版本。
文件列表数据来自已提交事件，未提交临时文件不可见。
下载使用带 session stamp 的 fetch → Blob → 用户点击保存，不用公开静态链接。鉴权后只按 fid 查映射；无 path 参数、无 URL fetch、无 inline HTML。
Content-Disposition 使用安全文件名，拒绝 CR/LF；忽略 Range，完整有界下载即可。

## 7. 发起与停止一轮

### 7.1 请求

```json
{
  "client_request_id":"44444444-4444-4444-8444-444444444444",
  "expected_revision":12,
  "token_id":123,
  "model":"example-chat-model",
  "capability_revision":"sha256:example-fixture",
  "context_revision":4,
  "skill_ids":[],
  "mode":"files",
  "text":"请汇总附件，生成一份 Markdown 报告。",
  "attachment_ids":["33333333-3333-4333-8333-333333333333"]
}
```

- mode=`chat/files`。chat 模式 attachment_ids 必须为空；files 模式允许当前聊天已有文件，工具可访问这个聊天的完整文件清单，不能跨聊天。
- text 必须含非空白内容且不超 USER_TEXT_BYTES；数组元素唯一、属于当前聊天且不超 ATTACHMENTS_PER_TURN。
- KEY、模型、能力 revision、文件和权限全部在发起前复核；变更时 409 `CHAT_CAPABILITY_CHANGED`，要求刷新选择，不自动重发。
- 完整预检失败不创建 run、不落用户消息、不收费；持久化 run.accepted 后失败必须在历史中留下该轮及终态。
- 发起前同步保存 run.accepted（包含用户消息、参数快照和幂等 ID），再开始模型请求。
- 相同 owner/cid/client_request_id、相同语义载荷：返回原 run，不发第二次请求；换了 text/token/model/files/mode/capability_revision/context_revision/skill_ids 则 409 `CHAT_IDEMPOTENCY_CONFLICT`。expected_revision 不参与幂等摘要。
- 顺序固定为“认证/归属/基础格式 → 幂等命中/冲突 → 未命中才检查revision/容量/KEY/模型/附件/个人配置”。原KEY失效或配置变化不能阻止合法重复请求读取原run，也绝不能导致再次派发。
- 尚未落盘的并发重复请求可能得到 409 busy；只能 GET 历史确认，前端不自动 POST 重试。

首发响应为 `200 text/event-stream; charset=utf-8`；重复命中为 `200 application/json`：

```json
{"replayed":true,"run_id":"22222222-2222-4222-8222-222222222222","status":"completed","last_seq":30}
```

前端先检查 Content-Type 再决定解析器。重复命中后用 GET 事件恢复，不重新开始 SSE。

### 7.2 SSE

使用 POST fetch + ReadableStream，不使用会自动重连的 EventSource。

```text
id: 13
event: run.accepted
data: {"v":1,"seq":13,"type":"run.accepted","conversation_id":"11111111-1111-4111-8111-111111111111","run_id":"22222222-2222-4222-8222-222222222222","at":"2026-10-15T02:01:00Z","data":{}}

: heartbeat

id: 15
event: assistant.delta
data: {"v":1,"seq":15,"type":"assistant.delta","conversation_id":"11111111-1111-4111-8111-111111111111","run_id":"22222222-2222-4222-8222-222222222222","at":"2026-10-15T02:01:02Z","data":{"message_id":"55555555-5555-4555-8555-555555555555","text":"已读取"}}

```

上例 accepted.data 省略展示；完整字段见存储事件表，实现不能发送空 accepted。
SSE id=全会话 seq，event=type，data=一行 JSON。心跳不落盘、不计 seq；HTTP 设置 `X-Accel-Buffering: no`，无压缩/缓存。
只发送已经持久化的事件；文件内容和工具返回有上限。流已开始后的错误使用 `run.terminal`，不能再试图改 HTTP 状态或返回第二段普通 JSON。
正常结束必须有 run.terminal；没有终态就 EOF，前端显示“连接中断，状态待确认”，用 GET 查状态，不显示成功、不重发。

### 7.3 状态与停止

GET run 返回：

```json
{
  "run": {
    "id":"22222222-2222-4222-8222-222222222222",
    "client_request_id":"44444444-4444-4444-8444-444444444444",
    "status":"cancelled",
    "model_requests":1,
    "tool_calls":0,
    "last_seq":18,
    "cancel_requested":true,
    "uncertain":true,
    "usage":null,
    "usage_complete":false,
    "error_code":null
  }
}
```

状态：accepted → running → completed/failed/cancelled/interrupted；终态不再运行。usage 可为 null，不允许把未知用量显示为 0。
cancel POST body 必须 `{}`。返回 `{run_id,status,cancel_requested:true}`；202 表示停止请求已持久化，**不等于已停止或不会计费**。
执行者在读取循环、工具之间、每次派发前检查取消；关上游连接，保留已收到的文本和文件。
停止按钮先发送 cancel，再关闭当前流；即使 cancel 失败也关闭浏览器流并查询终态。取消没有事务回滚承诺。
刷新后的页面最多每 POLL_INTERVAL 秒 GET 状态/增量，终态后停止轮询；绝不复用旧 POST 恢复生成。

## 8. 错误码与 HTTP 映射

| HTTP | code | 处理 |
|---|---|---|
| 400 | CHAT_INVALID_REQUEST / CHAT_INVALID_CURSOR | 修正输入，不重发付费请求 |
| 401/403/409 | AUTH_* | 原认证规则；切账号清空页面内存 |
| 403 | CHAT_SESSION_REQUIRED / CHAT_TOKEN_FORBIDDEN | 重新登录或选本人 KEY |
| 404 | CHAT_NOT_FOUND | 不存在或非本人，不区分 |
| 409 | CHAT_REVISION_CONFLICT / CHAT_CONVERSATION_BUSY / CHAT_USER_BUSY | 刷新状态；不排队 |
| 409 | CHAT_IDEMPOTENCY_CONFLICT / CHAT_FILE_CONFLICT | 同一 ID 载荷不同，拒绝 |
| 409 | CHAT_CAPABILITY_CHANGED / CHAT_MODEL_UNSUPPORTED / CHAT_TOOLS_UNSUPPORTED | 重新选择；不偷偷降级 |
| 410 | CHAT_DELETED | 已删除，不能用旧 ID 重建 |
| 413 | CHAT_REQUEST_TOO_LARGE / CHAT_CONTEXT_TOO_LARGE / CHAT_FILE_TOO_LARGE | 缩短内容或新建聊天 |
| 415 | CHAT_FILE_TYPE_UNSUPPORTED | 使用允许的 UTF-8 文本 |
| 429 | CHAT_CAPACITY_BUSY | 全局容量满；只显示稍后手动重试 |
| 507 | CHAT_STORAGE_LIMIT / CHAT_STORAGE_FULL | 超额度/磁盘不足；保留已有历史 |
| 503 | CHAT_STORAGE_UNAVAILABLE / CHAT_STORAGE_CORRUPT / CHAT_CONFIG_INVALID | 不派发付费请求 |
| 502/504 | CHAT_UPSTREAM_ERROR / CHAT_UPSTREAM_INTERRUPTED / CHAT_DEADLINE | 记录失败/不确定，不自动重发 |

流中错误沿用 code，但 HTTP 已是 200；`uncertain` 依据是否已进入派发边界，不能只依据 HTTP 状态判定。
工具错误码见运行文档；工具错误可作为安全有界结果交回模型，权限拒绝不能变成读取宿主文件的理由。
个人设置/Skill专属错误码见[个人上下文](user-context.md)，同样使用顶层error/code并遵守no-store。

## 9. 页面交互与账号切换

- 左侧：新建、本人所有聊天、加载更多、重命名、删除；标题不会包含 token_id 或完整文件路径。
- 中间：用户消息、流式助手回答、折叠工具步骤、运行状态、用量“未知/部分/完整”。失败和中断不能从聊天列表消失。
- 全局顶部：本人 KEY/模型/模式，脱离会话且按账号缓存选择；新建/切换聊天不重置。刷新时重新读取本人 KEY 和模型能力，不缓存凭据或目录。切 KEY 或模式只影响下一轮，已接受运行的选择不变；查看活动运行时表单冻结。
- 文件侧栏：当前聊天上传和所有输出版本；明确“上传原件只读，输出不覆盖原件”。
- 发送有正常计费提示；长历史提示实际上下文只取最近完整轮次，历史仍全部可查看。
- 新建按钮只创建空会话，不生成。没有任何 KEY 时仍可查看历史；发送不可用。
- URL 可有 `?conversation=<uuid>` 以便回到聊天，不含 KEY/正文。直接访问别人 ID 只显示不存在。
- 正文/文件不进 localStorage/sessionStorage/IndexedDB；草稿仅页面内存，刷新可能丢失未发送草稿，已提交消息从服务器恢复。
- 监听 `cockpit-auth-changed`：立即中止旧读取、清空 DOM/内存/Blob URL/请求状态，不把旧账号流渲染进新账号页面；异步回包必须匹配页面 generation + cid + run_id。
- Markdown 禁用原始 HTML、脚本、iframe、SVG/图片自动加载；链接仅 http/https，外链 noopener/noreferrer。禁止模型借图片请求向外发送会话内容。
- 首版可用安全的 Markdown 子集实现（段落、列表、代码、链接），不能未经净化 `innerHTML`；未支持语法原样显示。

前端不自行拼接文件系统路径，不把某聊天的临时工具状态复用于另一个聊天。

## 10. 个人设置、Skill、Memory入口

`/cockpit/chat/settings/` 提供本人设置管理，具体API、字段和格式见[个人上下文](user-context.md)。
发送区提供显式Skill选择（默认不加载）；发送前显示本轮使用的个人上下文版本，body 必填 context_revision、skill_ids。
重新打开历史时不自动把历史选择的Skill重新加载到下一轮；必须由用户选择或明确确认当前界面选项。
用户只能管理自己的 MEMORY.md、SOUL.md、IDENTITY.md 和 Skill；这些不是管理员站点全局配置。
