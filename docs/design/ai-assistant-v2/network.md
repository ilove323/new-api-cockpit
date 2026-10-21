# N01–N03：有界联网工具

> 本文件说明新增 `web_tools` 模块的实际接口、安全边界和离线验证，不代表已集成到路由/UI、部署或通过真实搜索服务验收。未使用真实 KEY、付费调用或公网服务。主工具分发与运行循环由父任务接入；本模块不修改原 `tools.py`。

## 导出接口

模块：`new_api_cockpit.features.chat.web_tools`。

```python
WebSettings.from_env() -> WebSettings
schemas() -> list[dict]  # 恰好三个新的 OpenAI function tool schema
availability() -> dict  # 本地配置状态，不发送探测请求
execute(name, args, workspace, run_context) -> dict
```

`schemas()` 每次返回新的结构，不允许调用方修改共享 schema。工具名/参数：

| 名称 | 参数 | 行为 |
|---|---|---|
| `web_search` | `query: str`；可选 `max_results: int = 5` | SearXNG JSON 搜索，最多 5 条 |
| `http_fetch` | `url: str` | 公网 GET，UTF-8 文本或 HTML 可读正文 |
| `download_url` | `url: str, name: str` | 保存为当前 Workspace 的不可变文本输出版本 |

严格拒绝额外字段。不接受目标路径、后端地址、任意请求头、鉴权、Cookie、代理、HTTP 方法、shell 或浏览器参数。搜索词最多 500 字符，不得含控制字符；URL 最多 4096 UTF-8 字节；文件名复用现有 `tools._filename` 验证器。

`run_context` 可为 dict 或属性对象：

```python
{
    "run_id": "<当前运行 UUID>",
    "call_id": "<当前工具调用 ID，1–128 字符，字母/数字/_/->",
    "abort": shared_transport_AbortControl,
    "stop_event": shared_stop_event,
    "deadline": absolute_time_monotonic_deadline,
    "web_enabled": True,
}
```

必须显式为布尔 `True`；缺失、`1`、字符串 `"true"` 均拒绝且不联网。运行 ID、调用 ID 与有限数值截止时间在 I/O 前校验。`abort`、`stop_event` 由运行器提供并共享；没有 `abort` 时模块创建本地取消控制，不创建后台线程。`workspace` 必须是父运行器为当前用户/聊天构造的现有 Workspace；下载要求其 `run_id` 等于当前运行，并由 Workspace 再验证租约、活动运行、配额和文件作用域。

### 返回结构

- 抓取成功：`{ok: true, source_url, content_type, text, truncated, bytes_read, redirects, untrusted: true}`。`source_url` 为最终验证通过的规范 URL；`bytes_read` 是正文 UTF-8 解码前字节数，不是工具结果长度；`redirects` 为跟随次数。
- 搜索成功：`{ok: true, backend: "searxng", query, results: [{title, url, snippet}], truncated, untrusted: true}`。不返回后端地址、环境变量、密钥或其他供应商字段。标题最多 512 UTF-8 字节，摘要最多 2048 字节；条目 URL 进行语法/字面 IP 公网验证，但不在搜索结果整理时逐条发起 DNS/GET；之后真正抓取时重新执行完整网络检查。
- 下载成功：现有 `write_file` 的白名单结果 `{ok: true, file_id, name, version, size_bytes, sha256, download_path}`，外加 `{source_url, content_type, untrusted: true}`。下载不截断文件；超过写入上限直接失败，无输出文件。
- 普通失败：`{ok: false, code, message}`，固定中文消息；不附原始异常、响应错误正文、解析器消息、搜索配置或主机文件路径。主要错误码为 `WEB_DISABLED`、`WEB_INVALID_ARGUMENTS`、`WEB_URL_BLOCKED`、`WEB_DNS_ERROR`、`WEB_TIMEOUT`、`WEB_NETWORK_ERROR`、`WEB_HTTP_ERROR`、`WEB_REDIRECT_ERROR`、`WEB_RESPONSE_LIMIT`、`WEB_CONTENT_INVALID`、`WEB_DOWNLOAD_LIMIT`、`WEB_SEARCH_UNAVAILABLE`、`WEB_CONFIG_INVALID`、`WEB_WORKSPACE_ERROR`。
- **运行控制异常不是可恢复工具失败**：与现有文件工具一致，取消/总截止以清理过消息的 `ChatError(code="CHAT_CANCELLED"/"CHAT_DEADLINE")` 交给父运行器终止。`CHAT_STORAGE_*` 异常同样清理消息后抛出，避免存储损坏被误报为普通网页错误。不会自动重试。

