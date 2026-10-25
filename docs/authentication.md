# 登录与会话

## 用户体验

- 业务入口未登录时跳转 `/cockpit/login?next=...`，登录成功后返回原页面。
- 登录页先尝试复用同一浏览器、同一协议/域名/端口的 New API 登录；成功后返回原页面。
- 没有有效会话时显示用户名和密码表单。密码、动态验证码及恢复码直接发送给同源 New API 官方接口，不经过 Cockpit 密码验证或存储。
- 支持官方密码加密公钥协议及二次验证。站点启用人机验证、协议确认，或账号要求通行密钥、SSO 等流程时，使用页面上的官方登录入口；完成后返回 Cockpit。
- 侧栏显示当前用户及“退出登录”。普通用户显示模型使用、辅助工具、智力测试、生图工作站和本人令牌入口，管理导航仅对管理员显示。退出会撤销当前浏览器对应的 New API 会话，不影响其他设备的会话。
- 页面间切换账号或退出通过官方跨标签页事件同步；事件不携带凭据，也不能授予权限。不同会话的旧页面必须重新加载后才能继续操作。
- 启用的普通用户默认进入 `/cockpit/manual`；从模型使用、智力测试或生图工作站发起登录时返回原页面。其他管理页面和 API 仍要求管理员。访问管理功能被拒绝不会清除普通用户的有效会话。账号不可用时提供退出换账号入口。

## 会话链路

1. 浏览器向 `POST /api/user/auth/refresh` 发送 New API 自己的 HttpOnly 刷新 Cookie。该 Cookie 限定在 `/api/user/auth`，不能直接当作 `/cockpit` 的 Cookie 使用。
2. New API 校验其会话、刷新令牌及账号状态，返回短期访问 JWT。
3. 浏览器把访问 JWT 交给 `POST /cockpit/api/auth/session`。Cockpit 通过内部地址调用 `GET /api/user/self`，确认真实用户、会话和启用状态；前端传来的用户、角色不参与授权。
4. 验证成功后，将同一会话的访问凭据保存在 `cockpit_access` HttpOnly Cookie 中；路径为 `/cockpit`、SameSite=Strict，默认 Secure，最长保留 15 分钟且不超过上游 JWT 到期时间。
5. 每个会话请求再次交给 New API 校验会话和权限，不跨请求缓存认证结果。页面请求和下载使用该 Cookie，业务 API 也可携带官方浏览器 JWT Bearer。

普通用户允许访问辅助工具、登录后的插件下载、模型使用、智力测试、生图工作站及其 `/cockpit/api/manual/`、`/cockpit/api/intelligence/`、`/cockpit/api/image-studio/` 接口，以及会话接口和自动选择落地页的 `/cockpit`。模型使用仅查询本人令牌，智力测试仅创建或配置本人专用测试 KEY，生图工作站使用本人有生图权限的 KEY，不限定分组名称。其余路由默认要求 `role >= 10`，不是只隐藏菜单。所有访问都要求启用账号；新增管理接口不会自动继承普通用户权限。

New API 的 `user_sessions` 属于上游自身结构。Cockpit 登录不新增会话表、不读写 New API Redis、不共享 SESSION_SECRET，不读取密码哈希。生图工作站的独立 Redis 只保存短期派发回执，与登录无关。
访问 JWT 只短暂出现在内存和 HttpOnly Cookie；密码、验证码、JWT、PAT 不进入 URL、localStorage、sessionStorage、日志或监控库。
官方同步事件只包含会话 ID 和事件元信息，不包含令牌。

## 续期、并发与安全边界

- 前端在访问凭据临近到期时续期；同页共享续期任务，并使用与 New API 相同的 `new-api:auth-refresh` Web Lock 协调多个标签页。无 Web Lock 的浏览器依赖上游刷新竞争处理，最多短暂重试一次。
- 普通查询的 GET/HEAD 可以在认证失效后续期重试一次。可能补建 PAT 的用户/KEY 详情、写请求及文档调试都只发送一次，避免重复执行。
- 浏览器的业务 API 请求携带 `X-Cockpit-Session`；会话与当前 Cookie 不一致时返回 409，旧页面操作不执行。
- 写接口验证同源及各功能 JSON/自定义请求头。会话承接、清除也要求 JSON 和 `X-Cockpit-Auth: 1`；第三方表单不能注入登录或触发修改。
- 登录失效返回 401，账号不可用或权限不足返回 403，上游故障返回 503，限流保留 429 及有效的 `Retry-After`。临时故障和限流不清除已有 Cookie 或聊天草稿，不自动重发消息；不降级为本地密码或另一种凭据。
- 退出先调用官方 `POST /api/user/auth/logout`，成功后清除 Cockpit Cookie；调用失败时显示错误，供用户重试。
- `/cockpit/api/statistics/balance` 和 `/cockpit/api/statistics/alert` 保持独立的管理员 PAT Bearer 认证，不接受浏览器 Cookie 或 JWT。

