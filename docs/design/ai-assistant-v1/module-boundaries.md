# 模块边界与生图整理

> 当前仓库内的目录和接口已按设计整理，验证边界见[开发验证](../../../CONTRIBUTING.md#ai-助手离线回归)；不设计未来拆仓库方案。

## 1. 基线代码事实（重构前）

基于 `40c91c4`，不能把“生图运行在另一个容器”描述成“后端已无交叉依赖”。

| 当前位置 | 当前联系 | 后续处理 |
|---|---|---|
| `image_studio.py` | 引用 `balance` 判断监控库配置 | 生图自己的 capacity 组件直接读取注入配置，不 import 余额模块 |
| `image_studio.py` | `quota.connect()` 获取源库只读连接；间接带入配额/操作记录 | 使用窄的源库连接适配器；图片路由 SQL 仍属于生图 |
| `image_studio.py` | `manual.list_tokens()`、`manual.upstream()` | 提取本人 KEY 和有界 New API JSON 客户端为公共集成层；不借用“教程”业务 |
| `image_studio.py` | `personal_tokens` 校验归属、状态、取 KEY 前后复核 | 保留算法，搬入公共集成；现有导入可用短期兼容转发 |
| `app.py` | 生图页面/接口、multipart 解析与错误处理内联 | 搬到生图 HTTP 层，以 Blueprint 注册；组合入口仅装配 |
| `image_results.py` | 图片解码子进程字符串硬编码旧模块路径 | 搬文件时同时更新 `_DECODE_CODE`，测试真实子进程而非仅 mock |
| `image_contracts.py`、`image_receipts.py` | 基本无跨业务依赖 | 原逻辑搬入生图目录，不改契约/Redis 状态机 |
| `runtime.py`、`gunicorn_conf.py` | 目前仅对 image 跳过迁移/定时器；入口仍 import 其他模块 | 增加 chat 明确分支；不要让聊天健康检查依赖监控库 |

生图目前仍依赖源 PostgreSQL 查询路由、监控 PostgreSQL advisory lock、隔离 Redis 派发回执。
目录整理不取消这些依赖，也不能删除路由配置复核或把 Redis 换成聊天 JSONL。

## 2. 目标目录

目录均相对于仓库根；这是后续实现清单，不要求本次创建空的 Python 包。

```text
src/new_api_cockpit/
  app.py                         # 宿主 Flask 组合入口；保留现有公共认证
  composition.py                 # 显式构造依赖、注册两个功能 Blueprint
  platform/
    __init__.py
    identity.py                  # Principal / RequestAccess，纯数据契约
    host.py                      # HostServices 契约，无业务模块 import
  integrations/new_api/
    __init__.py
    errors.py                    # 有界错误，不带原始响应/凭据
    json_client.py               # 固定地址、无重定向、有界 JSON 客户端
    personal_tokens.py           # 所有者/状态检查与原始 KEY 临时读取
    token_provider.py            # 本人列表、模型目录、临时 KEY 的实现
    source_db.py                 # 源 PostgreSQL 只读连接，无配额/账本语义
  features/
    __init__.py
    chat/
      __init__.py                 # 仅导出 register/create_blueprint，无副作用
      http.py                    # HTTP 参数和授权边界、SSE 输出
      ports.py                   # TokenProvider 等输入契约
      schemas.py                 # 请求/事件校验和数据类
      errors.py                  # ChatError
      config.py                  # 限制常量、配置解析和能力文件
      service.py                 # 会话/运行用例，非 HTTP
      storage.py                 # JSONL/元数据/附件事务
      locks.py                   # Linux flock 与容量租约
      recovery.py                # 中断/尾行/派生索引恢复
      context.py                 # 从完整轮次构建有界模型上下文
      user_context.py            # 本人MEMORY/SOUL/IDENTITY/Skill管理和冻结
      skills.py                  # 单文件SKILL.md纯解析，不注册工具
      runner.py                  # 本轮有限循环与取消
      transport.py               # 三协议固定上游传输与显式原生 compact
      protocols.py               # Chat / Responses / Anthropic 的严格协议/连续状态转换
      working_context.py         # 完整存档到有界工作窗口的纯规划
      auto_memory.py             # 用户事实提案、受管区及个人文档 CAS/撤销
      web_tools.py               # 显式开启的公网搜索/抓取/文本下载
      resolver.py                # 有界DNS解析辅助，无用户命令/凭据
      tools.py                   # 四工具注册、schema、执行
      workspace.py               # 文件 ID、路径、读写、版本安全
    image_studio/
      __init__.py
      http.py                    # 搬自 app.py 的图片端点和 multipart 解析
      ports.py                   # KEY/源库连接/宿主服务契约
      service.py                 # 原 image_studio 的业务用例
      capacity.py                # 原并发租约和 DB epoch 检查
      source_routes.py           # 图片路由受限只读查询
      contracts.py               # 原 image_contracts
      transport.py               # 原 image_transport
      results.py                 # 原 image_results
      receipts.py                # 原 image_receipts
  templates/
    chat/index.html
    chat/settings.html
    image_studio/index.html       # 原 studio.html，保留页面 URL
  static/
    chat/{app,api,state,stream,render,files,settings}.js
    chat/chat.css
    studio*.js、studio.css        # 现有资源本轮不改公开路径
frontend/studio/                 # 已有独立画板源码/构建，保持
tests/
  test_feature_boundaries.py
  test_chat_*.py
  test_chat_*.cjs
```

两模块的后端平级，不存在“chat 的子模块是 image”或反向关系。
模板可独立归档；聊天的新静态文件使用自己的子目录。生图已有独立源码目录，本轮不强行批量改静态 URL 或画板构建链。
公共登录、侧栏、图标、CSS 可以复用，不应各复制一套。

## 3. 依赖方向（硬约束）

```text
app / composition（组合入口）
  ├─ 现有 auth、导航、站点名称提供器
  ├─ integrations.new_api → 标准库 / psycopg
  ├─ features.chat       → 自己的内部模块 / platform 数据契约
  └─ features.image_studio → 自己的内部模块 / platform 数据契约

composition 把 integrations 的具体对象传给 features 的 ports。
```

- `features/chat` 与 `features/image_studio` **禁止互相导入**。
- 两者禁止 import `app`、`composition`、`manual`、`intelligence`、`balance`、`quota*`、`report*`、`user_management`、`operation_records`、`quality`、`notifications`、`timers`。
- 功能核心通过注入对象使用外部服务；不直接 import `integrations` 的具体实现。生图自己的 capacity/transport、聊天自己的 storage/transport 属于各自基础设施，可以使用其必需第三方库。
- `platform`、`integrations` 禁止反向引用任一业务模块，也禁止经兼容转发间接绕回业务。
- `http.py` 可依赖 Flask；核心 schemas/context/tools 等不能读取 Flask `request/g`。身份必须作为参数传入。
- 组合入口允许引用多个业务模块来“接线”。这不等于业务模块互相调用；本轮不承诺共享宿主进程 import 图已完全隔离。
- 不做通用插件注册中心、抽象 Repository 框架或万能 provider SDK；只定义眼前实际需要的窄接口。

## 4. 公共契约

以下为类型/行为契约，不是本轮要提交的业务代码。

### 4.1 身份与凭据

`Principal(user_id: int, username: str, role: int, session_id: str | None, expires_at: int | None, auth_kind: "session" | "pat")`

- 来源必须是现有 `verify_session`/`verify_pat` 的验证结果，不能从浏览器 JSON 创建。
- 聊天首版仅接受 `auth_kind=session`；生图保持现有 JWT/管理员 PAT 的本人行为。
- `RequestAccess(principal: Principal, credential: str)` 是请求内临时对象。credential 禁止 `repr`、序列化、日志、磁盘和异常详情；不写入事件。Python 无法保证字符串物理擦除，不作此承诺。
- 原认证不能改成仅解析 JWT claims；仍须官方身份校验。允许注入 fake 身份仅用于测试。

### 4.2 本人 KEY：两个功能各自 ports 中定义同一兼容协议

| 方法 | 输入 | 输出/约束 |
|---|---|---|
| `list_tokens(access, *, group=None)` | 已验证 RequestAccess；可选精确分组 | `list[TokenSummary]`，只含当前用户，保留原 MAX_TOKENS 有界分页 |
| `list_models(access, token_id, *, group=None)` | 本人 token_id | `list[ModelSummary]`；KEY 调用 `/v1/models` 并取令牌模型限制交集，不生成、不补建 PAT |
| `lease_key(access, token_id, *, group=None)` | 本人 token_id | 上下文管理器提供 `ModelKey`；取原始值前后都检查归属/状态/额度/过期 |

`TokenSummary` 字段沿用现有 summary：id/name/key_masked/group/status/expired_time/remain_quota/unlimited_quota/usable/unavailable_reason。
`ModelSummary={id: str, protocols: list[str]}`；protocols 沿用现有映射 `chat/responses/anthropic`，缺失表示未知，不按模型名猜测。
`ModelKey` 只给 transport 用，不提供通用 JSON 导出；离开请求不缓存。HTTP 返回只能使用 summary。

适配器内部必须校验 `model_limits_enabled` 严格bool；启用时解析有界 `model_limits` 逗号列表并与 `/v1/models` 取交集，字段无效时拒绝，不按“未限制”放行。
生图最终仍计算“图片路由模型 ∩ 本人KEY目录 ∩ 令牌限制”，保留当前 `_limited_models/_discover` 的语义。
不要只搬TokenSummary后丢掉原始限制信息；不需要把包含全部原始字段的token row暴露给两个业务模块。

聊天调用 `group=None`；生图调用 `group="GPT"`，**精确分组限制不能放松**。
`personal_tokens.owned_row/key_for` 现有前后复核规则必须保留。

公共实现由组合入口注入，两模块不调用彼此的 token/models HTTP 路由，也不为了选 KEY 依赖教程/智力测试模块。
手册继续有显式 reveal-key 功能，但聊天不能复用或暴露这个 HTTP 接口给自己的前端。

### 4.3 宿主服务与只读源库

`HostServices` 提供以下回调，不包含业务计算：

- `access()`：从宿主已验证上下文构造 RequestAccess。
- `validate_write(feature_header, content_type)`：复用同源约束、特征头和内容类型检查；各模块仍校验字段和大小。
- `site_name()`：宿主提供页面标题；功能模块不导入 `report.load_site_name`。

另注入 `SessionVerifier.revalidate(access) -> RequestAccess`，供聊天在每次付费派发前用固定的本次凭据重新验证官方会话，核对user_id/session_id不变。
不从工作线程读取Flask g，不通过此接口刷新/替换为另一账号的Cookie；过期或撤销即停止，不自动续费重发。

`SourceDb.read_connection()`：上下文管理器，源库连接、只读事务、已有连接/语句超时，关闭即释放。用于生图 route 查询，不给聊天核心。
从 `quota.connect()` 提取连接行为时核对只读事务和超时，不复制配额的审计/写操作。
图片所需 SQL 保持在 `image_studio/source_routes.py`，接口 `load_routes() -> RouteSnapshot`，返回现有编译契约要求的受限投影，不包含渠道 KEY、原始 header、任意配置 JSON。

### 4.4 注册与角色

每模块提供 `create_blueprint(dependencies)` 及显式 endpoint 集合 `USER_ENDPOINTS`、`PAGE_ENDPOINTS`、`ROLE_ENDPOINTS`。
宿主把**具体 endpoint 名**加入现有白名单；禁止“整个 /chat 前缀自动允许所有用户”这种未来容易放权的认证捷径。

- `web`：现有页面/API + 聊天页面，不承接聊天状态/文件/运行 API；生图页面、目录仍按现有分工。
- `image`：保持健康检查和图片生成/编辑；其他业务拒绝。
- `chat`：健康检查及显式聊天 API；不得开放管理接口、其他功能或静态文件目录映射。
- 未知 role 启动失败，不默认回退 web。runtime、Gunicorn 生命周期、健康检查三处同步覆盖角色。

Blueprint名称固定 `chat`、`image_studio`。以下 endpoint 使用显式 `endpoint=`，不依赖函数更名推断；每个名字为完整 `blueprint.endpoint`：

| URL后缀/页面 | 完整endpoint | role |
|---|---|---|
| `/cockpit/image-studio[/]` | image_studio.page | web |
| `/cockpit/api/image-studio/tokens` | image_studio.tokens | web |
| `/cockpit/api/image-studio/models` | image_studio.models | web |
| `/cockpit/api/image-studio/generations` | image_studio.generations | image |
| `/cockpit/api/image-studio/edits` | image_studio.edits | image |
| `/cockpit/chat[/]` | chat.page | web |
| `/cockpit/chat/settings[/]` | chat.settings_page | web |
| `/cockpit/api/chat/config`、`tokens`、`models` | chat.config、chat.tokens、chat.models | chat |
| `/cockpit/api/chat/conversations` | chat.conversations | chat |
| `/cockpit/api/chat/conversations/{cid}` | chat.conversation | chat |
| 上述cid下 `/events`、`/files` | chat.events、chat.files | chat |
| 上述cid下 `/files/{fid}/download` | chat.file_download | chat |
| 上述cid下 `/runs`、`/runs/{rid}`、`/runs/{rid}/cancel` | chat.runs、chat.run、chat.cancel_run | chat |

个人设置/Skill端点按个人上下文文档补入同一显式集合，不采用前缀自动授权。
具体补充名字为 `chat.context_get`、`chat.profile_update`、`chat.skills_list`、`chat.skill_create`、`chat.skill_get`、`chat.skill_update`、`chat.skill_delete`、`chat.run_context_get`，均仅chat角色且本人session；页面为上表的chat.settings_page。
上表均对启用普通用户开放但仍核对本人权限；生图保留现有PAT约束，聊天全部session-only。
迁移时同步更新 app.py 的 USER_ENDPOINTS、auth_failure页面跳转集合、role guard、CSP/Referrer-Policy选择、HTTPException uncertain判定、健康检查分支，以及相关测试。
`safe_next()` 与 auth.js.pagePaths 添加聊天和设置页；旧生图URL保持不变。用 `app.url_map` 断言名字/URL/方法/角色，不能只改一个白名单。

## 5. 生图改动映射与不可变项

| 原文件/位置 | 新位置 | 注意事项 |
|---|---|---|
| `image_studio.py` 的 list/models/parse/run | `features/image_studio/service.py` | 参数形状、错误码、uncertain/details 不变 |
| `_monitor_connection`/epoch/health/capacity | `features/image_studio/capacity.py` | 保持全局 2、本用户 1、数据库重启 fail-closed |
| `_source_columns`/`_gateway_policy`/`_routes` | `features/image_studio/source_routes.py` | 保持所有优先级/重试路径校验，不能只选第一渠道 |
| `image_contracts.py` | `features/image_studio/contracts.py` | revision 和编译结果不变 |
| `image_transport.py` | `features/image_studio/transport.py` | 超时、大小、类型与无重试不变 |
| `image_results.py` | `features/image_studio/results.py` | SSRF/地址校验、图片隔离解码不变；修子进程 import 及包根计算 |
| `image_receipts.py` | `features/image_studio/receipts.py` | Redis 命名空间、TTL、AOF/noeviction、claim/finish 不变 |
| `app.py` 生图端点 | `features/image_studio/http.py` | URL、X-Cockpit-Studio、multipart 和权限语义不变 |
| `locks.py` 两个图片 namespace | `features/image_studio/capacity.py` 常量 | 数值保持 90216327/90216328，避免滚动升级锁失效 |

可保留旧 Python 文件作为只转发兼容壳，不能含第二份实现或供新功能反向依赖。
测试的 monkeypatch 目标必须指向真实实现，不能测了兼容壳却绕过实际派发。
不要同时重写图片结果/传输内部结构；已有内部延迟 import 可以先保留为模块内部关系。
原 `Path(__file__).resolve().parents[1]` 在移动后不再指向包的父目录；新 results.py 对应 `parents[3]`，或显式传入由包位置推导的根。
真实 `python -I` 解码测试必须证明加载的是移动后的模块，不能被机器里残留安装的旧版本掩盖。

## 6. 边界验收

`tests/test_feature_boundaries.py` 至少检查：

1. AST 检查上述禁止导入，包括相对导入、函数内延迟导入；禁止动态 import 绕过。
2. 以 fake TokenProvider/SourceDb/HostServices 初始化两模块，不能隐式连接数据库或发 HTTP。
3. 新聊天核心不 import psycopg/redis，不读取监控配置；chat 角色不执行 migration/timer。
4. 生图全部已有契约、结果、回执、前端与认证定向测试仍通过；目录变化不是功能升级。
5. 运行镜像中的模板/子目录静态资源存在，子进程加载新路径成功。

这证明当前仓库内边界明确，不等同于已经完成独立部署或未来剥离。