所有普通返回的完整 JSON（包含来源、元数据、转义开销）最多 `min(32768, config.TOOL_RESULT_BYTES)` 字节；抓取正文按完整 UTF-8 字符截断并标记。网页/搜索输出始终标记 `untrusted`，不能当作系统指令或用户亲述事实，也不得自动写入长期 MEMORY。

## 运维配置：只支持 SearXNG

未配置搜索后端时，抓取/下载仍可用；`web_search` 返回 `WEB_SEARCH_UNAVAILABLE`。启用搜索需要两个服务端环境变量：

```dotenv
CHAT_WEB_SEARCH_BACKEND=searxng
CHAT_WEB_SEARXNG_URL=https://search.example.net/search
```

这里只是占位配置，不是已经存在或验证过的服务。端点填写 **完整 search 路径**，无 query、fragment、userinfo，且必须可从服务端通过标准 80/443 端口访问公网。模块追加 `q=<编码搜索词>&format=json`，GET 请求，要求后端开启 SearXNG JSON 格式并返回 `{results: [{url, title, content}, ...]}`。后端 JSON 不可用、返回 HTML、错误状态或超时均明确失败，不回退网页抓取/其他供应商。

本实现没有 Tavily/Brave、没有独立搜索 KEY，也不读取/存储/转发用户 New API KEY。私网 SearXNG、凭据保护端点和非标准端口有意不支持；不要为部署便利放宽公共网络策略。若后续加入受信任私网后端，需要独立配置与单独传输边界，不得复用用户可控 URL 的例外。

`WebSettings` 是冻结数据对象，字段为 `search_backend`（空字符串或 `searxng`）、`searxng_url`；URL 不进入对象 repr。仅按需从环境读取，不落盘、不写聊天历史。无配置时返回空设置，配置不合法时抛出模块内部固定错误，`availability` 与 `execute` 将其转换为公开状态/安全失败。

`availability()` 仅返回以下非秘密状态：

```json
{
  "http_fetch": true,
  "download_url": true,
  "web_search": false,
  "search_backend": null,
  "search_status": "not_configured"
}
```

另外两种 `search_status` 为 `configured`（此时 `web_search=true, search_backend="searxng"`）和 `invalid_configuration`。这些字段表示实现/配置可用性，**不是当前运行已授权联网，也不是后端连通性保证**。父运行器仍须按本人运行快照的显式开关决定是否将 schema 提供给模型，并传入 `web_enabled=True`。

## URL / SSRF / DNS / TLS

