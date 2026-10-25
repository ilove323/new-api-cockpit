# 账户 MCP：本人余额与用量

入口：`https://你的站点/cockpit/mcp`。在 **文档 → 辅助工具 → 账户 MCP** 第 2 步可复制当前站点地址和配置示例；同页还提供 VS Code 插件下载，见[辅助工具](tools.md)。

只提供两个只读工具，不调用模型，不充值、不修改额度或令牌，不返回提示词、完整日志、模型 KEY 或其他账户。
普通用户可用；管理员 PAT 也只能查询该 PAT 所属账户，不能指定其他用户。

## 1. 准备用户 API 访问令牌

在 New API 的 `/security`（安全与访问）中，创建本人用户 API 访问令牌（PAT），选择最小权限：

- `profile:read`：身份、余额、累计消费及请求数。
- `usage:read`：本人消费用量；仅查余额时可不授予此权限。

设置适当到期时间，凭据仅放在自己的受信任客户端或启动环境。
新式 PAT 通常以 `nap_` 开头。**不要使用 `/keys` 中的 `sk-` 模型调用 KEY，也不要使用浏览器登录 JWT、账号密码或 Cookie。**

MCP 不代建 PAT，不自动提升权限，也不读取 `users.access_token` 来绕过 New API 校验。
新式 PAT 的作用域、过期/撤销、账号停用，以及旧 PAT 的上游退役期限均由 New API 在每次请求时执行。
该适配按 New API `v1.0.0-rc.46` 的 Bearer 身份及 `profile:read` / `usage:read` 路由核对；旧版本缺少这些接口/认证方式时应先评估升级。

## 2. 配置客户端

只想在 VS Code 中看余额，可以直接安装 [Cockpit 账户与 KEY 插件](../vsix/README.md)，不需要配置本节的 MCP 客户端。余额调用现有账户入口，PAT 只需 `profile:read`；默认每 15 分钟更新，手动至少间隔 60 秒，429 时按服务端要求延长等待。插件另有独立 KEY 管理分区，直接调用同站点的 New API 本人接口，需要对应 `api_key:*` 权限；KEY 不随余额定时刷新，已读取的数据保留缓存，手动查询不设时间间隔，每次修改前后各查一次。MCP 本身仍只有下述只读工具。

服务使用 Streamable HTTP，响应为 JSON，无状态、无 SSE 长连接，不需要粘性会话。
支持 `2025-03-26`、`2025-06-18`、`2025-11-25` 版本的 initialize 生命周期；现代客户端可回退到此生命周期。
这里只提供静态 Bearer PAT 认证，不实现 OAuth 发现/登录；客户端必须支持自定义凭据请求头。
只支持 OAuth 的连接器不能直接使用下面的静态 PAT 配置。

### Codex

将以下表合并到本机 `config.toml`，不要重复添加同名表：

```toml
[mcp_servers.newapi_account]
url = "https://gateway.example.com/cockpit/mcp"
bearer_token_env_var = "NEW_API_USER_PAT"
enabled_tools = ["get_balance", "get_usage"]
```

将地址换成自己的站点。交互输入 PAT，避免把明文写入 shell 历史：

```sh
# macOS / Linux，Bash / Zsh
printf '请输入用户 API PAT: '; read -r -s NEW_API_USER_PAT; printf '\n'; export NEW_API_USER_PAT
codex
```

```powershell
# Windows PowerShell
$env:NEW_API_USER_PAT = [System.Net.NetworkCredential]::new('', (Read-Host '用户 API PAT' -AsSecureString)).Password
codex
```

