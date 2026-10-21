# 有限运行循环、四个工具与部署增量

> 本文描述当前源码契约，验证入口见[开发验证](../../../CONTRIBUTING.md#ai-助手离线回归)。部署步骤见[AI 助手配置](../../chat.md#配置与可选请求池)，部署状态不写入公共契约。

## 1. 固定限制与配置

以下保留兼容基线与当前扩展默认值。集中实现于 `features/chat/config.py`，不能在前后端散落不同数字；API向前端返回必要的非秘密限制。
除“可配置”项外首版作为常量。调整默认值应修改此表与测试，不让模型自行决定无限上限。

| 名称 | 值 | 说明 |
|---|---:|---|
| GLOBAL_RUNS | 2 | `CHAT_MAX_CONCURRENT_RUNS` 可配1..8；跨进程/同数据根共享 |
| USER_RUNS | 1 | 每用户；不排队 |
| MAX_MODEL_REQUESTS | 6（主调用） | 一次用户发送最多6次主请求派发，含工具后续；辅助另受上限控制 |
| MAX_AUX_MODEL_REQUESTS / MAX_TOTAL_MODEL_REQUESTS | 4 / 10 | 辅助与全部派发上限；整轮截止包含全部步骤 |
| MAX_CANDIDATES / MAX_TOOL_SCHEMAS | 4 / 8 | 普通多候选 / Agent 固定 schema 总数 |
| EXTENDED_CONTEXT_BYTES / MODEL_REQUEST_BYTES | 2097152 / 4194304 | v2 配置输入 / 完整模型请求体上限 |
| MAX_OUTPUT_TOKENS / SUMMARY_TRIGGER_PERCENT | 32768 / 75 | 配置输出上限 / 主动压缩阈值 |
| REASONING_BYTES / PROTOCOL_STATE_BYTES / SUMMARY_BYTES | 262144 / 524288 / 16384 | 公开推理 / 单 replay DTO / 可读摘要 |
| MAX_TOOL_CALLS | 16 | 一轮累计；同一模型响应最多4个，按顺序执行 |
| RUN_DEADLINE_SECONDS | 180 | 从 accepted 起总截止，不在每次请求重置 |
| CONNECT_TIMEOUT_SECONDS | 5 | 建连上限，受总截止约束 |
| UPSTREAM_IDLE_SECONDS | 60 | 上游无有效数据上限；不能用心跳无限续命 |
| OUTPUT_TOKENS_PER_REQUEST | 2048 | 显式输出上限；真实用量只认上游报告 |
| USER_TEXT_BYTES | 32768 | 单条用户正文 UTF-8 字节 |
| CONTEXT_BYTES | 65536 | 完整序列化 messages 上限，含个人设置/Skill/工具结果 |
| CONTEXT_HISTORY_RUNS | 1024 | 仅兼容 recent 选择的轮次上限；adaptive 覆盖元信息独立限额，不裁UI历史 |
| CONTEXT_COVERAGE_RUNS / CONTEXT_COVERAGE_BYTES | 16384 / 655360 | adaptive 的完整来源覆盖元信息限额，超出明确413，不沿用recent的1024上限 |
| RUN_TEXT_BYTES | 262144 | 一轮所有助手文本合计 |
| UPSTREAM_BYTES_PER_REQUEST | 4194304 | 单次上游流总原始字节上限 |
| SSE_FRAME_BYTES | 262144 | 单个上游 SSE frame 和工具参数累积分别有界 |
| TOOL_ARGUMENT_BYTES | 131072 | 每个工具 arguments JSON 字节上限 |
| TOOL_RESULT_BYTES | 32768 | 回传模型的单工具结果序列化字节上限 |
| TOOL_WRITE_BYTES | 65536 | 每次生成文件内容 UTF-8 上限 |
| TOOL_SEARCH_SCAN_BYTES | 8388608 | 每次检索最多扫描8 MiB；最多100条命中 |
| TOOL_READ_MAX_LINES | 200 | 一次读取最多200行且受 TOOL_RESULT_BYTES 限制 |
| TOOL_DEADLINE_SECONDS | 5 | 单工具截止，受本轮剩余时间约束 |
| DELTA_FLUSH_MS / DELTA_FLUSH_BYTES | 250 / 4096 | 合并增量后持久化；遇UTF-8边界正确切分 |
| HEARTBEAT_SECONDS / POLL_INTERVAL | 10 / 2 | SSE心跳 / 刷新后只读状态轮询 |
| EVENT_PAGE_MAX | 200 | 一页历史事件，达到事件字节响应上限也提前分页 |
| EVENT_RESPONSE_BYTES | 2097152 | 事件分页返回最多2 MiB；至少容纳1个合法事件 |
| EVENT_BYTES | 1048576 | 单个持久事件的JSON字节上限 |
| LIST_PAGE_DEFAULT / LIST_PAGE_MAX | 50 / 100 | 会话列表分页 |
| TITLE_CHARS | 80 | 标题Unicode字符数 |
| CONVERSATIONS_PER_USER | 200 | 超限拒绝新建，不删除旧聊天 |
| FILES_PER_CONVERSATION | 200 | 所有上传及输出版本合计 |
| ATTACHMENTS_PER_TURN | 8 | 当前轮显式引用文件 |
| UPLOAD_FILE_BYTES | 52428800 | 单文件50 MiB（含上限）；界面显示50 MB |
| UPLOAD_REQUEST_OVERHEAD_BYTES | 65536 | multipart请求为文件上限加64 KiB，Nginx为51264k |
| UPLOAD_FORM_MEMORY_BYTES | 131072 | 有界multipart解析缓冲；文件由框架自动spool，字段/部件仍严格校验 |
| JSON_REQUEST_BYTES | 131072 | 普通聊天JSON请求上限；设置接口另按自身约束 |
| CONVERSATION_BYTES | 134217728 | 每聊天128 MiB，含事件、文件、快照及在途预留；支持50 MiB上传双份发布预留后继续运行 |
| USER_STORAGE_BYTES | 1073741824 | 每用户1 GiB，含个人设置与Skill |
| TOTAL_STORAGE_BYTES | 10737418240 | `CHAT_TOTAL_STORAGE_BYTES` 可降低/提高，需 >= USER_STORAGE_BYTES |
| RUN_STORAGE_RESERVE_BYTES | 16777216 | 每轮预留16 MiB；不代表会实际占满 |
| RUN_EVENT_BYTES | 8388608 | 每轮日志最多8 MiB；包括工具/文本快照 |
| EVENT_TERMINAL_RESERVE_BYTES | 16384 | 日志预算保留给终态；部分回答可直接由已保存delta投影 |
| DISK_FREE_MIN_BYTES | 536870912 | 新操作要求磁盘至少512 MiB空余且满足预留 |
| QUEUE_EVENTS | 32 | 每运行生产者→SSE队列；满且消费者不前进则停止 |
| STORAGE_LOCK_WAIT_SECONDS | 0.5 | 本机短期配额锁有界等待；不用于会话运行锁或模型重试 |

HTTP 上传在解析 multipart 前先核对本人聊天归属，再通过稳定 inode 的实例级 `upload.lock` 仅接纳 1 个上传；并发返回 429 `CHAT_UPLOAD_BUSY`，不排队、不重传。这样 50 MiB 文件的磁盘 spool 不会并发挤满现有 64 MiB `/tmp`。上传、解析异常均释放锁，禁止删除锁文件。

个人设置与 Skill 的尺寸/数量约束以[个人上下文文档](user-context.md)的限制表为准；这些内容计入所选模型的当前输入预算和存储额度。
限制触发不等于自动重试：发起前拒绝；运行中收尾并明确原因。第6个主调用仍想用工具时不执行新工具；辅助请求独立记账且最多4次，不伪装成免费总结。

环境配置仅新增：

| 配置 | 默认/规则 |
|---|---|
| CHAT_ENABLED | false；未启用不显示入口，API返回404；配置成功后由运维开启 |
| CHAT_DATA_DIR | `/app/data/chat`；绝对路径，不由用户覆盖 |
| CHAT_INSTANCE_ID | 开启时必填；`[a-z0-9][a-z0-9_-]{0,63}`，稳定不变 |
| CHAT_CAPABILITIES_FILE | 可空；绝对路径只读JSON，不接受网页路径 |
| CHAT_WEB_SEARCH_BACKEND / CHAT_WEB_SEARXNG_URL | 可空；配置公网标准端口的 searxng JSON 后端，不含凭据 |
| CHAT_MAX_CONCURRENT_RUNS | 2，允许1..8；需配合线程与内存容量 |
| CHAT_TOTAL_STORAGE_BYTES | 10737418240 |
| CHAT_PORT / CHAT_MEMORY | 8093 / 1g；Compose层，不进入模型参数 |

目录/身份/配置无效则 chat readiness=503，不能悄悄回退 `/tmp` 或内存。

## 2. 模型能力与协议

当前扩展已实现固定 `NEW_API_INTERNAL_URL` 下的 `/v1/chat/completions`、`/v1/responses` 与 `/v1/messages` 三协议；原生压缩传输为 `/v1/responses/compact`，只供显式确认兼容能力后使用。没有用户自定义 URL、代理、headers、路径或重定向。
从本人 KEY 的 `/v1/models` 获取真实目录；不请求公开模型列表，不按名称构造可用模型。

能力配置样例（模型名为虚构值）：

```json
{
  "schema_version": 1,
  "models": {
    "example-chat-model": {
      "chat": true,
      "tools": true,
      "output_token_parameter": "max_completion_tokens",
      "stream_usage": true,
      "max_input_bytes": 65536
    }
  }
}
```

- `chat` 缺配置时仅在目录明确包含 chat 协议时开启；协议未知需运维显式确认 chat=true。配置 chat=false 可禁用，不能让配置添加目录里没有的模型。
- `tools` 仅显式 true 开启，不从模型名、价格、供应商推断。支持目录元数据或运维明确指定的 Responses/Anthropic 原生协议，不要求伪装成 Chat Completions。
- `output_token_parameter` 仅允许 `max_tokens/max_completion_tokens`，默认 max_tokens；`stream_usage` 默认 false，true 才发送 `stream_options.include_usage`；缺 usage 允许回答成功但显示未知。
- `max_input_bytes` 必须1..CONTEXT_BYTES；每模型上下文以较小值为限。字节上限是保守资源控制，不宣称能精确换算每个模型的 Token 窗口。
- 不能原样传任意配置字段到上游。配置文件严格白名单/schema/大小验证，未知字段启动报错。
- capability_revision = 对当前模型有效配置 + 目录协议证据做规范JSON SHA256。每轮重新核对，变化要求用户刷新，不能在已提交时默默改变协议/参数。
- 已实现依赖专用 reasoning 的工具衔接与持久化：Chat 保存实际返回的 reasoning_content/reasoning；Responses 保留 reasoning/function items；Anthropic 原样保留 thinking/signature/redacted blocks。公开文本与不透明状态分开，签名/加密内容不是隐藏思考原文；当前 UI 行为见[前端契约](../ai-assistant-v2/ui.md)。不宣称所有渠道已真实验收。
- v2 能力文件允许显式 protocol、max_output_tokens、compaction 与按协议验证的 reasoning/thinking 参数；max_input_bytes 最多 2 MiB。v1 文件继续兼容原 64 KiB 限制。不能仅凭模型名启用工具或原生压缩。
- 普通聊天支持 1—4 候选；Chat 原生 n 为一次真实请求，Responses/Anthropic 在同一轮内最多四次有界请求。Agent 只允许单执行分支，候选选择落盘后才用于后续上下文。

普通模式不发送 tools/tool_choice；文件 Agent 的四个基础 schema 保持不变，显式联网增加三个，自适应上下文增加一个当前聊天原文检索 schema，总共最多八个；tool_choice=auto，不透传任意自定义工具。
工具数组相同，模型只能选用其中工具；前端不能自行定义工具或函数名称。
HTTP 4xx/5xx、无效SSE、参数不支持：一次失败后报告，不切协议、换KEY、换组或重发收费请求。

## 3. 上下文与运行顺序

### 3.1 构建上下文

1. 固定服务端安全/能力说明与本轮冻结的个人设置/显式 Skill。
2. 全部可重放语义历史；候选仅采用所选分支，原生连续状态仅在协议/模型/token ID/能力版本一致时附加。
3. 当前用户消息/附件摘要。文件正文仍需通过当前工作区工具读取。

新界面默认 `adaptive`：完整原文放得下且未到阈值时完整发送；约 75% 输入预算时压缩较早前缀，保留最近两个完整轮次与当前要求，保存覆盖/哈希。明确支持的 Responses 原生 compact 保存整个返回窗口，其他配置使用有界可读检查点。失败后不切换另一种压缩方式重复收费。
Agent 运行内后续调用前也可压缩完整旧工具组，保留当前用户意图与最新完整工具组；这类活动窗口单独标记，不冒充可复用的历史覆盖检查点。
兼容 `recent` 策略按预算取最近完整语义轮次且不调用压缩；两者都不裁 UI 存档。失败/中断的用户输入和安全部分文本明确标记不完整，绝不发送半个工具调用对。
必留内容、旧前缀或压缩输出超限时明确失败，不静默丢弃或无限调用。单轮压缩最多 3 次，摘要/原生压缩/Memory 辅助合计 4 次；主调用 6 次、总共 10 次、总截止 180 秒。
`run.accepted.context_from_run_ids` 与冻结个人快照保留来源；新建/复用检查点分别记录 `context.compacted/context.selected`。完整算法与事件见[工作上下文](../ai-assistant-v2/context.md)、[扩展契约](../ai-assistant-v2/contracts.md)。

### 3.2 接受一轮（同步预检）

`service.accept_run(access, cid, request) -> AcceptedRun`：

1. 验证owner/基础schema，锁外幂等快速返回；未命中则取得运行租约，**锁内再次查幂等**，再检查revision。锁内检查才有决定权。
2. 仅新请求检查本人KEY状态/模型目录、能力版本、mode、附件、个人上下文版本和Skill归属；重复已完成请求不依赖这些当前状态仍有效。
3. 冻结个人上下文和参数，申请磁盘预留，构建上下文并核对预算。
4. 保存快照和 run.accepted，fsync；无此持久承诺不得发收费请求。
5. 启动有界本轮生产者，HTTP消费者返回已提交事件与后续流。

预检不包含收费生成。任何失败释放租约/未用预留；accepted之前的孤立快照由恢复器处理。
凭据仅为请求内对象传入工作线程，不能从 Flask g 在线程里重新取，也不能存进 AcceptedRun 的可序列化部分。

### 3.3 有限循环（生产者）

```text
接受后先完成待压缩工作计划（若需要，单独辅助派发并记录用途/用量）
普通聊天：Chat n 一次主调用，或 Responses/Anthropic 有界逐候选主调用
文件 Agent：最多6次主调用，每次前检查停止/总截止/身份/KEY/输入/空间预算
    后续主调用前按需压缩完整旧工具组（压缩最多3次，辅助合计最多4次）
    持久化run.dispatching，再经黏性取消闸门单次HTTP，无重试
    解析本协议有界SSE；候选/正文/公开推理独立缓冲落盘
    完整结束后保存原生连续状态、assistant.message与实际返回usage
    工具只执行完整合法JSON，最多16次；每次结果完整落盘后加入后续上下文
    最后一个主调用仍要求工具则不执行该工具，以预算终态收尾
正常回答保存后，若本轮显式自动Memory，执行一次有界提取/意向/CAS/审计
日志健康时提交唯一run.terminal；poisoned日志只停止清理，恢复器补interrupted
所有步骤共享整轮180秒、主6/辅助4/总10派发预算，不后台续跑或读取时收费
```

正常 `stop` 才作为无工具回答完成。`length/content_filter` 等状态保留文本并给出明确未完整结束原因；不当作完整成功。
最后一次模型调用若含工具，记录其 assistant.message 后以预算终态结束，不执行工具，后续只保留标记不完整的安全文本语义，不重放残缺工具对。
未经证据的模型结果不当作工具执行成功。取消/超时不能回滚已经保存的输出文件，界面仍可下载。

## 4. 传输与停止的实现约束

- `transport.stream_model(...,protocol=...)` 是统一的单次 HTTP 迭代器，输出规范 text/reasoning/replay/tool_delta/finish/usage；旧 `stream_chat` 仅保留兼容接口，均不负责重试、持久化或权限。
- 连接仅允许配置的 New API 地址，禁止重定向、环境代理、转发浏览器Cookie/IP、记录Authorization。
- 流式UTF-8解码、SSE空行分帧、分块/多行data、`[DONE]`、usage-only尾块均有测试。Chat 支持严格编号的1—4候选、每个都必须完整结束；Responses/Anthropic 采用各自终止事件，非法候选/工具编号仍 fail-closed。
- Chat `[DONE]` 前必须每候选都有合法 finish；Responses 必须有完整 response 终态，Anthropic 必须有 message_stop 与合法 stop_reason；有 tool_calls 必须每个都有唯一ID、名字和可解析参数。普通模式收到tool_calls也拒绝执行。
- 统计响应总字节、frame/arguments/text大小，检查单调时钟总截止，不被持续小心跳绕过。

为避免一个阻塞上游把停止接口也堵住，采用**请求消费者 + 每运行一个生产线程 + 一个有界监护线程**：

- HTTP消费者从大小 QUEUE_EVENTS 的队列取已落盘事件，空闲发心跳；断开设 stop_event。
- 生产线程是本会话唯一写者，持有运行租约；不能创建无界线程池/离线队列。
- 监护线程每250ms检查 deadline、stop_event、该rid取消标志；通过 transport.abort 对活动socket执行 shutdown/close，使阻塞读退出。
- abort是黏性的：在与连接注册/发送共用的本地互斥闸门内设置取消标志；之后注册的新连接立即关闭，禁止发送。不能只关闭“当时已有socket”而遗漏认证查询结束后才创建的连接。
- 身份/KEY查询也用有界超时且占用总截止；停止可能要等待当前短查询收尾，202不是立即停止保证。
- 队列满超过1秒且消费者不前进，停止上游并保存中断，不无限积累结果。
- 工作线程finally收尾并释放锁；消费者退出不能提前释放收费容量。监护线程随生产线程结束 join，不遗留常驻轮次。
- SIGTERM 先拒绝新运行，设置各运行取消并关闭连接，等待短期收尾；不把普通 Gunicorn `--timeout` 当作 gthread 每请求硬截止。
- DNS/建连必须有界；若采用标准库HTTP，不能把 socket timeout 等同于 DNS 截止。实现使用可超时并终止的解析辅助进程（仅固定配置主机名、无KEY），或已有等价且经过阻塞DNS测试的实现；不得引入模型可执行的任意命令工具。必要辅助文件 `features/chat/resolver.py` 归本模块。
- 如果底层资源无法终止，保持租约、标记进程不健康，等待运维重启；不能放出新槽导致后台仍收费且新请求又开始。

## 5. 工具契约

文件工具作用域永远是当前会话。用户资料/Skill 是只读提示上下文，不是工具可访问的文件系统。
工具结果为 JSON，成功 `{ok:true,...}`，失败 `{ok:false,code,message}`；保存/回传前做 TOOL_RESULT_BYTES 校验。
错误文本是本地白名单中文，不包含宿主路径、数据库、系统异常堆栈。

下面是 function.parameters 的完整首版schema；工具外层固定 `type:function` 与对应 name/description。各对象 `additionalProperties:false`。

### 5.1 list_files

列举当前聊天已提交的文件，可选类型，无任意目录参数。由于文件最多200个，结果按结果字节上限分页。

```json
{
  "type":"object",
  "properties":{
    "kind":{"type":"string","enum":["all","upload","output"]},
    "offset":{"type":"integer","minimum":0,"maximum":200}
  },
  "required":["kind","offset"],
  "additionalProperties":false
}
```

返回 `{ok:true,files:[{file_id,name,kind,size_bytes,version}],next_offset:int|null,total:int}`。
排序按 file.added seq；最多50项且受结果字节限制。只读，不扫描 profiles/skills/其他聊天。

### 5.2 read_file

```json
{
  "type":"object",
  "properties":{
    "file_id":{"type":"string","format":"uuid"},
    "start_line":{"type":"integer","minimum":1},
    "max_lines":{"type":"integer","minimum":1,"maximum":200}
  },
  "required":["file_id","start_line","max_lines"],
  "additionalProperties":false
}
```

后端仍严格校验UUID v4，不能依赖模型遵守JSON Schema。
返回 `{ok:true,file_id,start_line,end_line,text,truncated,next_line:int|null}`；按解码后的行读取，结果转义后也不能超过 TOOL_RESULT_BYTES。
单行超结果上限不无限读取，返回 `TOOL_LINE_TOO_LONG`，建议用户上传分行版本；禁止偷偷截断一行后宣称完整。
超出EOF返回空text、end_line=start_line-1、next_line=null。

### 5.3 search_files

```json
{
  "type":"object",
  "properties":{
    "query":{"type":"string","minLength":1,"maxLength":200},
    "file_ids":{"type":"array","items":{"type":"string","format":"uuid"},"maxItems":20,"uniqueItems":true},
    "case_sensitive":{"type":"boolean"}
  },
  "required":["query","file_ids","case_sensitive"],
  "additionalProperties":false
}
```

空file_ids表示当前会话全部文件；否则只检索其中已授权fid。纯字面子串，不支持正则、shell、glob、跨文件系统索引。
返回 `{ok:true,matches:[{file_id,line,text}],truncated:bool,scanned_files:int,scanned_bytes:int}`。
按文件提交顺序/行号排序；达到扫描、结果字节、100命中或时间上限时 truncated=true。每条摘录最多500字符，不能把内容误当指令。

### 5.4 write_file

```json
{
  "type":"object",
  "properties":{
    "name":{"type":"string","minLength":1,"maxLength":120},
    "content":{"type":"string","maxLength":65536}
  },
  "required":["name","content"],
  "additionalProperties":false
}
```

另以UTF-8字节校验 TOOL_WRITE_BYTES，JSON maxLength不是字节限额。
name为展示logical_name，不能有目录、控制字符、路径特殊名；扩展名须在允许列表。每次创建新版本，不接受 overwrite/path/mode 等参数。
返回 `{ok:true,file_id,name,version,size_bytes,sha256,download_path}`，文件必须已提交后才报告成功。
上传的同名文件仍是原件；输出各版本独立；用户决定下载/使用，模型无权限覆盖源代码或配置。

### 5.5 文件类型与工具错误

允许扩展名（不区分大小写）：`.txt .md .csv .json .yaml .yml .toml .xml .log .py .js .ts .jsx .tsx .html .css .sql .sh`。
必须UTF-8，无NUL；允许去除一个UTF-8 BOM，记录实际保存字节的sha256。CSV/代码/XML只是文本，不解析执行、不跟随include、不加载资源。
ZIP/压缩包、二进制、PDF/Office/图片不支持；不能单靠客户端MIME判断。输出即使是HTML/脚本也仅attachment下载，不预览运行。

工具错误：`TOOL_INVALID_ARGUMENTS`、`TOOL_UNKNOWN`、`TOOL_FILE_NOT_FOUND`（含非本会话）、`TOOL_LINE_TOO_LONG`、`TOOL_RESULT_LIMIT`、`TOOL_STORAGE_LIMIT`、`TOOL_TIMEOUT`。
不允许越权后尝试其他目录，也不允许模型通过错误信息得知别的用户文件是否存在。

## 6. 部署修改（未来实现时）

继续同一镜像，新增 `chat` 服务；不新增数据库/Redis。Compose源构建版与发布版同时更新。

```yaml
chat:
  # image/build 沿用对应 Compose 的现有镜像方案
  env_file: .env
  environment:
    COCKPIT_PROCESS_ROLE: chat
    CHAT_DATA_DIR: /app/data/chat
  command: [gunicorn, --bind, "0.0.0.0:8000", --worker-class, gthread,
            --workers, "2", --threads, "8", --timeout, "210",
            --access-logfile, "-", "new_api_cockpit.app:app"]
  ports: ["${BIND_HOST:-127.0.0.1}:${CHAT_PORT:-8093}:8000"]
  volumes:
    - type: bind
      source: ./data/chat
      target: /app/data/chat
      bind:
        create_host_path: false
  read_only: true
  tmpfs: ["/tmp:size=64m,mode=1777"]
  mem_limit: ${CHAT_MEMORY:-1g}
  stop_grace_period: 30s
  cap_drop: [ALL]
  security_opt: ["no-new-privileges:true"]
  networks: [new-api]
```

上例是增量片段，不是完整可运行 Compose：实现时补 restart/healthcheck；能力文件存在时只读挂到 web/chat 的同一路径或由chat独占读取，前端不得直接取文件。
web 仅显示 CHAT_ENABLED 入口，不读个人数据；image 不挂聊天数据。即使共同 env_file 中有监控配置，chat也绝不能执行其初始化。
chat端口仅loopback，代理才公开；health不发模型请求，不看用户正文，只检查role/配置/数据目录可写/可用空间/运行监护状态。
Gunicorn chat graceful_timeout=20s；收到停止信号取消当前运行并收尾，不仿照 web 启动定时器。

Nginx 新增精确功能前缀（放在通用 `/cockpit/` 之前，不覆盖 `/api/` 或 `/v1/`）：

```nginx
location ^~ /cockpit/api/chat/ {
    proxy_pass http://127.0.0.1:8093;
    proxy_set_header Host $http_host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_http_version 1.1;
    proxy_set_header Connection "";
    client_max_body_size 51264k;
    client_body_timeout 30s;
    proxy_connect_timeout 5s;
    proxy_send_timeout 30s;
    proxy_read_timeout 210s;
    proxy_buffering off;
    proxy_cache off;
    gzip off;
}
```

应用仍按每路由执行更小的JSON/设置/附件限制，不以代理上限代替后端校验。页面 `/cockpit/chat/` 和静态资源仍走web。
`/cockpit/api/chat` 无尾斜杠不是有状态端点，可明确404；不要给POST做跨容器重定向。
把 `/data/` 加入根 `.gitignore`、`.dockerignore`；镜像构建测试断言不存在数据目录中的哨兵文件。
模板/静态新子目录加入 setuptools package-data，wheel安装与Docker都检查存在；不能只在源码目录运行成功。

## 7. 实现中的禁止项

不引入 LangChain/通用代理框架来替代此有限循环；不加shell/Python执行、浏览器或数据库工具；联网只允许本轮显式开启的受控公网工具。
不将历史/用户个人设置接入现有操作记录/监控数据库；不让chat引用生图Redis。
不开放静态 `data` 下载；不开放跨用户管理 API；不把Skill当安装脚本。
不做脱离用户运行的收费后台摘要/记忆任务，标题仍为本地生成。用户本轮启用的自适应压缩/自动 Memory 属于同一个有界运行，逐次记录用途和真实返回用量；读取和重启不自动调用模型。