1. 只接受 `http`、`https` GET。只允许本协议默认端口（HTTP 80、HTTPS 443），拒绝非规范端口写法、userinfo、片段、反斜线、控制字符及坏百分号编码。国际化主机规范为 IDNA，路径/查询正确转义；请求 Host 与 TLS 主机名来自原始验证后的主机，不使用数值 IP 代替 TLS 身份。
2. `ipaddress` 全局单播检查加显式特殊用途网段拒绝，覆盖私网、回环、CGNAT、链路本地、未指定、文档、基准测试、组播、保留地址、元数据地址；对 Python 版本的特殊网段例外采取更保守策略。IPv4-mapped IPv6 一律拒绝（即使映射的是公网），拒绝 IPv6 zone、NAT64/兼容地址、6to4/Teredo/其他转换地址，IPv6 仅接受通过检查的 `2000::/3` 全局单播。额外拒绝 Azure `168.63.129.16`、localhost、`.local` 和已知元数据主机名。
3. DNS 使用固定 Python `-I -c` 子进程；不使用 shell、线程池或现有只取第一条答案的 resolver。子进程仅接收主机与端口，空环境、无 stdin、丢弃 stderr。获取 **完整** `getaddrinfo` 结果；超过 32 条直接失败，绝不先截断再检查。逐条验证 family、TCP 类型、端口、数值地址、IPv6 scope；**任意一条非公网则整组拒绝**。
4. DNS 最多 3 秒且受共享停止/总截止约束，每 50ms 检查。退出路径均 kill（如仍存活）、communicate 回收子进程并关闭管道。没有在运行结束后遗留的不可杀 DNS 工作线程。DNS 输出最多 8192 字节。
5. 全部地址通过后仅选第一条，用对应 socket family 与 **数值 sockaddr** 连接；不调用 `create_connection`、不二次解析、不为失败地址自动重试。HTTPS 使用 `ssl.create_default_context()` 和原主机 `server_hostname`，保持系统信任链与主机名校验。
6. 301/302/303/307/308 最多跟随 3 次；每跳重新检查 URL、完整 DNS 答案并固定新连接，拒绝循环、缺失 Location、HTTPS→HTTP 降级。搜索后端重定向额外限制为同协议/同主机/同端口；不会向第三方搜索端点转发查询。
7. 仅发送固定 Host、Accept、Accept-Encoding: identity、固定 User-Agent、Connection: close；不用环境代理、Cookie jar、认证头、Referer、浏览器状态或业务凭据。Set-Cookie 不保存、不转发。重定向立即关闭原连接，不读取无限重定向正文。

## 时间、大小、内容与落盘

| 项目 | 上限 |
|---|---|
| 单工具总时间 | 15 秒，且不晚于共享运行截止 |
| DNS | 3 秒，可取消并回收进程 |
| TCP 连接 + TLS 握手 | 每跳合计 5 秒，受总截止限制 |
| 写请求/读响应空闲 | 5 秒；覆盖握手后请求、状态行、响应头、chunk framing、正文 |
| 重定向 | 3 次，最多 4 个 GET，无自动重试 |
| HTTP 头 | 每响应最多 32 KiB 原始读取预算；缓冲可能包含部分正文，边界保守拒绝 |
| HTTP 原始数据 | 整次工具所有跳累计最多 2 MiB，**含响应头与 chunk framing** |
| 解码后正文 | 最多 2 MiB；仅 identity，不进行压缩解码，拒绝 gzip/br 等 |
| 完整工具 JSON | 32 KiB 或更小的现有工具结果上限 |
| 下载输出 | 现有 `config.TOOL_WRITE_BYTES`，目前 64 KiB |

HTTP 状态必须为 200（或上述有限重定向）。拒绝重复/冲突 Content-Length、Transfer-Encoding 与长度并用、未知传输/内容编码、坏 chunk、提前 EOF、坏 UTF-8 和 NUL。支持正常 chunked，但 framing 本身也占原始预算，防止很多小 chunk 绕过正文上限。内容声明为 UTF-8 或未声明；非 UTF-8 charset 拒绝，不猜编码。

抓取支持 `text/*` 及限定 JSON/XML/YAML/TOML/JavaScript/SQL 文本媒体类型。HTML 使用标准库解析器提取可读文本，去掉 head/script/style/nav/header/footer/aside/form/template/svg/iframe 等噪声与隐藏节点，解实体，最大嵌套 256 层；不执行脚本、不加载子资源。它不是浏览器级渲染/内容可信性证明。下载额外允许 `application/octet-stream`，但仍须完整 UTF-8、无 NUL、文件扩展名为现有惰性文本白名单，不支持 PDF/Office/压缩包/可执行二进制。