从该终端启动 Codex 后确认 MCP 工具已连接；不要把 `NEW_API_USER_PAT` 设成模型 KEY。
它与模型接入的 `auth.json` / `OPENAI_API_KEY` 完全分开。
桌面应用/IDE 必须在实际启动进程的受信任环境中取得该变量，已打开的进程不会自动继承终端新变量。
参见 [Codex 官方 MCP 配置](https://learn.chatgpt.com/docs/extend/mcp)。

### Cursor 或支持相同远程配置格式的客户端

将以下对象合并到客户端自己的 MCP 配置中，地址改成实际站点，手动替换 `USER_API_TOKEN`：

```json
{
  "mcpServers": {
    "newapi_account": {
      "url": "https://gateway.example.com/cockpit/mcp",
      "headers": {
        "Authorization": "Bearer USER_API_TOKEN"
      }
    }
  }
}
```

此文件会包含明文 PAT，只允许本人读取，不加入仓库、不共享。推荐使用客户端支持的环境变量或密钥库能力。
保存后重连 MCP，确认出现 `get_balance`、`get_usage`。
不同客户端的配置目录/字段可能不同，不能把此 JSON 当作所有客户端通用的导入文件。
Cursor 默认使用本人 `~/.cursor/mcp.json`，不要把包含 PAT 的项目级 `.cursor/mcp.json` 提交到仓库。
也支持把请求头值改为 `Bearer ${env:NEW_API_USER_PAT}` 并通过实际启动进程环境提供该变量，参见 [Cursor 官方 MCP 配置](https://cursor.com/docs/mcp)。

## 3. 使用工具

### get_balance

参数为空对象 `{}`。返回本人 `user.id` / `username`、`remaining_quota`、`used_quota`、
`remaining_amount`、`used_amount` 和 New API 的累计 `request_count`。

示例提问：“我的账户余额还剩多少？”“我累计用了多少额度？”

### get_usage

不传参数时，查询**北京时间今天零点到现在**。
指定区间必须同时提供 `start` 与 `end`，最长 31 天，开始时刻包含、结束时刻不包含，不能查询未来。
日期代表北京时间零点；完整时间必须带时区并精确到秒。

```json
{"start": "2026-10-01", "end": "2026-10-02"}
```

查询 10 月 1 日一整天。若要查指定小时，可使用：

```json
{"start": "2026-10-01T09:00:00+08:00", "end": "2026-10-01T18:00:00+08:00"}
```

返回 `totals` 和按消费金额排序、最多 20 个模型的 `models`：请求数、输入/输出、缓存读/写、去重总 Token 与消费金额。
总计始终涵盖全部模型，不因列表上限而遗漏；`models_may_be_truncated` 表示模型列表可能受到上限限制。
只读取 `type=2` 消费日志；时间、缓存归一化和实际消费金额复用 Cockpit 统计 SQL，不重新计算账单。

**金额口径**：原始 `quota` 是整数，`amount` 等金额字段为十进制字符串，换算为 `quota / 500000`，并返回 `quota_per_unit`。
这是当前 Cockpit 的账面单位，不自动换算成 USD/CNY，也不套用客户端自己的汇率。
站点采用不同 quota 单位或自定义货币时应先核对兼容性，不直接把数值当作任意法币金额。

## 4. 部署与安全

- 随 Cockpit 网页池提供，不开新端口。现有 `/cockpit/` 反向代理会把 `/cockpit/mcp` 送到网页池；图片/聊天池拒绝此端点。
- `COCKPIT_MCP_ENABLED=true` 默认启用；设置为 `false` 后重建网页服务可关闭。完整安装写在 `config/web.env`；已有实例写在实际注入网页池的环境配置。
- 远程部署使用 HTTPS。不要记录 Authorization、Cookie、请求体或带凭据的 URL；已有自定义请求日志须核对脱敏策略。
- 默认 Gunicorn 访问日志只记录 URL 路径，不记录查询串或认证头；自定义启动方式及反向代理仍需遵守上述边界。
- 不接受 URL 查询参数、工具参数中的 PAT/KEY/用户 ID、任意上游地址。只信任已配置 `NEW_API_INTERNAL_URL` 的本人 GET 接口；不使用环境代理，不跟随重定向，不返回上游错误正文。
- 每条请求都通过 `/api/user/self` 校验 PAT。查询用量先通过 `/api/log/self/stat` 执行 `usage:read` 与上游日志范围策略检查，再在只读数据库连接中、**源查询聚合之前**强制绑定已验证用户 ID。
- 即使请求头/参数伪造 `New-Api-User`、用户名或管理员身份，也不会用它选择用户；不得以管理员令牌或全站查询作为失败回退。
- 无 MCP 会话、身份缓存、凭据表、后台任务或新业务表。上游可以正常记录访问审计和 PAT 使用时间；本组件不修改业务配额。
- 单条 JSON-RPC，请求体最多 16 KiB；拒绝批处理，跨源 Origin 返回 403；不启用跨域 CORS。
- 只使用两项工具，禁用模型采样、写操作、任意代码执行和第三方服务调用。

## 5. 常见错误

| 现象 | 处理 |
| --- | --- |
| HTTP 401 | 用本人有效 PAT；确认不是模型 KEY、JWT 或 Cookie，且 PAT 未过期/撤销 |
| HTTP 403 | 余额至少需要 profile:read，用量还需 usage:read；检查账号状态与请求来源 |
| tools/call 的 isError=true | 上游/只读数据库暂不可查询；不填旧数据、不绕过权限，也不会自动重试 |
| 时间参数无效 / JSON-RPC -32602 | 同时提供起止时间，最长 31 天、结束不含，不传用户 ID 或任意附加参数 |
| HTTP 404 | 功能关闭、网页池未更新，或误连到图片/聊天请求池 |
| GET / DELETE 405 | 正常：此实现仅通过 HTTP POST 处理 MCP 请求，不提供独立 SSE 会话 |
| 未识别的 server/discover | 这是 2025 生命周期服务；客户端应回退到 initialize 协商支持的版本 |

低层调用须使用 `Content-Type: application/json` 和 `Accept: application/json, text/event-stream`。
不能用浏览器打开地址是否显示页面来判断 MCP 是否正常；应在兼容客户端列工具并查询本人账户。

协议参考：[Streamable HTTP](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports)、[工具与只读标注](https://modelcontextprotocol.io/specification/2025-11-25/server/tools)。