## 脚本与管理员 PAT

业务接口统一在 `/cockpit/api/` 下；除私有聊天模块外，沿用已有 New API 管理员 PAT Bearer。
模型使用接口只能读取认证账号本人的令牌；智力测试只使用认证账号自己的测试 KEY；生图工作站只使用本人的生图 KEY。管理员 PAT 也不能在这些接口里指定另一用户。
PAT 每次先调用官方 `/api/user/self`，要求管理员账号启用。旧 PAT 只有在上游仍接受时才能使用；退役、撤销、过期或上游故障均不回退到 `users.access_token` 匹配。新式 `nap_` PAT 还按哈希和已验证用户读取 `user_access_tokens` 的权限、到期信息，并逐接口检查所需权限。认证结果不跨请求缓存，不接受模型 KEY。
权限、执行身份及审计来自对应管理员，不接受客户端指定操作者。参数、确认头与错误处理见 [API 参考](api.md)。

显式 Bearer 优先于 Cookie，无效时不回退到 Cookie，也不清除浏览器的另一份有效会话。
会话和 PAT 的有效性均由官方接口确认；源库读取只能进一步收窄新式 PAT 权限，不能替代上游验证。认证服务故障不会尝试其他凭据。
PAT 不用于打开 HTML 管理页面，不能通过 `/cockpit/api/auth/session` 换取浏览器 Cookie；
这不改变 New API 网页登录、二次验证和会话退出流程。
PAT 调用不携带 `X-Cockpit-Session`，JSON/确认头要求不变；服务器调用不需要 `Origin`。
前端遇到显式 Bearer 时单次直发，不续期页面会话，也不混入浏览器 Cookie 或会话身份。
API 文档页面通过浏览器登录打开；页面内调试可用当前会话或手动输入的 PAT，所有调试请求只发送一次。
带浏览器来源的写请求仍须同源，不提供跨域 CORS。PAT 与 JWT 均只通过 HTTPS 传递，不进入 URL 或日志。

## 用户账户 MCP

账户 MCP `/cockpit/mcp` 是独立例外，接受启用普通用户或管理员的本人 API PAT，但拒绝浏览器 Cookie/JWT 和模型 KEY。
不经过管理员 PAT 的附加权限查询，也不会让普通用户 PAT 获得任何现有管理 API 权限。
每次向 New API 验证 `profile:read`；查用量还执行 `usage:read`，源 SQL 在聚合前绑定已验证用户 ID。
KEY 列表、修改和完整密钥读取分别通过本人接口检查 `api_key:read`、`api_key:write`、`api_key:reveal`。MCP 不使用管理员代管或自动补建所属用户 PAT，不能操作他人 KEY。
具体约定、禁用开关和客户端配置见[账户 MCP](mcp.md)。

## 部署要求

- New API 与 Cockpit 必须使用同一浏览器入口；`/cockpit/` 转发到本应用，`/api/`、`/sign-in` 等路由由 New API 处理。
- `NEW_API_INTERNAL_URL` 指向可信的固定内部服务，仅用于后台验证及管理调用；拒绝认证 HTTP 重定向，不透传浏览器 Cookie 给其他地址。
- HTTPS 下保持 `COCKPIT_COOKIE_SECURE=true`。仅可信 HTTP 开发/内网调试可显式设为 `false`；正式部署不得关闭。
- 已核对 New API v1.0.0-rc.40 的 JWT 会话协议。接入其他版本时，需确认上述刷新、退出和用户信息接口一致，详见[兼容范围](compatibility.md)。
- 登录通过官方接口完成，无需数据库会话表或额外授权。源库 PAT 补建用于用户/KEY 管理，与网页登录分别处理。


## 私有聊天与个人上下文

AI助手启用后允许所有启用用户访问本人聊天与设置页。`/cockpit/api/chat/` 仅接受官方已验证会话Cookie/访问JWT，拒绝管理员PAT；不因管理员角色授予跨用户权限。
所有聊天API（包括GET/下载）强制匹配X-Cockpit-Session，写入另需同源和X-Cockpit-Chat:1。显式Bearer不回退Cookie，也不会尝试PAT源库认证。
正文、附件、运行状态、取消、MEMORY/SOUL/IDENTITY、Skill和历史快照均从当前验证身份的用户ID确定作用域，不接受客户端owner_id。
每次付费模型请求前重新验证固定的本次会话及本人KEY；账号变化/取消后不继续发起下一次收费请求。
账号切换会清空浏览器旧内容与Blob，丢弃晚到响应；历史仍按稳定用户ID存储，不绑定session或旧KEY。详情见[AI助手](chat.md)。
