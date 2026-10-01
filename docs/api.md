# 对外余额与报警 API

两个独立接口均使用已有 New API 管理员 PAT：
`Authorization: Bearer <管理员PAT>`。

校验 users.access_token，要求 role >= 10、status = 1、未删除。
不接受模型调用令牌（tokens.key），不剥离或添加 sk- 前缀。
PAT 直接使用原值，包括其中的 + 等字符。PAT 更新或账号停用后立即失效。
默认返回“全部”账本预算；可通过 `?scope_id=<账本ID>` 查询指定渠道标签账本。不是个人或令牌余额，不提供按 Key 的独立预算。

## 实时余额

`GET /statistics/api/balance`

```bash
curl --fail-with-body   -H 'Authorization: Bearer <管理员PAT>'   -H 'Accept: application/json'   https://example.com/statistics/api/balance
```

```json
{
  "code": 0,
  "data": {
    "site": "示例网关",
    "currency": "CNY",
    "total_quota": 10000.0,
    "used_quota": 7500.0,
    "remaining_quota": 2500.0,
    "alert_threshold": 2000.0,
    "usage_percent": 75.0,
    "checked_at": "2026-09-19T10:00:00+08:00"
  }
}
```

- 每次请求重新汇总本月消费；历史月份从逐渠道归档费用按渠道当前归属汇总。渠道改标签后历史账本金额随之变化，原始费用不变。
- 不缓存计算结果，不写报警记录、不发送通知，不改变每日 10 点的定时检查。
- 不受页面的时间、用户、模型和令牌筛选影响。
- 总额度来自余额设置；累计消费为起始月份以来归档费用加本月费用。
- 金额是 JSON number，保持账务原有精度；JSON 不保证显示末尾的零。
- 已用百分比保留两位小数；总额度为零时返回 null，不截断超支百分比或负余额。
- checked_at 是本次计算的截止时间，ISO 8601、北京时间 +08:00。
- 无报警时同样返回完整余额。未配置监控或历史归档不齐全时返回 503，不以零冒充余额。
- 可每 5～10 分钟轮询；每次均访问数据库，多个调用方应错峰并避免并发重复请求。

## 报警检查

`GET /statistics/api/alert`

```bash
curl --fail-with-body   -H 'Authorization: Bearer <管理员PAT>'   https://example.com/statistics/api/alert
```

每次调用执行即时检查、更新报警记录，低于阈值时调用已启用的通知渠道。
这不是单纯的余额查询，轮询余额请使用上面的 /balance。

有报警返回：
`{"has_alert":true,"alert":{...}}`。
alert 包含 title、site_name、budget、spent、remaining、threshold、currency、
checked_at、timezone，金额为数字，时间为带时区 ISO 8601。
无报警返回 `{"has_alert":false,"alert":null}`。
并发检查冲突返回 409，数据库失败返回 503，查询超时返回 504。
认证失败返回 401。对外接口不接受 Basic Auth。

## 网页内部接口与权限

`/statistics/api/balance/status` 用于余额窗口的状态、报警和月度归档展示。
网页及其内部管理接口使用管理员 Basic Auth，不向普通令牌开放管理权限；
上述 `/statistics/api/balance` 与 `/statistics/api/alert` 对外接口仅使用管理员 PAT Bearer。
现有 /statistics/ Nginx 转发即可覆盖新接口。只通过 HTTPS 对外使用，不在 URL 中传递凭据。

数据库只读账号需要 users 表（包含 access_token）的 SELECT 权限；
无需 tokens 表权限，无需修改数据库结构或新建密钥表。

## 账本选择

网页内部 `GET /statistics/api/scopes` 返回账本列表（管理员网页认证）。
`kind=all` 表示全部，`kind=tag` 的名称为 `tag_value`，`kind=ungrouped` 表示未分组。
列表只展示 New API 当前仍存在的标签，以及“全部”“未分组”；隐藏账本的设置和历史费用不会删除。
余额与报警接口增加可选查询参数 `scope_id`，省略时使用全部账本；无效 ID 不回退至全部。

```bash
curl --fail-with-body -H 'Authorization: Bearer <管理员PAT>' \
  'https://example.com/statistics/api/balance?scope_id=3'
curl --fail-with-body -H 'Authorization: Bearer <管理员PAT>' \
  'https://example.com/statistics/api/alert?scope_id=3'
```

`/balance` 仅实时计算指定账本余额，不发送通知；`/alert` 只检查指定账本，
关闭该账本监控后不发送余额告警。通知渠道配置仍为全局共用。

## 网页报表的按需详情（管理员内部接口）

这些接口仍使用网页管理员 Basic Auth，不属于上述 PAT 对外余额接口。

- `/statistics/api/usage`、`/statistics/api/usage/by-token` 和
  `/statistics/api/usage/by-selection` 支持 `details=lazy`。
- 配置监控库并完成迁移后，列表只保留显示字段；不携带 `cost_formula`、
  `tier_usage`、`pricing_buckets`、`price_tiers`。新增 `report_id`、`row_id`、
  `has_pricing_buckets` 和 `price_tier_count` 用于关联详情及显示提示。
- 不传 `details=lazy` 保留完整响应，兼容已有内部调用方；未配置可选监控库时也返回完整数据。
- 悬浮时调用 `POST /statistics/api/usage/details?scope_id=<原账本ID>`：
  请求为 `{"report_id":"<UUID>","row_ids":[0,1]}`，最多 500 行；要求 JSON 和
  `X-Statistics-Request: 1`，不允许跨站调用。返回
  `{"rows":[{"row_id":0,"detail":{"cost_formula":{...}}}]}`。
- 详情只读取查询时保存的不可变结果，不重新拉日志或当前价格。按管理员账号和账本隔离，
  有效期 15 分钟，每账号最多保留最近 10 份；过期、范围不符或已被替换返回 `410`，应重新查询。
- 详情不改变消费、报警、月度归档或余额 API；Excel 仍独立实时查询，精度和工作表规则不变。