下载只调用当前 `Workspace.write_file` 写入，不接触任意目标路径，不执行内容。已有 `.sh/.py/.js/.html` 只是工作区已有允许的**惰性文本**，不扩大允许的扩展名，不新增执行能力。HTML 下载保留原始 UTF-8 文本；仅抓取工具做可读正文清理。

### 与 Workspace 的外层调用 ID、持久化幂等及协议配对

使用父任务扩展后的 Workspace 接口，直接复用模型发起的外层 `download_url` 调用 ID：

```python
workspace.write_file(
    args["name"], downloaded_text, original_call_id,
    source_tool="download_url",
    source_arguments={"url": args["url"], "name": args["name"]},
)
```

`source_arguments.url` 必须是工具原始参数 URL（不是规范化/重定向后的地址），与父运行器已经持久化的外层调用严格一致。文件来源 URL 则在工具结果返回最终验证过的地址。`tool.call` 只记录原始 URL 与文件名，**不记录下载正文**。

Workspace/recovery 将文件关联到同一外层 run/call；相同调用 ID/参数重入返回已保存文件，不创建第二版本，不覆盖原内容。直接调用模块、此前尚无对应 `tool.call` 时，Workspace 可补记同一外层 `download_url` 调用，并验证总工具计数边界；已存在外层调用时不再增加工具计数，最后一个合法工具调用也可下载。参数改变则由 Workspace 拒绝。单次重入仍可能重新 GET（没有隐式重试/缓存），但不会用新响应覆盖已存在的文件；父层不得自动重放有副作用工具。

**不生成 `web_download_*` 内部 ID、不增加内部 `write_file` 事件、不额外扣除工具预算。** 模型 assistant tool_calls、持久化 tool.call、下载文件关联和外层 tool.result 全部使用同一个 call_id，保持完整轮次重放的工具配对连续性。父层负责更新 Workspace/recovery；联网模块没有修改它们。

停止与截止在 DNS、connect、TLS、send/read、解析、写入前后检查；所有 socket 注册到共享 AbortControl，退出统一关闭并注销。同步磁盘事务使用 Workspace 的既有租约/锁/配额机制，不用超时线程模拟“取消写入成功”。

## 离线验证与未验证范围

执行：

```text
PYTHONPATH=src:tests .venv/bin/python -m unittest discover -s tests -p test_chat_web_tools_v2.py -v
.venv/bin/ruff check src/new_api_cockpit/features/chat/web_tools.py tests/test_chat_web_tools_v2.py
```

测试只使用合成 DNS 答案、隔离 DNS 子进程、本地 socket 与临时 ChatStore/Workspace。测试适配器把已经验证的公网数值 connect 目标路由到随机本地 fixture 端口，验证固定 IP 和实际 HTTP 行为；**生产没有私网允许开关，也没有测试端口例外**。

覆盖特殊地址/元数据/IPv4-mapped、混合 DNS、数值连接固定、同域重定向 rebinding、三跳限制、HTTPS 降级、真实本地 TLS 的信任链/主机名/SNI 校验、无凭据请求头、取消/截止/空闲/连接超时、DNS 杀进程回收、原始与解码大小限制、chunked framing、UTF-8/HTML 清理、完整结果字节预算、搜索配置/摘要限额、真实工作区下载同外层调用幂等/单次工具计数/不覆盖/跨用户跨聊天拒绝和停止前不落盘。

真实 SearXNG 连通与 JSON 配置、真实公网证书链、生产出口防火墙、三协议运行器和 UI 的端到端集成不由这些离线测试证明，需父任务或正式部署阶段另验。

独立复审补充：分块传输由有界严格帧解码器验证，统一规范化 Transfer-Encoding 尾随空白；必须检查十六进制 chunk-size、数据 CRLF、零终止块及完整 trailer 结束。非法/截断分块不返回成功、不落盘，避免 Python 标准宽松解码接受不完整前缀或把分块标记保存成正文。
