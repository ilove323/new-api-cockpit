# API 参考

Cockpit 的业务接口统一放在 `/cockpit/api/`，与页面共用站点入口。
报表、渠道质量、智力测试、模型使用、生图工作站、用户、令牌、配额、规则和操作记录接口支持 **New API 管理员 PAT** 调用。
网页通过 New API 登录会话调用同一套接口；模型使用、智力测试、生图工作站和启用后的 AI 助手向所有启用用户开放，其余管理功能要求管理员。
**AI 助手 `/cockpit/api/chat/` 是例外：仅接受本人登录会话/访问JWT，不支持管理员PAT；所有读取也强制 `X-Cockpit-Session`，管理员没有跨用户权限。**

- 页面地址见 [README](../README.md#功能)。
- API：`/cockpit/api/` 下的 `statistics`、`quality`、`intelligence`、`manual`、`image-studio`、`chat`、`users`、`keys`、`operations` 模块。
- 独立余额和报警：`/cockpit/api/statistics/balance`、`/cockpit/api/statistics/alert`。
- New API 自己的 `/api/...` 不属于本项目，仍由 New API 处理。

登录后在 `/cockpit/docs/` 可查看交互式文档并试调用，使用方法见[交互式文档](interactive-api.md)。
`GET /cockpit/api/openapi.json` 返回 OpenAPI 3.1 描述，接受管理员 PAT 或管理员浏览器会话，不访问业务数据。

Nginx 转发完整 `/cockpit/` 前缀，覆盖页面、API 和静态资源。

## 认证与调用约定

### 管理员 PAT

每次请求携带：

```http
Authorization: Bearer <New API 管理员 PAT>
Accept: application/json
```

PAT 由 New API `/api/user/self` 验证，要求账号 `role >= 10`、`status = 1`；上游拒绝旧 PAT 后不再通过源库放行。新式 `nap_` PAT 需要 `profile:read` 和相应功能权限：报表/质量为 `log:read`；用户读取/修改为 `user:read` / `user:write`；跨用户 KEY 管理还需 `api_key:read` / `api_key:write`，查看完整 KEY 另需 `api_key:reveal`；账本读取需 `channel:read` 与 `log:read`，修改配置另需 `option:write`。未知接口默认拒绝新式 PAT；完整逐接口映射在 `pat_permissions.py`。可能补建目标 PAT 的管理详情接口需要 `user:write`，不能用只读凭据触发。
**不是模型调用 KEY（`tokens.key`）**，不添加或剥离 `sk-` 前缀；原值里的 `+`、`/`、`=` 都保留。
PAT 每次从数据库核对，不缓存认证结果；撤销、换 PAT、停用或降权在后续请求生效。
PAT 不放在 URL、请求体或 Cookie，也不需要写入 Cockpit 的 `.env`。

不需要 `New-Api-User`；传入该头或请求体中的操作者名字不能切换身份。
权限及审计操作者来自 PAT 对应的真实管理员。公开 API 不代表匿名可访问，也不提升普通管理员权限。

业务 API 也接受有效的 New API 浏览器会话 Cookie 或访问 JWT Bearer；
显式 Bearer 优先于 Cookie，凭据无效时不会回退到另一个登录身份。
统计模块的 `/cockpit/api/statistics/balance`、`/cockpit/api/statistics/alert` 仅接受管理员 PAT。
会话桥接 `/cockpit/api/auth/session` 用于网页登录，不接受 PAT 换取网页登录 Cookie。
`/cockpit/api/manual/`、`/cockpit/api/intelligence/`、`/cockpit/api/image-studio/` 接口允许启用的普通用户会话或访问 JWT，仅针对当前账号；使用管理员 PAT 时也不能指定其他用户。
不开放跨域 CORS；服务器、脚本直接调用不需要 `Origin`。浏览器写请求的来源必须与站点一致。

下文示例假定调用环境已安全注入 `ADMIN_PAT`，只需设置站点地址：

```bash
BASE_URL='https://example.com'
curl --fail-with-body "$BASE_URL/cockpit/api/users" \
  -H "Authorization: Bearer $ADMIN_PAT" -H 'Accept: application/json'
```

### 写请求与确认头

所有 JSON 请求使用 `Content-Type: application/json`。此外按功能携带下列头，PAT 调用也不能省略：

| 功能 | 额外请求头 |
|---|---|
| 用户资料、密码、用户组；KEY 查询、单个操作、批量预览及执行 | `X-Management-Action: confirm` |
| 用户额度预览 | `X-Quota-Action: preview` |
| 用户额度执行 | `X-Quota-Action: confirm` |
| 定时规则新增、编辑、启停、删除 | `X-Quota-Action: schedule` |
| 报表详情、余额设置、历史追溯、手工检查、通知设置与测试 | `X-Statistics-Request: 1` |
| 浏览器会话承接、清除 | `X-Cockpit-Auth: 1` |
| 智力测试发起、JSON HTML 预览 | `X-Intelligence-Request: 1` |
| 模型使用：按令牌读取模型、读取 KEY | `X-Cockpit-Manual: 1` |
| 生图工作站：模型目录、生成、编辑 | `X-Cockpit-Studio: 1` |

确认头只是防误操作和跨站表单的校验，**不表示幂等**。
用户/KEY/额度写操作必须配置监控库保存审计，未配置时不能执行。
浏览器请求另外携带 `X-Cockpit-Session` 防止旧页面误操作；PAT 调用不需要该头。
图片编辑使用 `multipart/form-data`，不是 JSON；浏览器发送 FormData 时由浏览器生成 Content-Type 和 boundary。
生成/编辑属于真实计费写操作，交互式客户端须明确确认且不得自动重放；文档调试器不开放这两个接口的试运行。

### 数据类型与错误

- 请求金额建议用十进制字符串，如 `"100.00"`，避免客户端浮点误差。
- 报表、额度、设置、归档等 Decimal 金额返回为字符串，单位为元；`/balance`、`/alert` 的金额为 JSON number。
- 原始 `quota` 单位为整数，`500000` 单位 = `1` 元。API 不按表格的两位小数截断金额。
- 带日期时间的响应为 ISO 8601；余额截止时间为北京时间 `+08:00`。客户端不要假定所有数据库时间都使用同一偏移。
- 成功通常为 HTTP 200，新增定时规则为 201。各接口返回自己的业务对象，不统一包装成 `code/data`；只有 `/balance` 使用该包装。
- 失败主要为 `{"error":"说明"}`。认证失败另有 `code`、`login_url`；API 不跳转到 HTML 登录页。

```json
{"error":"请提供有效的 New API 管理员 PAT。","code":"AUTH_REQUIRED","login_url":"/cockpit/login?next=..."}
```

| HTTP 状态 | 含义与处理 |
|---|---|
| 400 | 参数、金额、字段或业务条件不合法；检查错误信息 |
| 401 | 缺少或无效凭据；PAT 账号不符合管理员条件时也返回此状态。响应含 `WWW-Authenticate: Bearer` |
| 403 | 当前功能要求管理员、账号不可用、无目标操作权限，或缺少确认头/来源不匹配 |
| 404 / 405 | 路径不存在 / 方法不支持 |
| 409 | 设置版本、预览、会话或执行状态冲突；重新读取并核对，不要盲目重试 |
| 410 | 报表详情快照过期或不属于当前账号/账本；重新查询列表 |
| 413 | 请求体超过接口的大小上限，包括会话桥接、测试参数和预览 HTML |
| 502 | 上游写请求结果不明确，可能已经生效；部分接口含 `uncertain: true` |
| 503 | 数据库、认证服务或必要表结构不可用，或余额数据不可用 |
| 504 | 数据库查询超时；缩小统计区间 |

认证故障不会降级到其他认证方式；PAT 请求失败不会清除浏览器的已有登录 Cookie。
**写请求断连、超时或返回结果不明确时，先查实际数据和操作记录，不自动重发。**
批量返回 HTTP 200 也可能部分失败，必须检查逐项结果与完成标记。

## 接口目录

`{user_id}`、`{key_id}`、`{rule_id}` 是整数，`{operation_id}` 是 UUID。
除专门说明的创建入口外，ID 应为正整数。GET 自动支持 HEAD；OPTIONS 不提供跨域授权。

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/cockpit/api/openapi.json` | 下载 OpenAPI 描述 |
| `GET` | `/cockpit/api/statistics/balance` | 实时余额，不报警 |
| `GET` | `/cockpit/api/statistics/alert` | 即时检查并按配置发送报警 |
| `GET` | `/cockpit/api/statistics/scopes` | 渠道标签账本 |
| `GET` | `/cockpit/api/statistics/usage` | 用户模型用量、合计、排名 |
| `GET` | `/cockpit/api/statistics/usage/by-token` | 按令牌拆分用量 |
| `GET` | `/cockpit/api/statistics/usage/tokens` | 区间令牌选项 |
| `GET` | `/cockpit/api/statistics/usage/groups` | 区间日志分组选项 |
| `GET` | `/cockpit/api/statistics/usage/by-selection` | 按令牌/日志分组筛选用量 |
| `POST` | `/cockpit/api/statistics/usage/details` | 读取不可变公式详情 |
| `GET` | `/cockpit/api/statistics/export` | 下载 Excel |
| `GET` | `/cockpit/api/statistics/balance/status` | 设置、状态、报警及月度归档 |
| `PUT` | `/cockpit/api/statistics/balance/settings` | 保存当前账本设置 |
| `GET` | `/cockpit/api/statistics/balance/usage-channels` | 当前账本的渠道库存 |
| `POST` | `/cockpit/api/statistics/balance/check` | 手工余额检查与报警 |
| `POST` | `/cockpit/api/statistics/balance/recalculate-history/preview` | 追溯预览 |
| `POST` | `/cockpit/api/statistics/balance/recalculate-history` | 确认重建归档 |
| `GET` | `/cockpit/api/statistics/balance/channel` | 读取指定通知渠道的配置与状态 |
| `PUT` | `/cockpit/api/statistics/balance/channel` | 保存指定通知渠道的配置与开关 |
| `POST` | `/cockpit/api/statistics/balance/channel/test` | 发送测试通知 |
| `GET` | `/cockpit/api/quality/summary` | 渠道质量、性能健康与筛选选项 |
| `GET` | `/cockpit/api/quality/trends` | 渠道质量、小时趋势和历史价格 |
| `GET` | `/cockpit/api/intelligence/models` | 可测试模型及自动分组 |
| `POST` | `/cockpit/api/intelligence/test` | 同步鹈鹕骑车测试，真实计费 |
| `POST` | `/cockpit/api/intelligence/preview` | 当前 HTML 的沙箱预览，不调用模型 |
| `GET` | `/cockpit/api/image-studio/tokens` | 本人 KEY 脱敏摘要 |
| `POST` | `/cockpit/api/image-studio/models` | 所选 KEY 的模型、操作能力与参数，不生成内容 |
| `POST` | `/cockpit/api/image-studio/generations` | 同步文生图，真实计费 |
| `POST` | `/cockpit/api/image-studio/edits` | multipart 图片编辑，真实计费 |
| `GET` | `/cockpit/api/users` | 用户与配额列表 |
| `GET` | `/cockpit/api/users/{user_id}` | 用户资料详情 |
| `GET` | `/cockpit/api/users/{user_id}/groups` | 用户可切换的组 |
| `POST` | `/cockpit/api/users/{user_id}/action` | 新增、编辑、密码、组、状态及删除 |
| `POST` | `/cockpit/api/users/quota/preview` | 用户额度增减预览 |
| `POST` | `/cockpit/api/users/quota/apply` | 用户额度原子增减 |
| `GET` | `/cockpit/api/users/schedules` | 定时规则及用户组 |
| `POST` | `/cockpit/api/users/schedules` | 新增定时规则 |
| `PUT` | `/cockpit/api/users/schedules/{rule_id}` | 编辑定时规则 |
| `PATCH` | `/cockpit/api/users/schedules/{rule_id}` | 启停定时规则 |
| `DELETE` | `/cockpit/api/users/schedules/{rule_id}` | 软删除定时规则 |
| `GET` | `/cockpit/api/keys/options` | 分组选项、操作者及审计可用性 |
| `POST` | `/cockpit/api/keys/query/grouped` | 按用户分页的 KEY 列表 |
| `GET` | `/cockpit/api/keys/{key_id}` | KEY 配置详情，不含明文 |
| `GET` | `/cockpit/api/keys/{key_id}/groups` | KEY 可切换的组 |
| `POST` | `/cockpit/api/keys/{key_id}/action` | KEY 新增、编辑、额度、组、查看、状态及删除 |
| `POST` | `/cockpit/api/keys/groups/preview` | 冻结批量改组名单 |
| `POST` | `/cockpit/api/keys/operations/{operation_id}/apply` | 执行一组，最多 5 个 KEY |
| `GET` | `/cockpit/api/operations` | 已发起操作列表 |
| `GET` | `/cockpit/api/operations/{source}/{record_id}` | 操作逐项详情 |
| `GET` | `/cockpit/api/auth/session` | 当前浏览器会话身份 |
| `POST` | `/cockpit/api/auth/session` | 承接 New API 访问 JWT |
| `DELETE` | `/cockpit/api/auth/session` | 清除 Cockpit 浏览器 Cookie |

## 实时余额与报警

### GET /cockpit/api/statistics/balance

可选 `scope_id`，省略为全部账本（ID 1），无效 ID 不回退至全部。
这是上游账本预算，不是个人用户余额或某个模型调用 KEY 的额度。

```bash
curl --fail-with-body "$BASE_URL/cockpit/api/statistics/balance?scope_id=3" \
  -H "Authorization: Bearer $ADMIN_PAT" -H 'Accept: application/json'
```

```json
{
  "code": 0,
  "data": {
    "site": "示例网关", "currency": "CNY",
    "scope": {"id": 3, "kind": "tag", "tag_value": "comlan"},
    "total_quota": 10000.0, "used_quota": 7500.0, "remaining_quota": 2500.0,
    "alert_threshold": 2000.0, "usage_percent": 75.0,
    "checked_at": "2026-10-09T10:00:00+08:00"
  }
}
```

| 字段 | 含义 |
|---|---|
| site / currency | 站点名 / `CNY` |
| scope | 仅显式传 `scope_id` 时返回账本标识 |
| total_quota | 账本设置的总额度 |
| used_quota | 起始月份以来已归档费用 + 本月实时费用 |
| remaining_quota | 总额度减累计费用，允许负数 |
| alert_threshold | 当前账本报警阈值 |
| usage_percent | 已用百分比，四舍五入到两位小数；总额度为零时为 null，超支不截断至 100 |
| checked_at | 本次计算截止时间，ISO 8601、`+08:00` |

每次重新计算本月消费，历史按逐渠道归档及渠道当前归属汇总；不受页面时间或用户筛选影响。
计算时可以同步渠道库存、补齐缺失月度归档，但不写报警记录、不发送通知，也不改变定时检查安排。
未配置监控库或结果不可用返回 HTTP 503、`{"code":503,"message":"余额数据暂不可用。"}`，不以零冒充余额。
建议 5～10 分钟轮询一次，多个调用方错峰，避免并发重复统计；没有计算结果缓存。

### GET /cockpit/api/statistics/alert

可选 `scope_id`。**有副作用**：执行即时检查、更新报警记录，符合阈值和开关条件时发送全局通知渠道消息。
不要把这个接口当作无副作用的轮询查询。仅查余额用同级的 `/cockpit/api/statistics/balance`。

```bash
curl --fail-with-body "$BASE_URL/cockpit/api/statistics/alert?scope_id=3" \
  -H "Authorization: Bearer $ADMIN_PAT"
```

返回 `{"has_alert":false,"alert":null}`，或 `{"has_alert":true,"alert":{...}}`。
`alert` 含 `title`、`scope_id`、`scope_name`、`site_name`、`budget`、`spent`、`remaining`、
`threshold`、`currency`、`checked_at`、`timezone`；金额为 number，时间带时区，`timezone` 为 `Asia/Shanghai`。
显式选择账本时响应顶层也带 `scope`。并发检查冲突返回 409。
剩余额度严格小于阈值且账本监控启用时记录余额报警，并分别发送到所有已启用的通知渠道。
关闭账本监控后不发送该账本余额报警；关闭某个通知渠道不影响其他已启用渠道。
各渠道的发送状态通过通知配置接口查询，HTTP 200 不等于消息平台已送达。

## 统计、筛选与导出

### 账本列表

`GET /cockpit/api/statistics/scopes` 返回：

```json
{"rows":[{"id":1,"kind":"all","tag_value":""},{"id":2,"kind":"ungrouped","tag_value":""},{"id":3,"kind":"tag","tag_value":"comlan"}]}
```

`all` 为全部，`ungrouped` 为未分组，`tag` 用 `tag_value` 显示名称。
只展示 New API 当前存在的标签；隐藏账本的设置和历史不删除。未配置监控库时只返回全部。
统计、导出、余额设置与归档接口通过查询参数 `scope_id` 选择账本；不用于选择用户组或 KEY 调用分组。

### 用量列表

公共参数：

| 参数 | 类型 | 规则 |
|---|---|---|
| start / end | string，必填 | 北京时间日期、分钟或秒，例如 `2026-10-01`、`2026-10-09T10:30:00`；不接收带偏移时间 |
| scope_id | integer，可选 | 省略为全部账本 |
| include_failures | `1`，可选 | 增加失败次数和错误码；不改变日志消费金额；默认不查询失败日志 |
| details | `lazy`，可选 | 以快照按需读取公式；省略时返回完整详情 |

日期结束边界包含当天最后一秒；有时分秒的 `end` 也包含该秒，单次最多 367 天。
参数值经 URL 编码；接口不分页，较大区间应拆分查询。

- `GET /cockpit/api/statistics/usage`：按用户、模型及请求发生时价格聚合，返回 `scope`、`start`、`end`、`rows`、`totals`、`rankings`、`updated_at`。
- `GET /cockpit/api/statistics/usage/by-token`：额外按令牌 ID 分行，返回 `scope`、`start`、`end`、`rows`。
- 上面两个接口可额外传单个 `user`、`model`，分别精确匹配用户名和模型名，不是模糊搜索或数组。
- `GET /cockpit/api/statistics/usage/tokens`：返回该区间用到的 `token_id`、最近 `token_name` 选项；不是完整 KEY 或密钥列表。
- `GET /cockpit/api/statistics/usage/groups`：返回该区间日志中的 `group_name`；不是渠道标签账本。
- 两个选项接口都返回 `scope`、`start`、`end`、`rows`，支持 `include_failures=1`，不接受用户/模型二次筛选。

```bash
curl --fail-with-body --get "$BASE_URL/cockpit/api/statistics/usage" \
  -H "Authorization: Bearer $ADMIN_PAT" \
  --data-urlencode 'start=2026-10-01' --data-urlencode 'end=2026-10-09T10:30:00' \
  --data-urlencode 'scope_id=3' --data-urlencode 'user=alice' --data-urlencode 'include_failures=1'
```

主要行字段：

| 字段 | 类型与含义 |
|---|---|
| user_id / username / display_name | 用户 ID、用户名、显示名 |
| model_name / tier_name | 模型及匹配当前价格的档位名；不匹配时档位为 `-` |
| token_id / token_name | 分令牌时为实际 ID/名称，汇总时为 0/空字符串 |
| request_count | 消费请求数，不含失败请求 |
| total_tokens / input_tokens / output_tokens / cache_read_tokens / cache_write_tokens | 展示口径用量；无法确认时可为 null，不以零替代损坏的元数据 |
| group_ratio | 请求日志记录的分组倍率，十进制字符串；无法确认时可为 null |
| input_price / output_price / cache_price / write_price | 请求价格，元/百万 Token，字符串或 null；缓存写按 5 分钟档 |
| amount | 日志实际消费，十进制字符串，不用当前价格覆盖历史账单 |
| failure_count / failure_codes | 勾选失败查询时提供；例如 `3` / `{"429":2,"502":1}`，无法解析错误码为 `未知` |
| cost_formula | 完整公式对象，含 `calculated`、`actual`、`difference` 等详情；lazy 模式通过详情接口取 |

相同用户、模型、价格、日志分组倍率（以及分令牌模式的令牌）合并，不同价格或倍率分别成行。
所有日期的 Token 用量取日志统计值，倍率不随当前分组配置改变。
缺少可靠历史价格时单价可能为空，金额仍保留；完整口径见[统计口径](calculation.md)。
同一模型分成多条价格行时，不要把同组失败次数在每行重复累加。

`totals` 含 Token 各项、金额、消费请求数、用户数、模型数、区间秒数、平均 TPM/RPM；
`rankings` 含 `model_amount`、`user_tokens`、`user_amount`。用量不可确认的合计可能为 null。
前端的模型汇总及多选用户/模型是展示行为，不是这个接口的多值参数。

### 按令牌、日志分组筛选

`GET /cockpit/api/statistics/usage/by-selection` 使用相同时间/账本参数，另外支持：

- `token_id`：可重复，非负整数，最多 500 个值；`0` 用于历史无有效令牌 ID 的记录。
- `group`：可重复，精确匹配日志分组，单值最长 128 字符，最多 500 个值；空值匹配空分组。
- 至少传一种筛选；两种同时传时取交集，各自多值之间取并集。
- `by_token=1` 按令牌拆行；省略仍按令牌过滤，只是不拆分令牌列。
- 支持 `include_failures=1`、`details=lazy`。不额外按 `user`、`model` 筛选。

```bash
curl --fail-with-body --get "$BASE_URL/cockpit/api/statistics/usage/by-selection" \
  -H "Authorization: Bearer $ADMIN_PAT" \
  --data-urlencode 'start=2026-10-01' --data-urlencode 'end=2026-10-09' \
  --data-urlencode 'token_id=10' --data-urlencode 'token_id=11' --data-urlencode 'group=default' \
  --data-urlencode 'by_token=1'
```

### 公式详情

配置监控库后，`details=lazy` 从行中移除 `cost_formula`、`tier_usage`、`pricing_buckets`、`price_tiers`，
增加 `report_id`、`row_id`、`has_pricing_buckets`、`price_tier_count`。
未配置监控库时仍返回完整详情，不产生快照。

`POST /cockpit/api/statistics/usage/details`，带 `X-Statistics-Request: 1`：

```json
{"report_id":"00000000-0000-4000-8000-000000000001","row_ids":[0,1]}
```

必须使用原查询账本的 `scope_id`、相同管理员身份；`row_ids` 为 1～500 个非负整数，最大 2147483647。
返回 `{"rows":[{"row_id":0,"detail":{"cost_formula":{...}}}]}`。
详情来自查询时的不可变结果，不重新查日志或当前价格；有效期 15 分钟，每账号最近 10 份，失效返回 410。

### Excel

`GET /cockpit/api/statistics/export` 使用 `start`、`end`、可选 `scope_id`、单个 `user`、`model`，
返回 XLSX 附件，不返回 JSON。导出消费汇总，不支持令牌/日志分组筛选、分令牌或失败次数导出。

```bash
curl --fail-with-body --get "$BASE_URL/cockpit/api/statistics/export" \
  -H "Authorization: Bearer $ADMIN_PAT" --data-urlencode 'start=2026-10-01' \
  --data-urlencode 'end=2026-10-09' -o usage.xlsx
```

导出独立实时查询，不复用页面快照；金额精度和工作表口径见[统计口径](calculation.md)。
失败时是 JSON 错误，`--fail-with-body` 可避免把错误当作成功下载。

## 预算、归档与通知设置

以下账本相关接口支持 `scope_id`；通知配置与测试是全局的，不随账本改变。
写请求使用 `X-Statistics-Request: 1`。

### 状态、渠道与检查

`GET /cockpit/api/statistics/balance/status` 返回 `configured`、`scope`、`settings`、`state`、
`months`、`alerts`、`valid`、`stale`。未配置监控库时仅返回 `{"configured":false}`。
默认读取最近检查状态；加 `live=1` 实时计算余额，不发送通知。
`valid` 表示状态版本/月份有效，`stale` 表示过旧或异常；不要把无效状态当作真实零余额。

- `settings`：`enabled`、`budget`、`threshold`、`start_month`、`version` 等。月份在响应中为 `YYYY-MM-01`。
- `state`：`current_month`、`current_amount`、`archived_amount`、`remaining`、`checked_at`、`last_error` 等，可能为 null。
- `months`：每月 `month`、`amount` 等归档字段；金额按渠道当前归属汇总，原始逐渠道费用一直保留。
- `alerts`：当前账本保存的报警记录，不是全站操作记录。

`GET /cockpit/api/statistics/balance/usage-channels` 返回 `rows`，每项含 `channel_id`、
`channel_name`、`channel_status`、`deleted`；已删除渠道状态为 null，仍保留原 ID/名称及归属。
这用于渠道库存展示，不提供删除原始费用的操作。

`POST /cockpit/api/statistics/balance/check`，请求 `{}`，执行即时检查并按配置通知，
随后返回与 `/balance/status` 相同的对象；检查占用返回 409。

### 保存余额设置

`PUT /cockpit/api/statistics/balance/settings`：

```bash
curl --fail-with-body -X PUT "$BASE_URL/cockpit/api/statistics/balance/settings?scope_id=3" \
  -H "Authorization: Bearer $ADMIN_PAT" -H 'Content-Type: application/json' \
  -H 'X-Statistics-Request: 1' \
  --data '{"enabled":true,"budget":"10000.00","threshold":"2000.00","start_month":"2026-01","version":1}'
```

所有示例中的版本号都应替换为刚读取的实际值。
`enabled` 为布尔值；金额非负、最多两位小数、最大 999999999999；`start_month` 为 `YYYY-MM`，
范围从 1970-01 至本月；`version` 为当前设置正整数版本。成功返回 `{"saved":true}`，版本冲突 409。
关闭监控会清除当前账本报警，不删除费用；保存可以补齐缺失归档，但不等于重建已有历史。

### 追溯历史计费

1. `POST /cockpit/api/statistics/balance/recalculate-history/preview`：请求体与保存余额设置一致。
2. 核对返回 `rows` 的逐月 `before`、`after`。响应另含 `version`、`scope_id`、
   `rebuild_scope: "all"`、`affects_all_scopes: true`、`warning`。
3. `POST /cockpit/api/statistics/balance/recalculate-history`：提交原设置及原预览行：

```json
{
  "settings":{"enabled":true,"budget":"10000.00","threshold":"2000.00","start_month":"2026-01","version":1},
  "preview":[{"month":"2026-01","before":"120.00","after":"125.00"}]
}
```

`preview` 必须是前一步返回的**完整 rows 数组**，不是整个响应对象；这里仅展示一个月的结构。
成功返回 `{"recalculated":true,"version":2,"months":1}`。会同时保存这次设置并递增版本。
预览后数据/版本变化、没有可追溯数据或重算总额少于已有归档基线，返回 409，原归档不覆盖。
预览行无效为 400。不能恢复 New API 已删除的日志。
**重建的是这些月份所有渠道的原始归档，会影响所有账本，不仅查询参数选择的账本。**

### 全局通知渠道

`GET /cockpit/api/statistics/balance/channel?channel=email` 读取指定渠道。
`channel` 可取 `feishu_app`、`dingtalk_webhook`、`email`，省略时默认为 `feishu_app`。
选择只读取配置。各渠道独立启停，可以同时启用；全体账本共用配置，不受 `scope_id` 影响。
响应公共字段为 `version`、`enabled`、`channel`、`last_attempt_at`、`last_success_at`、`last_error`，均针对所选渠道。
凭据仅返回是否已配置，不返回明文或密文。

`PUT /cockpit/api/statistics/balance/channel` 公共字段为 `version`（所选渠道当前整数版本）、
`enabled`（布尔值）、`channel`；使用 `X-Statistics-Request: 1`。
只修改该渠道的开关与配置，不关闭或覆盖其他渠道；同一渠道版本冲突返回 409。

| channel | 提供方字段 | 响应中的凭据标记 |
|---|---|---|
| feishu_app | `app_id`、`receive_id_type`（`chat_id` 或 `user_id`）、`receive_id`、`app_secret` | `secret_configured` |
| dingtalk_webhook | `webhook_url`、`signing_enabled`（必填布尔值）、`signing_secret` | `webhook_configured`、`signing_secret_configured` |
| email | 见下表 | `password_configured` |

飞书字段单值最多 512 字符；钉钉 URL 最多 2048 字符，签名密钥最多 512 字符。
凭据字段留空保持原值；飞书改变 App ID 不提供新 Secret 会清除旧 Secret。
启用时必须通过提供方完整配置校验。成功返回所选渠道的新脱敏配置与版本号。

邮件参数：

| 字段 | 类型 / 约束 |
|---|---|
| smtp_host | string；主机名或 IP，最长 253，不含协议、路径或端口 |
| smtp_port | integer；1～65535，必填；通常 SMTP 25、STARTTLS 587、SMTPS 465 |
| smtp_security | `smtp`（不加密）、`starttls`（必须升级 TLS）、`smtps`（连接即 TLS），必填 |
| auth_enabled | boolean，必填；false 时不发起认证 |
| username | string，最长 512；启用认证时必填 |
| password | string，最长 1024；认证时为密码或授权码，留空保留已保存值 |
| from_address / from_name | string；发件邮箱最长 254，名称最长 128（可空），不允许控制字符 |
| recipients | ASCII 邮箱字符串数组，最多 100 个；启用渠道时至少一个，重复地址去重 |

邮件响应还包含上述非密码字段。密码加密保存；改变 SMTP 主机、端口、加密方式或用户名时不复用旧密码，
认证模式启用时需重新提供。匿名模式不解密或发送已保存密码。
TLS 始终验证证书与主机名，不降级；普通 SMTP 可能明文传输，推荐 STARTTLS / SMTPS。

```json
{
  "version":1,"channel":"email","enabled":true,
  "smtp_host":"smtp.example.com","smtp_port":465,"smtp_security":"smtps",
  "auth_enabled":true,"username":"alerts@example.com","password":"<邮箱授权码>",
  "from_address":"alerts@example.com","from_name":"余额监控",
  "recipients":["ops@example.com","finance@example.com"]
}
```

匿名发信使用 `auth_enabled:false`，`username`、`password` 可为空；邮件服务器需要允许该来源中继。

`POST /cockpit/api/statistics/balance/channel/test` 必须传 `{"channel":"email","version":2}`，
携带同一确认头，只使用指定渠道的已保存配置发送测试消息，不测试其他渠道。
即使该渠道自动通知关闭仍可测试；成功 `{"sent":true}`，版本冲突 409，参数或配置问题 400。
自动余额报警向所有已启用渠道分发；失败独立记录，不影响其他渠道或已保存的账务结果。
邮件部分收件人被拒绝或超时可能已经部分投递，不要盲目重发。配置及排错见[通知渠道](notifications.md)。

## 用户管理与账户配额

### 列表、资料与分组

`GET /cockpit/api/users` 返回所有未删除用户，无分页、无服务端状态筛选；页面默认仅展示启用用户是前端筛选。
调用方自行筛选 `status=1` 启用、`2` 禁用。返回 `{"rows":[...]}`，每项含：
`id`、`username`、`display_name`、`user_group`、`role`、`status`、`remark`、`token_count`、
`quota`（原始整数）、`quota_yuan`、`used_quota_yuan`。不含密码、PAT 或 KEY。

`GET /cockpit/api/users/{user_id}` 返回用户资料：`id`、`username`、`display_name`、`group`、
`remark`、`status`、`role`、`available_groups`。详情经官方接口读取，管理员缺 PAT 时可能补建。
用户快捷改组应使用下面的组接口，其中组来自全局 `GroupRatio`，不要混用 KEY 的可用组。

`GET /cockpit/api/users/{user_id}/groups` 返回 `id`、`username`、`group`、`available_groups`、`persistence`。
只读源库，不触发 PAT 补建；`persistence` 表示是否配置了写操作所需监控库，不保证数据库一定健康。

### 单个用户操作

`POST /cockpit/api/users/{user_id}/action`，要求 `X-Management-Action: confirm`。
统一请求体 `{"action":"...","changes":{...}}`，成功返回
`{"ok":true,"operation_id":"<UUID>","result":null}`。同级/更高权限用户受原有管理限制，超级管理员例外。

| action | changes | 规则 |
|---|---|---|
| create | `username`、可选 `display_name`、`password` | **路径使用 `/users/0/action`**；仅超级管理员，创建普通用户，不允许指定角色或额度 |
| edit | `username`、`display_name`、`group`、`remark` 的变更部分 | 只允许这些文本字段；用户名/显示名最多 20 字符，备注 255；不能覆盖额度 |
| group | 仅 `group` | 必须在全局可选用户组中，当前组冲突返回 409，不改变启禁状态 |
| password | `password`、`password_confirm` | 8～72 UTF-8 字节，必须一致；不读取旧密码 |
| enable / disable / delete | `{}` | 不能禁用/删除超级管理员；删除遵循官方接口语义 |

```bash
curl --fail-with-body -X POST "$BASE_URL/cockpit/api/users/23/action" \
  -H "Authorization: Bearer $ADMIN_PAT" -H 'Content-Type: application/json' \
  -H 'X-Management-Action: confirm' --data '{"action":"group","changes":{"group":"default"}}'
```

用户组改变不会覆盖固定分组的 KEY；跟随用户组的 KEY 按 New API 规则使用新用户组。
密码不写审计；不要在终端日志或请求录制中泄漏密码。

### 用户额度预览和执行

`POST /cockpit/api/users/quota/preview` 与 `POST /cockpit/api/users/quota/apply` 使用同一请求结构：

```json
{"user_ids":[23,24],"mode":"add","amount_yuan":"100.00"}
```

| 字段 | 约束 |
|---|---|
| user_ids | 非空、互不重复的正整数数组，按每组最多 5 人执行 |
| mode | `add` 或 `subtract`，不能覆盖设置 |
| amount_yuan | **每人**金额，`0 < 金额 <= 1000000000`，必须精确换算为整数额度单位 |

普通管理员只能调整低于自己角色的用户；超级管理员可调整全站用户。
减少后允许负余额；接口不按页面的启禁筛选替你过滤 `user_ids`，调用前必须核对名单。

预览头为 `X-Quota-Action: preview`，返回 `mode`、`amount_yuan`、`users`；每项含
`id`、`username`、`before_yuan`、`estimated_after_yuan`。预览不执行、不冻结余额，也不生成可执行凭据。

执行头为 `X-Quota-Action: confirm`，服务端重新核对权限并调用 New API 原子增减接口，
**不是把预览值覆盖回数据库**。无需提交预览响应，但调用方应先展示并确认名单/金额。

```bash
curl --fail-with-body -X POST "$BASE_URL/cockpit/api/users/quota/apply" \
  -H "Authorization: Bearer $ADMIN_PAT" -H 'Content-Type: application/json' \
  -H 'X-Quota-Action: confirm' --data '{"user_ids":[23,24],"mode":"add","amount_yuan":"100.00"}'
```

响应示例：

```json
{
  "operation_id":"00000000-0000-4000-8000-000000000001", "mode":"add", "amount_yuan":"100.00",
  "results":[{"id":23,"username":"alice","ok":true,"state":"success"}],
  "completed":true, "remaining_user_ids":[]
}
```

这个响应示例对应只提交用户 23 的请求。每项含 `ok`、`state=success|failed|unknown`，错误项另有 `message`。
每组最多 5 人并发，收齐结果并持久化后再发下一组；出现任何失败/不明确即停止后续组。
`remaining_user_ids` 是未发起名单，不应当作失败项自动重试；成功项不回滚。
大批量建议调用方也按最多 5 人一组提交，减少长连接超时。接口无幂等键，重复执行会重复增减。

### 定时规则

`GET /cockpit/api/users/schedules` 返回 `configured`、`rows`、`groups`、`timezone`，配置可用时另有
`current_user_id`、`current_username`。规则包含 `id`、`enabled`、`period`、`operation`、`amount_yuan`、
`groups`、`version`、`executor_user_id`、`executor_username`、`next_run_at`、`last_status`、`can_edit`、`missing_groups` 等。
列表可见规则不代表可修改；普通管理员只能修改自己的执行规则，超级管理员可管理全部。

`POST /cockpit/api/users/schedules` 新增，`PUT /cockpit/api/users/schedules/{rule_id}` 编辑：

```json
{"enabled":true,"period":"monthly","operation":"add","amount_yuan":"100.00","groups":["default"],"version":1}
```

`enabled` 为布尔值；`period=daily|weekly|monthly`；`operation=add|subtract`；金额规则同手工额度。
`groups` 是非空、不重复的用户组数组，最多 1000 个，单组最多 256 字符；不是 KEY 分组或渠道标签。
新增不需要 `version`，返回 HTTP 201；编辑必须提供当前版本。成功对象为 `id`、`next_run_at`。

- `PATCH /cockpit/api/users/schedules/{rule_id}`：`{"enabled":false,"version":1}`。
- `DELETE /cockpit/api/users/schedules/{rule_id}`：`{"version":1}`，返回 `{"deleted":true}`，保留历史执行记录。
- 所有写请求携带 `X-Quota-Action: schedule`，包括 DELETE 的 JSON 请求体。版本冲突 409。
- 保存或重新启用绑定当前实际管理员，不允许请求体冒充执行人；启用时要求目标组当前可用。
- 保存不立即执行：北京时间每天 00:00、每周一 00:00、每月1日 00:00，只处理组内启用且未删除用户。
- 执行名单在到期时读取，不在保存时冻结；错过窗口不补发，网络不明确不重试。

完整调度及停用语义见[用户配额](quota.md)。

## 令牌（KEY）管理

所有令牌操作先验证实际管理员对所属用户的权限，再以所属用户 PAT 调用官方接口。
缺 PAT 时才经受限函数补建；不覆盖已有 PAT、不临时启用禁用用户。
查询列表/组菜单不补建；详情和实际操作可能补建，补建也记录目标用户。
安装及权限要求见[用户与令牌](users.md#安装补建函数与最小权限)。

### 选项及合并列表

`GET /cockpit/api/keys/options` 返回 `groups`、`operator: {id,username,role}`、`persistence`。
`groups` 为全局/已使用分组合集，**不能直接当作任意 KEY 的允许目标组**，改单个 KEY 前读其组接口。

`POST /cockpit/api/keys/query/grouped` 要求 `X-Management-Action: confirm`，只查询，不执行修改。
请求可为空对象，筛选字段如下：

| 字段 | 默认值与约束 |
|---|---|
| user_statuses | `[1]`；可选启用 `1`、禁用 `2`，空数组不匹配任何用户 |
| token_statuses | `[1,2,3,4]`；启用、禁用、过期、额度耗尽，按有效状态计算，不只读持久化状态 |
| user_groups / token_groups | 可选字符串数组，精确匹配，单值最长 64 字符；省略/null 不限制，空数组不匹配 |
| user_id | 可选正整数，仅查某个用户 |
| search | 最长 200 字符；按字面、不区分大小写匹配用户名、显示名、用户/KEY ID、KEY 名称；不匹配 KEY 明文 |
| page / page_size | 默认 1 / 50，正整数；page_size 最大按 100 处理，**按用户分页** |

```bash
curl --fail-with-body -X POST "$BASE_URL/cockpit/api/keys/query/grouped" \
  -H "Authorization: Bearer $ADMIN_PAT" -H 'Content-Type: application/json' \
  -H 'X-Management-Action: confirm' \
  --data '{"user_statuses":[1],"token_groups":["default"],"page":1,"page_size":50}'
```

返回 `rows`、`total`（匹配用户数）、`total_keys`（全部匹配 KEY 数）、`page`、`page_size`。
`rows` 每项为用户资料/配额及 `tokens` 子数组，同一用户不拆页，只有拥有匹配 KEY 的用户入列。
用户字段包括 `id`、`username`、`display_name`、`user_group`、`role`、`status`、`remark`、
`quota`、`used_quota`、`quota_yuan`、`used_quota_yuan`、`token_count`。
每个 KEY 含 `id`、`user_id`、`name`、`masked_key`、`token_group`、`user_group`、`user_status`、
`status`、`effective_status`、创建/访问/过期 Unix 秒数、原始及元单位剩余/已用额度、模型限制、IP 限制等。
列表不含完整 KEY。密码和 PAT 不出现在任何列表。

### 详情和可选组

`GET /cockpit/api/keys/{key_id}` 返回官方 KEY 配置的允许字段：`id`、`user_id`、`name`、`status`、
`expired_time`、`remain_quota`、`remain_quota_yuan`、`unlimited_quota`、`model_limits_enabled`、
`model_limits`、`allow_ips`、`group`、`cross_group_retry`、`auto_groups`、`available_groups`，不含明文 KEY。

`GET /cockpit/api/keys/{key_id}/groups` 返回 `id`、`group`、`user_status`、`available_groups`；
只读源库，不调用上游或补建 PAT。空组 `""` 表示跟随用户组，`auto` 表示自动分组，
其他组必须在所属用户的可用组范围内。

### 单个 KEY 操作

`POST /cockpit/api/keys/{key_id}/action`，`X-Management-Action: confirm`，
请求 `{"action":"...","changes":{...}}`，成功返回 `ok`、`operation_id`、`result`。

| action | changes | 说明 |
|---|---|---|
| create | 下表创建字段 | **此时路径整数是所属用户 ID，不是 KEY ID**；例如 `/cockpit/api/keys/23/action` 为用户 23 创建 KEY |
| edit | name、expired_time、model_limits_enabled、model_limits、allow_ips、group、cross_group_retry、auto_groups 的变更部分 | 保留未变字段，不允许改归属、KEY 值或 used_quota |
| group | `group` | 切至非 auto 时清空自动分组范围和跨组重试 |
| quota | `mode`、按需 `amount_yuan` | mode 为 `set`、`add`、`subtract`、`unlimited`；不是用户充值 |
| reveal | `{}` | 明确查看完整 KEY，`result` 为 `{"key":"..."}`；其他 action 的 result 为 null |
| enable / disable / delete | `{}` | 按官方接口启停/删除，不绕过所属用户状态 |

创建字段：`name`、`group`（默认空）、`expired_time`（默认 -1）、`amount_yuan`（默认 0）、
`unlimited_quota`、`model_limits_enabled`、`model_limits`、`allow_ips`、`cross_group_retry`、`auto_groups`。
不允许指定 `key`、`user_id` 或原始 `remain_quota`；归属由路径给出。

- 名称最多 50 UTF-8 字节；文本限制字段最长 10000 字符；组最长 64 字符且必须可用。
- 到期时间为整数 Unix 秒，`-1` 永不过期；布尔开关必须传 JSON true/false，不传字符串。
- `model_limits` 为英文逗号分隔模型；`allow_ips` 为换行分隔 IP；`auto_groups` 为字符串数组。
- 有限额度 `set` 允许零且关闭无限开关；`add/subtract` 不允许直接对无限 KEY 使用，结果不能小于零。
- KEY 金额必须精确换算为整数 quota，除允许零外范围同用户额度。`unlimited` 不需要金额。
- 新增成功不保证官方接口返回新 ID/KEY；需要再查询列表，查看明文仍需单独 `reveal`。

```bash
curl --fail-with-body -X POST "$BASE_URL/cockpit/api/keys/10/action" \
  -H "Authorization: Bearer $ADMIN_PAT" -H 'Content-Type: application/json' \
  -H 'X-Management-Action: confirm' --data '{"action":"group","changes":{"group":"default"}}'
```

**KEY PUT 是覆盖配置及剩余额度，不是与推理扣费之间的原子增减。**
改组、编辑或调整额度前先暂停目标 KEY 的并发调用；服务端重读配置仍不能消除读写之间的扣费竞争。
不会把 KEY 限额当作用户充值，不能因为响应失败就直接重试。

### 批量改组：预览与逐组执行

`POST /cockpit/api/keys/groups/preview`，请求示例：

```json
{"target_group":"default","token_ids":[10,11],"all_filtered":false}
```

或选择全部筛选结果：

```json
{"target_group":"default","all_filtered":true,"filters":{"user_statuses":[1],"token_groups":["vip"]}}
```

`filters` 规则同合并列表；批量默认仍只选启用用户，KEY 状态未指定时不限状态。
预览冻结具体 ID、所属用户、原组及状态；禁用所属用户或目标组不可用时报错。
返回 `operation_id`、`count`、`rows`（最多前 100 项）、`preview_truncated`；
行含 `id`、`user_id`、`username`、`before_group`、`after_group`。预览只展示前 100 项，执行使用冻结的完整名单。
预览有效期 15 分钟，不修改 KEY、不补 PAT、不进入操作记录。

核对后调用 `POST /cockpit/api/keys/operations/{operation_id}/apply`，请求 `{}`。
两步都要 `X-Management-Action: confirm`。每次最多处理 5 个 KEY，
返回 `results`（`id`、`state`、`message`）、`done`、`remaining`；已结束操作可能另含 `state` 且不含 remaining。

只在当前响应完整收到、全部成功且 `done=false` 时调用下一组。
失败、冲突、不明确后停止后续组，不能执行其他管理员的 operation_id；预览过期或上一组还在请求中返回 409。
**这个地址不是“重试上一组”**：再次调用可能领取新的 5 个目标。断连后先核对操作记录，不盲目循环重试。
已结束操作返回 done=true、不再执行；请求中断不自动续跑，不回滚成功项。

## 统一操作记录

`GET /cockpit/api/operations`，可选 `kind=user|token|quota|schedule`，省略查全部。
普通管理员仅查看自己发起的操作，超级管理员可查看全部。
按发生时间、来源及 ID 倒序，每页最多 50 项；后续页传回 `next_before` 为 `before`，游标不要自行构造。

```bash
curl --fail-with-body --get "$BASE_URL/cockpit/api/operations" \
  -H "Authorization: Bearer $ADMIN_PAT" --data-urlencode 'kind=token'
```

返回 `rows`、`next_before`（无下一页为 null）；每项含 `id`、`source`、`occurred_at`、
`operator_id`、`operator_name`、`action`、`state`、脱敏 `parameters`、`counts`、
`target_users`、`target_user_count`，规则操作另有 `target_rule`。
`counts` 含 total/success/failed/uncertain，目标用户最多展示三位及实际已发起人数。
只记录已发起的操作；预览、未发起名单及没有发起请求的定时任务不在记录中。

`GET /cockpit/api/operations/{source}/{record_id}`：

- `source=management`，`record_id` 为 UUID；`source=schedule`，`record_id` 为正整数。
- 可选 `after` 为上页返回的 `next_after`，默认 -1，从目标 ID 0（新对象未返回 ID）开始也能读取。
- 每页最多 100 项，返回 `rows`、`next_after`；每项含 `target_type`、`target_id`、`user_id`、
  `target_user` 或 `target_rule`、脱敏 `before_data`、`after_data`、`state`、`message`。
- 请求中/结果不明确应人工核对；查询记录不会再次执行原操作。
- 不返回密码、PAT 或完整 KEY；规则软删除后历史仍能查看。

## 浏览器会话桥接

这些接口服务于登录页，不用于 PAT 换取 Cookie。登录流程见[登录与会话](authentication.md)。

- `GET /cockpit/api/auth/session`：使用有效浏览器 Cookie 或 New API 访问 JWT Bearer，返回
  `id`、`username`、`role`、`session_id`、`expires_at`（Unix 秒）。PAT 不接受。
- `POST /cockpit/api/auth/session`：JSON `{"access_token":"<New API 浏览器访问JWT>"}`，
  必须 `X-Cockpit-Auth: 1`，请求最多 8192 字节；经官方验证后返回身份并写 HttpOnly Cookie，不回显 JWT。
- `DELETE /cockpit/api/auth/session`：JSON `{}`，同样要求该头及同源。允许清除过期 Cookie，
  返回 `{"ok":true}`；这一步不撤销 New API 会话，网页登录退出另经官方 logout 完成。

PAT 只用于 API 调用，不能通过它打开管理页面或跳过 New API 的网页登录、二次验证。

## 渠道质量接口

| 方法 | 路径 | 用途 |
|---|---|---|
| `GET` | `/cockpit/api/quality/summary` | 模型/渠道对比、精确总汇总、筛选选项；不加载小时趋势和历史价格组合 |
| `GET` | `/cockpit/api/quality/trends` | 同样的结果，加上历史价格、有记录的小时趋势、每行最近最多五个请求 ID 样本 |

均接受管理员 PAT 或现有浏览器会话，只读，不发报警或修改渠道。

| 查询参数 | 说明 |
|---|---|
| `start` / `end` | 北京时间日期、分钟或秒；结束秒包含，省略默认近 24 小时；最多 31 天 |
| `mode` | `channel`（默认）或 `upstream`；按渠道 ID 或渠道当前标签汇总 |
| `stream` | `all`（默认）、`stream`、`nonstream` |
| `channel_status` | `enabled`（默认）：只统计 New API 当前启用且未删除的渠道；`all`：包含禁用、已删除和未知渠道的历史 |
| `latency_scope` | `direct`（默认）、`all`；改变总耗时与平均 token/s 的成功样本，不改变调用/费用总数；旧日志重试不计算速度 |
| `model` / `user` | 模型、用户名；精确匹配，可重复，最多各 100 个 |
| `channel_id` / `token_id` | 非负 ID，可重复，最多各 100 个 |
| `tag` | 按渠道当前标签限定上游；`tag=` 选未分组；省略时展示全部上游，不关联余额账本 |

质量页不切换账本，也不按计费分组或档位筛选。传入 `scope_id`、`group`、`tier` 返回 400；这不影响用量统计接口的对应筛选。
页面固定使用当前启用渠道明细、全部请求方式和确认无重试的速度样本，不提供这些选项的切换；接口仍支持上表参数。
页面仅提供模型、渠道筛选，勾选即更新；用户、令牌参数仍可由 API 调用方按需使用。
操作列只提供“查看日志”。小时趋势、历史价格和请求 ID 样本可通过 `trends` 接口读取。
渠道状态每次查询重新读取，行明细、汇总、小时趋势、价格详情和筛选选项按同一渠道范围统计。
响应顶层 `channel_status` 返回所用范围；行中的同名字段仍是 New API 渠道状态数值。该限制不修改源数据，也不改变账务归档和余额统计。

示例：

```bash
curl --fail-with-body "$BASE_URL/cockpit/api/quality/summary?start=2026-10-09&end=2026-10-09&model=gpt-6-astra" \
  -H "Authorization: Bearer $ADMIN_PAT"
```

返回 `rows`、`totals`、`top_models`、`options`、`warnings`、`start_ts`、`end_ts`（排他上界）及更新时间。
每行包括 `request_count`、`success_count`、`failure_count`、`ignored_count`、`unknown_count`、
`success_rate`（0～100 或 null）、`failure_codes`、`duration_p50_ms` / `duration_p95_ms`、
`frt_p50_ms` / `frt_p95_ms`、`avg_duration_ms`、`avg_tokens_per_second`、有效样本数、覆盖率和 `amount`（十进制字符串）。
平均延迟 = `duration_total_ms / duration_samples`，范围与总耗时分位数一致；无有效样本为 null，不平均渠道均值。
`top_models` 按当前时间与筛选，合并同模型所有渠道的调用次数后排序，最多六个，返回模型名、请求/成功/失败次数和重新计算的成功率。
性能健康直接使用同一响应的 `totals.success_rate`、`totals.avg_duration_ms`、`totals.avg_tokens_per_second` 与 `top_models`。
页面不展示背景提示列表，`warnings` 仍供 API 调用方使用，查询失败仍返回相应错误状态。
平均 token/s = `tps_output_tokens * 1000 / tps_duration_ms`（含首字等待，不是纯生成速度）；
`tps_samples` 是参与计算的成功调用数，`tps_coverage` 是占成功调用的百分比。三项汇总只包含同批有效样本：输出 Token 和耗时均为正、可归属到最终消费渠道。
无有效样本时两项合计为 0，平均速度为 null；上游、总汇总和小时趋势均重新累计后求比值，不平均已有速度。
旧日志无法拆开重试耗时的调用不参加速度计算；输入和缓存 Token 不参加。
`unique_requests` 是关联请求 ID 的去重数，总汇总独立计算，不能相加各行值。
`trends` 中的 `pricing_buckets` 列历史价格、倍率、分组、档位、消费记录数与日志金额；单价缺失时为 null。
`summary` 不计算价格组合，`pricing_buckets` 为空、`price_bucket_count` 为 0，不表示没有历史价格。

缺失指标为 null，不是 0。质量分类不改变费用，客户端取消、违禁计费记录与结果未知不在成功率分母；明确的 400 等业务拒绝计入失败。
`failure_codes` 优先使用尝试事件或同请求/渠道/尝试的错误日志 HTTP 状态码；未记录错误码的流式失败以 `流式失败（未记录错误码）` 为键。
页面不展示排除/未知列，API 仍保留相应计数。
路由信息不充分、日志关闭或已删除时，不能保证完整可用率。
超过 1000 个模型/渠道组合、异常 JSON 回退超过上限返回 503；查询超时返回 504，缩小时间或筛选范围后再查。
详细规则见[渠道质量](quality.md)。

## 智力测试接口

| 方法 | 路径 | 用途 |
|---|---|---|
| `GET` | `/cockpit/api/intelligence/models` | 查询当前用户可测试模型、自动分组、文本端点、固定题目及专用 KEY 名称；不补建 PAT/KEY |
| `POST` | `/cockpit/api/intelligence/test` | 同步生成鹈鹕骑车 HTML，正常计费 |
| `POST` | `/cockpit/api/intelligence/preview` | 当前请求内沙箱预览 HTML，不调用模型、不保存 |

均接受启用用户的有效登录会话或管理员 PAT，不能指定另一用户的身份。普通用户读取官方 `/api/user/models`，使用自己的会话操作测试 KEY，不创建 PAT；管理员继续使用本人 PAT 和受限补建能力。
测试只接受 `{"model":"模型名"}`，必须携带 `X-Intelligence-Request: 1`；分组与题目由后端决定。
模型目录的每行包含 `model`、`group`、`available_groups`、`channel_id`、`channel_type`、`endpoint`。
优先当前用户组，否则按组名排序；组内按渠道优先级降序、ID 升序选择当前启用渠道。
`endpoint` 只有 `anthropic` 和 `openai`：Anthropic 渠道（`channel_type: 14`）使用 `/v1/messages`，其他使用 `/v1/chat/completions`。
开始时重新核对可用模型和分组。管理员固定选中的渠道；普通用户不使用指定渠道后缀，由 New API 在所选分组内正常路由，目录中的渠道字段仅用于选择测试协议。不按模型名猜协议，不自动切换协议或重试。
测试返回 `model`、`group`、`channel_id`、`channel_type`、`endpoint`、`key_name`、`elapsed_ms`、`html`、`source`、`warning`。普通用户请求没有固定实际渠道，响应中的 `channel_id`、`channel_type` 为 null。
Cockpit 不发送输出 Token 上限参数；Claude 必需的 `max_tokens` 由 New API 的模型配置补齐，实际仍受上游限制。
`html` 为 null 表示结果不能预览，仍可查看 `source` 与 `warning`；不代表可以原样重试而不计费。
同一用户并发返回 409，不排队。模型生成最多 180 秒，超时返回 504、`uncertain: true`，不自动重试。
生成响应没有任务 ID，不提供历史或结果查询接口，刷新页面不能找回。

测试 KEY 名为 `cockpit-智力测试专用`，只在当前用户名下查找或创建，永久保留。
首次创建永久有效、无限 KEY 额度，仍扣账户余额；已有 KEY 的额度、到期、启禁与 IP 配置不重置。
每次自动匹配分组并限定所选模型；KEY 创建/配置写入现有审计，不保存 HTML、PAT 或完整 KEY。

```bash
curl --fail-with-body "$BASE_URL/cockpit/api/intelligence/test" \
  -H "Authorization: Bearer $ADMIN_PAT" \
  -H 'Content-Type: application/json' -H 'X-Intelligence-Request: 1' \
  --data '{"model":"gpt-6-astra"}'
```

预览 JSON 只接受 `{"html":"完整 HTML 文档"}`，同样携带确认头，最多 512 KiB，返回 `text/html`。
响应通过 HTTP CSP 强制沙箱、限制资源和脚本权限。管理页面使用同源会话表单内嵌响应；
预览响应附加仅回传宽高的尺寸脚本，用于窗口缩放，不改变测试返回的 `html` / `source`。
表单需要有效 Origin 与匹配的 `session_id`，不接受 PAT 表单替代。
更完整的使用与限制见[智力测试](intelligence.md)。

## 模型使用（本人令牌）

账户 MCP 使用独立路径 `/cockpit/mcp`、JSON-RPC 和用户 API PAT，不复用管理员 REST API 或模型 KEY；
提供 `get_balance`、`get_usage` 两个本人只读工具，详见[账户 MCP](mcp.md)。

| 方法 | 地址 | 用途 |
| --- | --- | --- |
| `GET` | `/cockpit/api/manual/tokens` | 列出本人令牌，返回 `tokens`、`token_manager_path`；KEY 始终脱敏 |
| `POST` | `/cockpit/api/manual/models` | 按所选 KEY 查询 `/v1/models`，返回 `models: [{id, protocols}]`，不生成内容、不返回 KEY |
| `POST` | `/cockpit/api/manual/key` | 明确复制时读取本人模型 KEY，返回 `key`（含 `sk-` 前缀）；响应敏感且禁止缓存 |

启用的普通用户会话可用，管理页面仍要求管理员。管理员 PAT 也只读取该管理员自己的令牌。
POST 使用 JSON、`X-Cockpit-Manual: 1`，请求体仅接受 `{"token_id": 123}`；最多 1024 字节。
后台先读取官方本人令牌详情，验证归属、状态、有效期和额度，再调用官方 KEY 读取接口。
模型目录调用受 KEY 的分组、模型限制和 IP 允许列表约束，不修改设置、不回退到全站目录。
`protocols` 值为 `chat`、`responses`、`anthropic`；空数组表示上游未声明，不代表全部支持。
令牌列表最多 2000 个，超过返回 413；令牌不可用返回 409，无归属权限或受 IP 限制返回 403，上游响应无效/不可用返回 502/503。
KEY 在查询模型时只留于后台；复制接口只用于显式的含 KEY 复制，不进入交互式文档调试器，避免在响应面板泄露。
官方可能记录 KEY 读取审计，但不新增 Cockpit 操作记录。教程与协议地址规则见[模型使用](manual.md)。

## 生图工作站接口

所有启用用户可用，始终限定本人 KEY，不限制分组名称。管理员 PAT 也不能代用其他用户。生成/编辑是收费操作，交互式 API 文档不提供试运行。

### GET /cockpit/api/image-studio/tokens

返回 `tokens` 和固定的 `token_manager_path`。令牌摘要包含 `id`、名称、脱敏 KEY、实际 `group`、`usable` 与不可用原因，不返回原始 KEY。

### POST /cockpit/api/image-studio/models

携带 `X-Cockpit-Studio: 1`，发送 `{"token_id": 123}`，请求最多 1024 字节。这一步只读取模型目录，不生成图片。

每项模型返回：

| 字段 | 含义 |
|---|---|
| `id` | 当前 KEY 授权的精确图片模型 ID（不限制前缀） |
| `revision` | 有效路由契约的 64 位十六进制摘要，提交时带回 |
| `generation` | `null` 或 `{parameters}`，独立的生成参数 |
| `edit` | `null` 或 `{parameters, local_strategy, max_images, inputs}` |
| `unavailable_reason` | 没有共同契约时的说明，可省略 |

`local_strategy` 为 `native-mask`、`reference-only` 或 `unavailable`。
`inputs` 提供当前渠道的 MIME 类型、单图字节/像素、总像素、最长边与宽高比上限；页面会在提交前校验实际导出的主图、指导图和参考图。
`parameters` 包含 `allowed_fields`、`sizes`、`custom_size`、`qualities`、`formats`、`backgrounds`、`input_fidelity`、`max_images`。按操作使用对应参数，未出现在 `allowed_fields` 中的字段不要发送。

目录按本人模型限制、该令牌实际路由分组的启用 abilities、启用渠道、KEY 的 `/v1/models` 准入与显式协议配置求交；同模型所有可能路由使用共同契约。不默认选择型号，不通过收费请求探测能力。

### POST /cockpit/api/image-studio/generations

携带 `X-Cockpit-Studio: 1`，JSON 请求最多 192 KiB，七个顶层字段必填：

```json
{
  "token_id": 123,
  "model": "gpt-image-1",
  "prompt": "一只站在窗边的橘猫",
  "parameters": {"size": "1024x1024", "quality": "medium", "output_format": "png"},
  "request_id": "studio-request-1",
  "contract_revision": "从模型目录原样读取的 revision",
  "intent": {"operation": "generate", "has_annotations": false, "reference_roles": []}
}
```

上例 `contract_revision` 是说明占位值，实际必须使用目录返回的摘要。
`token_id` 是 1～2147483647 的整数；`request_id` 是 8～128 个英文字母、数字、下划线或连字符。生成提示词必须非空，最多 32000 字符、不含 NUL。

参数来自 `generation.parameters`。按配置允许时可省略自动值；服务端固定 `n=1`，不接受客户端传入 `n`、上游地址、凭据、渠道、`response_format` 等字段。
自定义尺寸为 16 的倍数、最长边 3840、比例不超过 3:1、像素 655360～8294400；JPEG 不支持透明背景。`input_fidelity` 不用于生成。

### POST /cockpit/api/image-studio/edits

携带 `X-Cockpit-Studio: 1`，使用 `multipart/form-data`：

| 字段 | 类型与约定 |
|---|---|
| `token_id` | 正整数的表单文本，仅一次 |
| `model` / `prompt` / `request_id` / `contract_revision` | 普通表单文本，各仅一次 |
| `parameters` | `edit.parameters` 允许的参数对象，JSON 文本 |
| `intent` | `{operation,has_annotations,reference_roles}` 的 JSON 文本 |
| `image[]` | 1～4 个真实文件，同名 part 保序：主图、可选指导图、参考图 |
| `mask` | 仅 `native-mask` 局部修改必填；整图或 `reference-only` 禁止发送 |

`operation` 为 `whole` 或 `local`。`has_annotations` 表示指导图是否有用户标注。每张用户参考图对应一个 `reference_roles` 值：`reference`、`subject`、`style` 或 `composition`。

有指导标注时 `prompt` 可以为空，后台添加固定的编辑说明；没有标注则需要明确的文字要求。
`reference-only` 局部修改的第二张图必须是同坐标的选区指导图，即使没有标注也需要它；`native-mask` 仅在有标注时追加指导图。图片角色与数量必须一致。

输入只接受 PNG/JPEG/WebP，统一 EXIF 方向，拒绝动画、SVG/HTML 和 URL。主图最多 16 MP、全部输入合计 32 MP；单张最多 8 MiB，总上传含表单和遮罩最多 24 MiB，实际还受契约的更小上限约束。
原生遮罩小于 4 MB、带 alpha、与第一张工作图同尺寸；alpha 0 表示可修改，255 表示保护。重复标量、额外字段和不匹配尺寸会在付费请求前拒绝。

### 响应与去重

成功返回 `images: [{b64_json,mime,width,height}]`、`model`、`request_id`、`duration_seconds`；上游提供时另有 `usage`、`upstream_request_id`。
无论供应商返回 Base64 还是经管理员许可的 HTTPS 图片链接，API 最终只返回验证后的图片数据，不暴露临时链接。输出最多 8.5 MP，上游 JSON 最多 48 MiB。

失败返回 `error`、`code`、`uncertain`，以及可用的 `details`：错误类别、HTTP 状态、参数名、安全供应商错误码、上游请求 ID。不会原样回传供应商报文。认证错误可附 `login_url`。

- 契约修订变化返回 `409`，此时未调用模型；重新读取目录并让用户确认新提交。
- 回执有效期内，同账号相同请求 ID 不再派发，返回 `IMAGE_REQUEST_DUPLICATE`；同 ID 的输入摘要不同返回 `IMAGE_REQUEST_CONFLICT`。默认 TTL 24 小时，到期后不保证幂等。
- 回执只保存摘要与状态，没有图片、提示词、KEY；无后台任务、结果查询或断线恢复接口。
- 同用户最多一笔、全站默认两笔，忙时 `429`；监控库或 Redis 回执不可用时 `503`，不降级成本地锁。
- 连接 10 秒、上传 30 秒、总截止 300 秒。应用不自动重试、不换模型，认证刷新不重放付费 POST。
- 超时、断网或 `uncertain: true` 表示结果可能已生效；结束等待不取消上游，需人工核对 New API 日志。New API 的下游重试另由其配置决定。
- 严格局部合成发生在工作站浏览器；直接调用 edits 得到的是模型结果，不承诺选区外像素未变。

操作见[生图工作站](image-studio.md)，能力文件、Redis 与数据库读权限见[部署](existing-installation.md#生图工作站请求池)。


## AI 助手（需显式启用）

页面 `/cockpit/chat/`，个人设置 `/cockpit/chat/settings/`。聊天数据仅属本人，管理员也不能查看他人记录。
完整字段、分页、流式事件、幂等与错误见[聊天接口契约](design/ai-assistant-v1/chat-api.md)及[扩展契约](design/ai-assistant-v2/contracts.md)，
Skill/Memory 的版本与快照见[个人上下文契约](design/ai-assistant-v1/user-context.md)。
所有读写需要已验证会话和匹配的 `X-Cockpit-Session`；写入另需同源及 `X-Cockpit-Chat: 1`。
普通 JSON 使用 application/json，文件上传使用 multipart/form-data；付费轮次不自动重试。
所有聊天接口在交互式文档中禁用试运行，不使用文档页的管理员PAT调用。

| 方法 | 路径 | 用途 |
|---|---|---|
| `GET` | `/cockpit/api/chat/config` | 非秘密客户端限制 |
| `GET` | `/cockpit/api/chat/tokens` | 本人全部分组的令牌摘要 |
| `POST` | `/cockpit/api/chat/models` | 所选本人KEY的模型与明确能力 |
| `GET` | `/cockpit/api/chat/conversations` | 本人全部未删除聊天分页，含空/失败/中断记录 |
| `POST` | `/cockpit/api/chat/conversations` | 幂等创建空聊天 |
| `GET` | `/cockpit/api/chat/conversations/{cid}` | 本人聊天元数据 |
| `PATCH` | `/cockpit/api/chat/conversations/{cid}` | 版本检查后改名 |
| `DELETE` | `/cockpit/api/chat/conversations/{cid}` | 确认删除聊天及附件/快照 |
| `GET` | `/cockpit/api/chat/conversations/{cid}/events` | 按已提交高水位分页读取完整历史 |
| `GET` | `/cockpit/api/chat/conversations/{cid}/sync` | 同一高水位的状态、首批增量、文件版本及必要的 Memory 上下文更新 |
| `GET` | `/cockpit/api/chat/conversations/{cid}/files` | 所有上传和输出版本 |
| `POST` | `/cockpit/api/chat/conversations/{cid}/files` | 上传单个文本、PDF、Word、Excel 或图片附件 |
| `GET` | `/cockpit/api/chat/conversations/{cid}/files/{fid}/download` | 经本人鉴权的附件或生成文件下载 |
| `DELETE` | `/cockpit/api/chat/conversations/{cid}/files/{fid}` | 确认删除上传附件；生成中也可删除，历史保留名称，禁止再次读取、引用或下载 |
| `POST` | `/cockpit/api/chat/conversations/{cid}/runs` | 有限收费轮次，POST fetch SSE；重复ID返回原run JSON |
| `GET` | `/cockpit/api/chat/conversations/{cid}/runs/{rid}` | 状态查询，不续跑模型 |
| `POST` | `/cockpit/api/chat/conversations/{cid}/runs/{rid}/cancel` | 请求停止，不撤销已发生计费 |
| `POST` | `/cockpit/api/chat/conversations/{cid}/runs/{rid}/candidate` | 版本检查后选择已完成多候选回答，后续仅采用所选分支 |
| `POST` | `/cockpit/api/chat/conversations/{cid}/runs/{rid}/memory/undo` | 本人显式撤销自动 Memory 更新；后续修改优先，冲突不覆盖 |
| `GET` | `/cockpit/api/chat/context` | MEMORY/SOUL/IDENTITY及Skill摘要 |
| `PATCH` | `/cockpit/api/chat/profiles/{name}` | 替换本人固定文档 |
| `GET` | `/cockpit/api/chat/skills` | 本人Skill目录 |
| `POST` | `/cockpit/api/chat/skills` | 创建单文件提示Skill |
| `GET` | `/cockpit/api/chat/skills/{sid}` | 查看本人Skill内容 |
| `PATCH` | `/cockpit/api/chat/skills/{sid}` | 版本检查后编辑 |
| `DELETE` | `/cockpit/api/chat/skills/{sid}` | 确认删除，旧运行快照不变 |
| `GET` | `/cockpit/api/chat/conversations/{cid}/runs/{rid}/context` | 本轮实际个人上下文快照 |

聊天列表必须继续取 next_cursor 直到 has_more=false；事件也按 after_seq/until_seq分页。
断线、刷新、401或停止后只能GET恢复记录，不重放旧POST。历史不依赖旧KEY仍有效。

新版配置返回 `features.recovery_snapshot: true` 时，优先使用 `sync`，不要再分别查询元数据、运行和文件。
查询参数：`after_seq` 为已接收序号（初次为 0），`limit` 为每页条数（1–200），
`files_revision` 为上次返回的 64 位小写十六进制文件版本（初次省略），`context_revision` 为已载入的个人上下文版本。
响应包含 `conversation`、`events`、`run`、`files`、`files_revision` 和 `context`。
`events` 沿用事件分页格式；若 `has_more=true`，继续用 `/events` 读取，固定本次的 `until_seq`。
`files: null` 表示文件列表未变化，不表示清空；`context: null` 表示没有需要补取的聊天 Memory 上下文更新。
返回有效 `Retry-After` 时，自动查询须等到该时间之后；恢复接口不创建收费轮次。

当前页面没有模式选择，默认使用完整助手请求（内部兼容值 `mode: "files"`，按站点配置启用 `web_enabled`）。两种旧 API 模式均允许 `attachment_ids`；新请求都由服务器读取本人当前聊天中引用文件的正文，历史通过服务端记录的 `attachment_mode: "server-text-v1"` 重建，不依赖工具调用才能获得正文。`mode: "chat"` 仅保留旧 API 无工具请求兼容；完整助手默认提供文件和联网工具，不因缺少逐模型工具声明禁用模型。运维明确关闭工具时保留正文附件分析。正文及历史计入上下文预算，超限在派发前返回 `413 / CHAT_ATTACHMENTS_TOO_LARGE` 或 `CHAT_CONTEXT_TOO_LARGE`，不自动截断或重试。客户端不能传正文映射、`attachment_mode`、物理路径或指定其他所有者。
