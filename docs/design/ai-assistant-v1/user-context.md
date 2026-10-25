# 个人上下文：Skills、MEMORY、SOUL 与 IDENTITY

> 状态：**当前工作区已实现首版契约**；2026-10-15。验证边界见[开发验证](../../../CONTRIBUTING.md#ai-助手离线回归)，不代表已发布或部署。
> 2026-10-16：个人文档/Skill 的格式与 CAS 兼容本规范；新增自动 Memory/撤销与工作压缩以[扩展契约](../ai-assistant-v2/contracts.md)为准。
> 与 [HTTP 契约](chat-api.md)、[存储与锁](chat-storage.md)、[运行循环](chat-runtime.md)、[模块边界](module-boundaries.md)共同实施。本文是个人上下文格式、接口、版本及尺寸限制的权威位置；总体运行/存储额度仍以运行文档为准。

## 1. 功能范围与不可扩大项

### 1.1 用户能做什么

- 在个人设置页查看、手动编辑、清空自己的 `MEMORY.md`、`SOUL.md`、`IDENTITY.md`，查看当前版本、更新时间、大小。
- 创建、查看、编辑、删除自己的单文件 `SKILL.md`；在聊天发送前显式选择 **0～3 个** Skill，并调整次序。
- 查看每个历史 run 实际使用的三份文档、选中 Skill 的内容及版本；不是查看“当前版本冒充当时版本”。
- 同一用户的全部聊天共享其当前个人设置和 Skill 库；每个 run 保存独立不可变副本。普通附件/输出仍严格限于所在聊天。
- 不依赖 KEY 存在即可管理个人设置、查看 Skill 或旧快照；只有发起模型请求才校验本人 KEY。

### 1.2 仍不实现什么（已移除本轮已完成项）

1. 不给普通聊天开放工具；Agent 的基础文件工具仍仅访问当前聊天，显式联网与当前聊天原文检索的扩展工具不扩大文件作用域。
2. 不做模型驱动 Skill 发现/载入、自动匹配、跨聊天自动载入 Skill 或脱离用户发送运行的后台 Memory 任务。当前页面默认启用运行内自动提取/永久 Memory 更新，不再提供勾选开关；见[自动 Memory](../ai-assistant-v2/memory.md)和[最新界面](../ai-assistant-v2/ui.md)。模型口头说“已记住”仍不等于有写入完成审计。
3. 不增加 shell、Python/脚本执行、安装依赖、绕过白名单的任意网络抓取、MCP、插件市场、子代理或任意路径读取。
4. 不扫描宿主 `~/.codex/skills`、`~/.agents/skills`、仓库技能目录或其他用户目录，不通过 Skill 导入下载 URL/Git 仓库，不导入 ZIP/目录。受控文本 URL 下载属于当前聊天文件工具，不是安装技能。
5. Skill 只支持一份提示模板。`scripts/`、`references/`、`assets/` 和其他附属文件不存储、不解析、不跟随；正文里的代码/链接只是文字。
6. 不做管理员代管、共享 Skill、公共 Memory、跨用户搜索、历史版本回滚 UI、永久删除备份承诺。

### 1.3 三份文档的含义

| 名称 | 用户填写的内容 | 不具备的能力 |
|---|---|---|
| `IDENTITY.md` | 助手称呼、服务对象、工作领域等初始身份背景 | 不能变更真实模型身份、账号角色、权限或系统身份 |
| `SOUL.md` | 语言、语气、表达风格、工作习惯 | 不能覆盖服务端安全规则或当前用户明确要求 |
| `MEMORY.md` | 用户愿意在不同聊天间复用的事实、偏好、项目背景 | 不自动证明事实真实/最新，不等于全部历史的摘要或知识库 |

三份文档默认均为空，版本为 0；不从用户名、聊天正文、其他产品或服务器配置偷偷生成。
UI 可以展示填写示例，但只有本人点击保存才变成内容。清空后仍保留对应文档记录，不能删除固定文档种类。

## 2. 用户隔离与身份规则

- 路径根固定为 `/app/data/chat/<CHAT_INSTANCE_ID>/users/<verified_user_id>/`。instance 来自服务端配置，user ID 来自已验证 Principal；HTTP 不接受 `user_id`、`owner_id`、instance、目录、base_url、headers、system_prompt 等覆盖项。
- 本文所有 JSON API 前缀为 `/cockpit/api/chat`。**GET 也必须携带匹配当前会话的 `X-Cockpit-Session`**；POST/PATCH/DELETE 另需 `X-Cockpit-Chat: 1`、同源检查、`Content-Type: application/json`。
- 沿用现有 Cookie/dashboard JWT 的官方身份验证；不新增密码/登录/PAT入口，显式无效 Bearer 不回退 Cookie。认证错误继续使用主 API 的 AUTH_* 规则。
- 原始 KEY、Cookie、JWT、PAT、Authorization、session stamp/session_id 不进入本文任何可序列化对象。设置 API 不需要读取 KEY。
- 管理员和普通用户在这些接口上权限相同：只看自己。猜中别人的 skill_id、cid、rid 也不授予权限；缺失与非本人统一 `404 CHAT_NOT_FOUND`。
- 所有成功和错误响应 `Cache-Control: no-store`；文本按 JSON 字符串返回，禁止公开静态映射、跨域 CORS、日志输出正文或凭据。HEAD/OPTIONS 不得旁路认证。
- 首次打开页面只读。不存在个人设置 manifest 时返回逻辑上的空配置 revision=0，不因为 GET 初始化文件；有 manifest 却损坏/缺少引用文件时必须报错，不能退回空配置掩盖故障。

页面 `/cockpit/chat/settings/` 由 web 角色提供，只含页面结构、公共资源和宿主登录流程；**页面 HTML 不嵌入个人内容**。页面导航不是 JSON API，不要求浏览器地址栏自行添加自定义请求头；所有实际数据加载必须经上述带 stamp 的 API。无尾斜杠 GET 可导航到标准 URL，不重定向任何写 API。

## 3. 个人设置与聊天界面

### 3.1 固定页面与布局

- URL：`/cockpit/chat/settings/`；完整 Flask endpoint：`chat.settings_page`。
- 顶部显示“个人上下文，仅你可见”、当前 `context_revision`、最后保存时间；可返回 AI 助手。
- 四个标签：**记忆 MEMORY / 风格 SOUL / 身份 IDENTITY / Skills**。
- 文档页：纯文本 Markdown 编辑器、UTF-8 字节计数/上限、只读安全预览、保存、放弃修改、清空。清空要二次确认，实际提交 `content:""`，没有自动保存。
- Skills 页：完整列表、名称/描述/版本/更新时间；新建、查看原文、编辑、删除。最多 64 项，所以一次返回全部，不分页隐藏后半部分。创建成功不自动加载到聊天。
- Skill 导入只允许浏览器选择**单个、文件名恰为 `SKILL.md` 的 UTF-8 文本文件**；浏览器读取后放进编辑器，用户确认保存，仍走 JSON 创建/编辑接口。也可直接粘贴。不接受多文件、目录、ZIP 或 URL；无独立上传端点。
- 保存冲突不覆盖：保留未保存草稿在当前页内存，提示“设置已在其他页面更新”，提供刷新比较/复制草稿；用户核对新版后再次主动保存，不能自动合并或覆盖。
- 保存结果未知（断网/超时）时先 GET 当前状态并比较内容，不自动 PATCH/POST 重试。未保存正文不放 localStorage/sessionStorage/IndexedDB，不由服务端访问日志记载。
- 草稿离页提醒；账号切换立即清除草稿、DOM、选中项和下载 Blob，并中止旧请求。迟到响应须匹配页面 generation 和已验证会话，不能显示给新账号。

### 3.2 当前聊天持续载入（2026-10-16 用户调整）

1. 主聊天界面只保留专门的 **Skills** 按钮。点击后在独立窗口中查看本人模板库、正文、载入状态和顺序，不再把库列表展开在主界面，也不使用“本轮 Skills”交互概念。
2. 载入、移除、调整顺序立即保存当前聊天的 `loaded_skill_ids`；最多 3 个有序且唯一的 UUID。状态属于本人当前聊天，不是浏览器临时参数，不写 localStorage，不跨聊天或用户继承。
3. 载入后持续生效，直至本人手动移除。发送、请求重放、刷新、重新登录、个人设置刷新和 Memory 撤销均不自动清空。新聊天及没有新设置事件的旧聊天默认空列表，不把历史曾经使用过的 Skill 自动重新载入。
4. 保存复用 `PATCH /conversations/{cid}`，请求为 `{expected_revision,context_revision,loaded_skill_ids}`，与标题修改请求互斥。服务器校验归属、两个版本、内容和预算，记录 `conversation.skills.updated`；不启动运行、不查询 KEY、不生成回答、不写冻结快照文件。运行中或版本冲突拒绝，不自动重试覆盖。
5. UI 后续发送继续传递既有 `skill_ids` 字段，取当前已确认的载入顺序；每次接受运行仍冻结当时真实个人设置和 Skill 内容版本。兼容原调用方显式提供的运行参数，不修改历史快照或已接受请求的幂等语义。
6. 模板库内容更新后，刷新个人设置以使用新版本；已接受运行继续展示旧的不可变副本。库删除了仍载入的 ID 时不静默剔除，界面明确标记不可用并阻止发送，允许本人移除该引用。移除引用不删除个人模板库。
7. 保存结果不明确时先只读恢复服务器元数据，不能用旧选择继续发送或自动 PATCH 重试；多标签页通过聊天 revision 防止覆盖。账号/聊天切换清除旧作用域的界面和请求。非空个人文档及已载入模板会随后续消息发给所选模型服务，不授予额外权限。

Markdown 预览沿用主页面安全子集：禁原始 HTML/脚本/iframe/SVG/远程图片自动加载；链接只是供用户点击，禁止服务端抓取。正文从不直接进入未净化的 innerHTML。

## 4. 单文件 Skill 格式与校验

### 4.1 与 Agent Skills 的关系

官方格式以 `SKILL.md` 的 YAML frontmatter 与 Markdown 正文为核心，允许扩展元数据和附属资源；官方集成也描述用户显式激活。这里仅借鉴文件结构和显式激活概念，**不是完整 Agent Skills 兼容实现**。[Agent Skills 格式规范](https://agentskills.io/specification)、[官方集成说明](https://agentskills.io/client-implementation/adding-skills-support)。

本项目主动缩小为两个元数据字段、单文件、UUID 存储、不执行资源、用户显式持续载入。尤其不遵循名称必须等于物理父目录名的完整格式约定；名称只是显示/逻辑标识，物理目录必须由服务器 UUID 决定。不能宣传“任意第三方 Skill 可直接安装”。

### 4.2 唯一允许格式

```markdown
---
name: weekly-summary
description: "把用户提供的材料整理成中文周报，缺失信息明确列出。"
---
# 周报整理

1. 仅使用当前用户明确提供的材料。
2. 按已完成、风险、下一步组织内容。
3. 不编造数字；缺失数据标为待补充。
4. 若当前是文件助手模式，可使用现有文件工具生成 Markdown；普通模式直接回答。
```

解析按下列顺序实现，错误不得“猜测修复后接受”：

1. 接受 JSON 字符串，严格 Unicode/UTF-8 编码；CRLF/单独 CR 规范化成 LF。允许文件导入时移除**一个开头** UTF-8 BOM；其他正文保持原样，不 trim 正文或悄悄添加换行。
2. 禁 NUL、无效代理项和除 LF/TAB 外的 C0 控制字符；正文不运行任何代码。
3. 第一行必须恰为 `---`；接下来恰两行且次序固定：`name: <value>`、`description: <value>`；第四行必须恰为闭合 `---`，其后为 Markdown 正文。
4. `name` 为未加引号的 ASCII `[a-z0-9]+(?:-[a-z0-9]+)*`，1～64 字符；不允许连续/首尾 `-`。当前用户活跃 Skill 内唯一，修改名称仍保持原 skill_id。
5. `description` 必须是**单行 JSON 双引号字符串**（属于 YAML 双引号字符串的受限形式）。按 JSON 字符串解析；解码后 1～256 Unicode 字符、含非空白、不得含换行/TAB/控制字符。UI 自动按此格式生成，不需用户理解 YAML。
6. 两个字段不可重复，不允许注释、空行、任意其他字段、多行标量、数组/对象、YAML tag/anchor/alias、`${...}` 插值语义。不得用不安全 YAML loader，也不新增通用 YAML 解析依赖；用严格行结构 + JSON 字符串解析即可。
7. Markdown 正文必须含非空白字符。规范化后的**完整 SKILL.md**（含 frontmatter）受 SKILL_FILE_BYTES 限制；保存并计算完整文件 SHA256。
8. `allowed-tools`、`tools`、`model`、`hooks`、`permissions`、`scripts` 等额外 frontmatter 一律 `422 CHAT_SKILL_FORMAT_INVALID`，不得静默忽略然后让用户误以为已生效。
9. 正文提到路径/URL/代码可以保留为文字，但不跟随、不安装、不执行；没有“根据文字自动打开关联资源”的阶段。

该固定语法是本项目产品约束，不是对官方 YAML 能力的描述。导入不符合者提示具体字段/行号，用户手工转换；不访问网络来补齐内容。

## 5. HTTP 路由、endpoint 与白名单

所有写入是本人在 UI 中的明确保存/删除动作；这些 HTTP handler **不注册为模型工具**。JSON schema 默认 `additionalProperties:false`；类型严格，不将 bool 当整数，不将 null 当空数组。

| 方法与路径（API 省略公共前缀） | 完整 endpoint | 角色 | 成功 |
|---|---|---|---|
| GET `/cockpit/chat/settings/`（页面） | `chat.settings_page` | web | 200 HTML |
| GET `/context` | `chat.context_get` | chat | 200 |
| PATCH `/profiles/{name}` | `chat.profile_update` | chat | 200 |
| GET `/skills` | `chat.skills_list` | chat | 200 |
| POST `/skills` | `chat.skill_create` | chat | 201 |
| GET `/skills/{sid}` | `chat.skill_get` | chat | 200 |
| PATCH `/skills/{sid}` | `chat.skill_update` | chat | 200 |
| DELETE `/skills/{sid}` | `chat.skill_delete` | chat | 200 JSON |
| GET `/conversations/{cid}/runs/{rid}/context` | `chat.run_context_get` | chat | 200 |

- `{name}` 只能是大小写精确匹配的 `MEMORY.md`、`SOUL.md`、`IDENTITY.md`，不是任意文件名。路由最好显式枚举/校验，不能传给通用文件读取器。
- sid/cid/rid 都是小写 UUID v4；非法标识返回 400，无权/不存在返回 404。
- 宿主 `USER_ENDPOINTS` 显式加入上表全部 9 个名字；`PAGE_ENDPOINTS` 仅加入 `chat.settings_page`；chat 角色的 `ROLE_ENDPOINTS` 加入 8 个数据 API，web 仅允许该页面；image 一个也不加入。
- Blueprint 名固定 `chat`，注册时显式设置 endpoint 后缀；不得通过函数名推断、前缀通配放行、复制一套第二认证。
- API GET 只允许文档列出的查询参数，未知参数 400。不存在批量写、读任意历史设置版本、任意文件下载、按用户名访问或管理员接口。

### 5.1 通用类型与 revision

`context_revision`：当前用户整个个人上下文的单调整数版本，初始 0；任一 profile/Skill 有效创建、修改、删除时 +1。不是会话 revision、模型 capability_revision，也不是 session stamp。

`revision`：某个 profile 或 Skill 的内容版本；profile 初始 0，首次非空保存为 1；Skill 创建为 1，内容改变 +1。整数范围 0..9007199254740991；达到最大值后拒绝再写并告警，不回绕。

写操作同时携带当前 `context_revision`，编辑/删除还带目标 `expected_revision`。在锁内先校验全局版本，再校验目标版本；任一冲突拒绝，不最后写入者覆盖。
这项全局检查是有意采用的保守规则：即使改的是本轮未选中的另一个 Skill，也需要刷新并确认新 context_revision；首版不实现复杂的局部版本合并。
版本相符且规范化后的内容完全相同时返回当前对象 200、`changed:false`，不推进任何 revision/时间。即使内容相同，过期 context_revision 仍返回 409，不能绕过用户确认。

`ProfileDocument`：`{name,revision,content,sha256,size_bytes,updated_at}`，其中 sha256 为规范化 UTF-8 字节的 64 位小写十六进制，updated_at 为 RFC3339 UTC；未编辑的默认空文档时间为 null、sha256 为标准空串哈希。

`SkillSummary`：`{id,name,description,revision,sha256,size_bytes,created_at,updated_at}`。`SkillDocument` 为 SkillSummary 加 `content`（完整规范化 SKILL.md）；不另返回重复 body 字段。

### 5.2 GET `/context`：一致的个人设置视图

无请求体/查询参数。短持 profile 锁，读一份 manifest，再读其引用的三份文件；返回同一个 revision 的文档和 Skill 摘要，不能分三次读到混合版本。

```json
{
  "schema_version": 1,
  "context_revision": 0,
  "updated_at": null,
  "profiles": [
    {"name":"MEMORY.md","revision":0,"content":"","sha256":"e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855","size_bytes":0,"updated_at":null},
    {"name":"SOUL.md","revision":0,"content":"","sha256":"e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855","size_bytes":0,"updated_at":null},
    {"name":"IDENTITY.md","revision":0,"content":"","sha256":"e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855","size_bytes":0,"updated_at":null}
  ],
  "skills": [],
  "limits": {
    "profile_bytes":{"MEMORY.md":8192,"SOUL.md":4096,"IDENTITY.md":2048},
    "skill_file_bytes":8192,
    "skill_name_chars":64,
    "skill_description_chars":256,
    "skills_per_user":64,
    "skills_per_run":3,
    "personal_context_bytes":32768,
    "settings_request_bytes":65536
  }
}
```

profiles 数组顺序固定如示例，恰三项；skills 按 name ASCII 升序、id 升序，返回全部且只含当前本人活跃项。limits 由 config.py 返回，不由前端维护另一套值。
这里不返回全部 Skill 正文、不向模型发送未选 Skill 目录。未初始化用户正常返回上述空数据，不是 404。

### 5.3 PATCH `/profiles/{name}`：完整替换一份文档

```json
{"context_revision":7,"expected_revision":2,"content":"偏好中文、先给结论，再列依据。"}
```

三个字段全部必需。name 由路由固定；content 为完整新正文，空串是清空，缺字段/null 不是清空。按 §4 的通用 UTF-8/换行/控制字符规则规范化，但 profile 没有 frontmatter 语法要求。

成功返回 `{context_revision,changed,profile:ProfileDocument}`；有变化则仅该文档 revision +1，其他文档和 Skill revision 不动。此操作不修改任何历史 run，不向模型发请求。
返回的是提交后实际保存内容，前端用返回版本更新基线；不得仅凭本地按钮状态推断成功。

### 5.4 GET `/skills` 与 GET `/skills/{sid}`

- `/skills` 无查询参数，返回 `{context_revision,skills:SkillSummary[],total:int}`，与 `/context` 同排序、同可见性、全量有界，不返回正文。
- `/{sid}` 允许唯一可选查询参数 `context_revision=<int>`；前端查看已选 Skill 时必须传其当前列表版本。版本不匹配返回 409；不传则读当前版本。
- 详情返回 `{context_revision,skill:SkillDocument}`。两种读取均短持 profile 锁；不存在/非本人统一 404。

### 5.5 POST `/skills`：创建

```json
{"context_revision":7,"content":"---\nname: weekly-summary\ndescription: \"整理本周材料\"\n---\n列出已完成、风险和下一步。\n"}
```

服务端生成 UUID v4；请求不得指定 ID/物理路径/任意版本。验证数量、格式、名称唯一性、额度；成功返回 `{context_revision,changed:true,skill:SkillDocument}`，HTTP 201。
Skill 创建版本 1；名称重复 409，不自动加后缀，不覆盖原 Skill。网络结果未知先 GET 列表确认，客户端不自动重复 POST；过期全局 revision 同样阻止无意的第二次创建。

### 5.6 PATCH `/skills/{sid}`：编辑

```json
{"context_revision":8,"expected_revision":1,"content":"---\nname: weekly-summary\ndescription: \"整理材料并列出待核实项\"\n---\n先列结论，再列证据与缺失信息。\n"}
```

完整替换文件，规则同创建；可改 name/description，但 id、created_at 不变。新名称与其他活跃 Skill 冲突则 409。
返回 `{context_revision,changed,skill:SkillDocument}`；已有运行继续用旧快照，不因编辑撤销或重新执行。

### 5.7 DELETE `/skills/{sid}`：删除

```json
{"context_revision":9,"expected_revision":2,"confirm":true}
```

confirm 必须严格为 true。UI 二次确认：“从个人 Skill 库删除；以前聊天的上下文副本不受影响”。
成功返回 `{context_revision,deleted_id:sid,deleted:true}`，HTTP 200；使用 JSON 而非 204 是为了返回新全局版本。
删除后原 ID 永不复用，详情/再次删除返回 404，不查他人目录；不存在恢复接口。快照内旧副本仍属于原 run；只删除其聊天才删除当前存储内该副本。
先提交 manifest 的移除，再安全清理此 Skill 版本文件；清理失败仍计额度并由恢复器重试，不能因清理失败把已删除 Skill 重新列出。

### 5.8 POST run 的新增字段

原发起路由 `/conversations/{cid}/runs` 与认证/SSE/停止不变；在原有字段上新增两个**必需**字段：

```json
{
  "client_request_id":"44444444-4444-4444-8444-444444444444",
  "expected_revision":12,
  "token_id":123,
  "model":"example-chat-model",
  "capability_revision":"sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "context_revision":7,
  "skill_ids":["66666666-6666-4666-8666-666666666666"],
  "mode":"files",
  "text":"请整理附件。",
  "attachment_ids":[]
}
```

- context_revision 来自本人 `/context`；即使三份文档为空且不选 Skill，也传 `0` 和 `[]`，不得缺省为“加载最新”。
- skill_ids 长度 0..3，小写 UUID v4、唯一、有序；只允许该用户在此全局 revision 下存在的 Skill。**数组靠前者在 Skill 间冲突时优先**，不排序后丢失用户次序。
- 客户端不传 Skill 正文、个人文档正文、resource version、文件路径；后端在同一 manifest 中解析。ID 不存在/非本人返回通用 404，不部分加载，不静默忽略。
- context_revision 或所选 Skill 在发起前已变化则 409 `CHAT_CONTEXT_CHANGED`，要求重新查看后手动发送；不自动刷新 revision 后重发原付费请求。
- 幂等摘要加入 context_revision 和 skill_ids **顺序**；expected_revision 仍不参与。先查同 owner/cid/client_request_id 的既有幂等结果，再校验当前设置，避免旧请求被错误换成新上下文。
- 重复请求命中原 run 时返回原状态，不读最新 profile、不复写快照、不收费；同一个幂等 ID 改了新增字段则 `CHAT_IDEMPOTENCY_CONFLICT`。

### 5.9 GET `/conversations/{cid}/runs/{rid}/context`

无请求体/查询参数。先验证本人会话和该 rid 属于该会话，再从已提交 `run.accepted.context_snapshot` 定位；禁止拿任意 `file=` 参数下载。
成功返回 `{context_snapshot: <§8 对象>}`，HTTP 200，包含完整内容，不返回 KEY/token_id/session/auth 或绝对路径。
读取accepted引用时严格受主存储的commit高水位限制，不能把已写入LF但尚未提交的accepted当作快照已生效；索引落后时只补已提交前缀。
不存在已接受 run、非本人、rid 来自其他聊天均 404。已接受 run 的快照缺失/校验失败返回 503 `CHAT_CONTEXT_CORRUPT`，不能拿当前设置补造旧快照；该聊天其他可读历史仍保留。
只读查看无论 KEY 是否失效都可用，绝不恢复运行。run.accepted 中的引用/哈希可见，但不是可公开下载的链接。

## 6. 错误码、大小与行为

沿用主 API 错误包：`{error,code,uncertain,run_id,details}`。设置操作 run_id=null、uncertain=false；新 run 预检失败同样不进入派发边界。认证错误沿用宿主原格式。

| HTTP | code | 本文场景 |
|---|---|---|
| 400 | CHAT_INVALID_REQUEST | 未知字段、错误类型/UUID/文档名、重复/超过3个 skill_ids、缺少必需字段 |
| 401/403/409 | AUTH_* / CHAT_SESSION_REQUIRED | 沿用登录、stamp、账号切换规则；不另设计认证 |
| 404 | CHAT_NOT_FOUND | Skill/run/会话不存在或非本人；不区分、不枚举 |
| 409 | CHAT_CONTEXT_CHANGED | 全局 revision 冲突；details 可含本人 current_context_revision |
| 409 | CHAT_PROFILE_REVISION_CONFLICT / CHAT_SKILL_REVISION_CONFLICT | 全局版本通过后目标 expected_revision 冲突 |
| 409 | CHAT_SKILL_NAME_CONFLICT | 本人活跃 Skill 名称已存在 |
| 409 | CHAT_SKILL_LIMIT | 活跃 Skill 数量已达上限 |
| 409 | CHAT_PROFILE_BUSY | 短期 profile 锁忙；提示稍后手动操作，不阻塞无限等待 |
| 409 | CHAT_IDEMPOTENCY_CONFLICT | 同一 run 幂等ID更换内容/选中项/全局版本 |
| 413 | CHAT_REQUEST_TOO_LARGE | 设置 HTTP 原始请求体超过上限 |
| 413 | CHAT_PROFILE_TOO_LARGE / CHAT_SKILL_TOO_LARGE | 规范化单文件超限；details={limit_bytes} |
| 413 | CHAT_CONTEXT_TOO_LARGE | 本轮个人块或完整模型上下文超限；不偷偷截断 |
| 415 | CHAT_CONTENT_TYPE_UNSUPPORTED | 设置写入不是 application/json |
| 422 | CHAT_SKILL_FORMAT_INVALID | 受限 frontmatter/非空正文不合法；仅返回 field/line/reason枚举，不回显正文 |
| 422 | CHAT_CONTEXT_CREDENTIAL_FORBIDDEN | 命中已知会话凭据或明确凭据格式；不回显敏感值 |
| 507 | CHAT_STORAGE_LIMIT / CHAT_STORAGE_FULL | 设置子额度、用户/全站额度、预留或磁盘空间不足 |
| 503 | CHAT_CONTEXT_CORRUPT | 已有 manifest/版本/快照 schema、哈希或引用损坏 |
| 503 | CHAT_STORAGE_UNAVAILABLE / CHAT_CONFIG_INVALID | 不可用文件系统、未知schema主版本或无效配置 |

权限检查先于读取资源/版本详情；错误不包含其他用户 ID、资源标题、正文、真实路径、KEY 或上游原文。设置失败不影响旧有效版本；快照失败绝不继续派发。

## 7. 每用户存储、版本、原子提交

### 7.1 选定方案：不可变版本文件 + 单个当前 manifest

不采用“直接分别覆盖三个活文件”的做法，否则崩溃可能读到新 MEMORY + 旧 SOUL 的混合状态。
逻辑名称仍是 `profiles/{MEMORY.md,SOUL.md,IDENTITY.md}` 和 `skills/<uuid>/SKILL.md`；**最终物理布局选用以下版本化布局**，不额外维护可写镜像或 `current` 软链接：

```text
/app/data/chat/<instance>/users/<user_id>/
  context-manifest.json                 # 唯一当前个人设置提交点；不是聊天列表索引
  profiles/
    MEMORY/versions/1.md                # 逻辑名 MEMORY.md；版本不可覆盖
    SOUL/versions/1.md
    IDENTITY/versions/1.md
  skills/<skill_uuid>/versions/1/SKILL.md
  .context-tmp/<operation_uuid>/        # 同文件系统临时文件；计额度
  .context-quarantine/                  # 孤立/异常文件，计额度，不自动激活
  conversations/<cid>/contexts/<rid>.json

/app/data/chat/<instance>/.locks/users/<user_id>.profile.lock
```

版本目录/文件号由服务端整数编码，profile 基名由固定映射生成，Skill ID 由服务端生成；不使用 description/name 作为路径。
manifest 是**权威当前状态**，不能像聊天派生 index 一样丢失后盲扫最高版本重建：那些文件可能是未提交临时版本或已删除 Skill。
manifest 缺失且存在任意非初始化版本数据时判为损坏，禁止当“首次用户”回到 revision=0；仅真正没有任何个人设置文件时使用逻辑空默认。

manifest schema v=1：

- `v:1, context_revision:int, updated_at:timestamp, profiles:object, skills:object`，仅这些顶层字段。
- profiles 恰含三个固定文件名；每项为 `{revision,sha256,size_bytes,updated_at}`，不含 content/任意 path；revision=0 只允许空文档，其文件无需存在。
- skills 是 `{<uuid>: SkillSummary}` 映射，key 必须等于 summary.id；name 唯一。metadata 只能来自对应不可变 SKILL.md 的解析，不能信任客户端单独提供名称/描述。
- 各路径根据上述映射从 revision 推导；读取时复核类型、大小、哈希及 metadata。内部 manifest 也不含凭据或任意系统提示字段。
- 更新不复制全部 Skill 文件，只新增改变资源的一个不可变版本，再一次性替换 manifest；未改变的引用维持原 revision。

### 7.2 锁顺序（与主存储文档完全一致）

全局顺序：**user_run → global_slot → conversation → profile → storage**；仅取所需子集，禁止逆序。

- 个人设置创建/修改/删除：仅 `profile → storage`；不取得 user_run/global_slot/conversation，所以用户可在某轮运行期间修改未来设置。
- GET 当前设置/Skill：短持 profile 锁，不需要会话锁；快照 GET 读取已提交不可变文件，不拿 profile 锁，也不等待整轮结束。
- 接受 run：已有前三把锁，再短拿 profile 锁验证版本并复制三文档和所选 Skill；需要空间预留/文件提交时在 profile 之后短拿 storage 锁。profile 锁内无网络/模型调用，复制完成即释放，不随运行长持。
- `flock(LOCK_EX|LOCK_NB)`，锁 inode 固定、close-on-exec、不 unlink；锁忙返回有界错误。不能用线程锁替代跨 worker 锁。
- storage 锁只保护额度和短提交；GC/恢复不能在持 storage 时反过来等 profile/conversation。先持 profile 再拿 storage；拿不到就保留文件待下次，不死锁。

### 7.3 一次设置写入的确定步骤

1. 验证身份/写头/同源/请求上限，规范化输入并做纯格式校验；取得本人 profile 锁。
2. 读取/验证当前 manifest；编辑/删除先在本人命名空间定位目标（不存在即404），再在锁内复核 context_revision、expected_revision、唯一名/数量。全同内容且版本匹配则返回 changed=false。
3. 按主存储协议申请 `kind=profile` 的临时空间预留，计入新版本、manifest 临时副本及目录余量；不得先删除旧有效文件腾空间再尝试保存。
4. 在本人 `.context-tmp` 写新版本，处理短写、fsync、核对哈希；安全移动到唯一版本名，**不得覆盖既有版本**，fsync 所在父目录。遇同版本残留，先按恢复规则隔离并计额度，不能覆盖不明文件。
5. 构造完整新 manifest，context_revision +1；写同文件系统临时文件、fsync；`os.replace` 当前 manifest，fsync 父目录。**这是唯一生效点**。
6. 更新/释放额度预留，返回新版本；后续 GC 仅处理已证明不再引用的内容。主文件提交成功、后续响应/台账更新失败时不能回滚或再执行一遍，客户端 GET 确认，台账按磁盘恢复。
7. 删除 Skill 不写新正文：提交去掉该项的新 manifest 后再清理旧版本；任何旧 run 的快照不在清理范围。

所有目录0700/文件0600、umask0077、容器UID10001，沿用 dir_fd/openat/O_NOFOLLOW/普通文件与 nlink=1 检查；不能用 chmod777，也不能只做字符串前缀校验。
宿主直接手改这些文件不是支持的编辑入口；检测出哈希不符应失败，不偷偷接纳为新版本。

### 7.4 崩溃与保留

| 故障点 | 结果 |
|---|---|
| 新版本未落完 / manifest 未提交 | 旧 manifest 继续有效；孤立版本隔离、计额度，不自动启用 |
| manifest 已原子替换、HTTP 响应丢失 | 新版有效；GET 可确认，禁止凭超时自动重复写 |
| manifest 有效但某引用文件缺失/哈希不同 | 503，不跳过该文档/Skill，不替换为旧内容或空内容 |
| manifest 损坏/未知版本 | 503，保留证据并提示恢复备份；不能按目录最大版本推断用户意图 |
| Skill 删除提交后清理中断 | 仍不可见；恢复仅在本人范围清理未引用旧版本，不能复活 |
| 快照已落盘但 accepted 未提交 | 按聊天恢复规则隔离，不能创建消息/自动运行 |

首版只保留 manifest 当前引用的资源版本，不提供个人设置历史回滚；旧 run 自带完整副本，不依赖原设置文件。**版本号持续递增，但不永久保留所有历史版本文件**，这样不需要第二份提交日志/辅助索引。
成功提交新 manifest 后，旧的未引用资源版本可在 profile 锁下安全清理；清理前核对当前 manifest，不按文件 mtime/最大版本猜测引用，不删除任何会话 contexts。崩溃留下、无法确认用途的文件先隔离，不自动激活；manifest 本身损坏时停止 GC，等待维护者恢复。
隔离/临时文件同样计入额度，恢复可在确认无活跃写者及引用后安全清理；不能无限积累以绕过 4MiB 子额度。删除/恢复的运维日志不记录正文。首次编辑产生的未提交文件可以清理回真正空的初始状态，但必须由持 profile 锁的恢复步骤确认，不是 GET 遇异常便返回空值。

## 8. 每轮冻结、快照与不可变性

### 8.1 冻结与接受流程

1. 沿用主流程：鉴权 → 既有幂等检查 → user_run/global_slot/conversation 锁 → 再查幂等 → 会话版本/KEY/模型/附件预检。
2. 短拿 profile 锁，读取同一个 manifest，严格匹配请求 context_revision；读取并验证三份文档和有序 skill_ids。未选 Skill 连正文也不读取/发送。
3. 生成只含个人上下文的 `ContextSnapshot`（下节），在内存计算 prompt block/完整上下文预算；仍在接受前失败则不落用户消息、不收费。新请求不能因为设置损坏静默降级为无 Memory。
4. 在主流程空间预留内，按会话不可变文件协议保存 `contexts/<rid>.json`：临时文件→fsync→不可覆盖发布→目录fsync；计算最终原始文件字节 SHA256。不能先发 accepted 再补快照。
5. 经主存储协议写入/同步 `run.accepted` 并发布 `commit.json` 高水位，其中引用固定为下列结构，file只由rid生成；之后才允许上游派发。profile锁在完成冻结的一致副本后即可释放，后续运行使用内存副本，不重读最新版。
6. 同一 run 的所有模型请求使用同一快照和拼接规则；设置更新/Skill 删除不影响此 run。worker 崩溃仍按主协议记录 interrupted，不从快照自动续跑。

并发语义的线性化点是第2步在 profile 锁下验证并复制成功的时刻；其后设置被改，即使 run.accepted 稍晚落盘，该轮仍合法使用已冻结旧版，不再临时刷新/重冻。三份文档与全部已选 Skill 必须在同一次短锁期间读完，不能只复制路径随后解锁再读文件（旧版可能已被GC）。

`run.accepted.data.context_snapshot` 的**完整且唯一引用形状**：

```json
{
  "file":"contexts/22222222-2222-4222-8222-222222222222.json",
  "sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "context_revision":7,
  "skill_ids":["66666666-6666-4666-8666-666666666666"]
}
```

示例 hash 仅表示合法字段形状，实施测试须计算真实文件哈希，不能复制占位值。正文不重复塞入 accepted 事件；该事件已有 text/token_id 等聊天字段，**不复制到个人上下文快照**。

### 8.2 快照 v=1 数据字典

快照只含下列白名单字段，不序列化 RequestAccess、HTTP请求、模型传输参数、Flask g、任意 `__dict__` 或整个 run：

| 字段 | 类型/含义 |
|---|---|
| v | 整数1 |
| conversation_id / run_id | 当前cid/rid，用于与路径和 accepted 交叉核对，不含owner/session |
| captured_at | 服务端 RFC3339 UTC |
| context_revision | 冻结时的全局版本 |
| policy_version | 固定 `personal-context-v1`，标识本文拼接/优先级规则 |
| profiles | 恰三份 ProfileDocument，顺序 MEMORY/SOUL/IDENTITY；空内容也保存 |
| skills | 按请求选择顺序的 0..3 个 SkillDocument，完整 SKILL.md，无body重复副本 |
| skill_ids | 与 skills.id 顺序完全相同的列表 |
| budget | `{personal_block_bytes:int,limit_bytes:32768}`，计量实际生成的个人块UTF-8大小，不是Token数 |

文件采用 UTF-8、紧凑 JSON、键排序、`ensure_ascii=false`、拒绝NaN/Infinity，尾部一个LF；哈希覆盖包含LF的最终文件原始字节。单文件最大 CONTEXT_SNAPSHOT_BYTES。snapshot 中不再保存自己的 sha256，避免自引用哈希。
content 保留规范化后真实文字，schema 不允许 KEY/token_id/model/session/auth/headers/cookie/base_url/绝对路径字段；模型消息正文、工具schema、历史正文、用量不属于这个文件。内容可能是敏感个人资料，因此只能本人经认证 GET。

三份 profile、选中 Skill 的 revision/hash 是当时真实版本。清空 Memory 或删 Skill **不会追溯擦除旧快照和旧聊天文字**；UI 明确提示。已删除/失效的原始 Skill 不影响读取旧快照；需要删除副本时由用户删除包含它的聊天，宿主备份另按保留策略处理。

### 8.3 凭据边界

快照绝不包含平台持有的原始 KEY/session/认证信息：实现采用白名单 DTO，凭据对象不可序列化；测试给请求凭据注入唯一哨兵，遍历 manifest/版本/快照/事件/日志断言不存在。禁止仅依赖日志脱敏来满足此要求。
UI 禁止把这些文档当密码库。保存前以及冻结前拒绝已知当前会话凭据原值、明显的 Authorization Bearer/Cookie 赋值、私钥块、项目支持的明确 API-key 格式，错误不回显值；匹配规则集中定义并用假凭据测试，不读取其他用户密钥做全站扫描。
已知凭据的比较由HTTP/接受运行层在请求内执行；只把通过检查的正文交给 user_context，不为此把 RequestAccess/KEY 传进持久化组件。识别规则只做本地拒绝校验，不调用外部DLP或模型。
首版固定拒绝规则：当前请求内已持有的完整认证凭据原值精确匹配；正文匹配 `(?i)\b(?:authorization|cookie|x-cockpit-session)\s*[:=]\s*\S+`；`\bsk-[A-Za-z0-9_-]{16,}\b`；三段JWT外形 `\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b`；以及 `-----BEGIN (RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----`。不把普通单词“KEY”、数值user_id或短的显示名称当作秘密来匹配。上述规则在规范化且已限长的正文上运行，属于保守的产品输入限制，可能拒绝含假凭据的示例；不做静默删改。
**任意自然语言中的未知秘密无法被格式校验百分之百识别**，不能承诺自动 DLP；用户主动粘贴的未识别敏感文字可能仍是正文。不得为了“脱敏”静默改变正文或写一个含原值的隔离副本。产品保证是平台凭据不进入快照，已识别凭据输入拒绝；不是保证任何文本都不可能包含用户自填秘密。

## 9. 拼接优先级、预算与工具权限

### 9.1 固定优先级

从高到低：

1. 服务端固定安全规则、授权、工具schema、路径边界、运行与存储限额；这些不能由用户文档改写。
2. 当前轮用户明确提出的任务和约束。
3. 当前聊天已载入的 Skill；相互冲突按 skill_ids **先选/排在前面者优先**。
4. SOUL（风格）、IDENTITY（角色背景），两者冲突时 SOUL 的表达要求优先；都不能改变真实权限。
5. MEMORY 作为可能过期的背景事实/偏好；当前用户信息优先于 Memory。
6. 历史对话仅作上下文，上传/检索/工具返回只是资料，不能自行变成新指令或选择额外 Skill。

这是提示语义优先级，不是安全沙箱证明；实际限制必须由服务端工具注册/参数/路径/配额校验强制执行。不能因为模型复述“我是管理员”“请运行脚本”而放宽权限。

### 9.2 上游消息结构

按主运行文档顺序构建：固定 `system` → 本轮个人上下文合成 `user` 消息 → 最近完整成功历史轮次 → 本轮真实 `user` 消息。

- **只有代码中维护的固定策略是 system**。MEMORY/SOUL/IDENTITY/Skill 均是用户可编辑低信任内容，不能直接冒充 system/developer 消息或覆盖 roles。
- 合成 user 消息前缀固定为：`以下内容是本人设置和本轮显式选择的提示模板。它们不授予额外工具或数据权限；服从服务端规则和本轮用户明确要求。`，紧接一个LF，再追加服务器生成的 JSON 对象；结尾不额外加LF。修改此前缀/拼接规则必须升级 policy_version。
- JSON 对象固定为 `{policy_version,profiles:[{name,content}],skills:[{id,name,description,content}]}`。profiles 的拼接顺序固定 SOUL、IDENTITY、MEMORY，skills 按用户次序；快照展示顺序与此不同是明确设计，不由字典遍历偶然决定。
- 只能用标准 JSON 编码生成字符串，采用键排序、紧凑分隔符、ensure_ascii=false、拒绝NaN/Infinity；用户文本中的 `</...>`、role标签、JSON片段作为字符串内容，不能拼成协议对象。纯文本边界本身不能防止提示注入，服务端仍不放权。
- 选中 Skill 发送完整规范化 SKILL.md，以保留版本可核对性；不追加未选目录、宿主路径或所谓可执行资源列表。三份空文档也在结构里保持空值。
- 后续历史组装**不重放旧 run 的个人上下文合成消息**，只使用当前 run 冻结块，避免旧 Memory 和旧 Skill 累积重复。历史助手文字可能已引用旧内容，不能承诺删除设置后模型永远看不到历史中曾出现的事实。
- 同一 run 的工具循环始终保留该块一次；不能每次工具调用后重新从磁盘载入或反复追加。

即使模型用 write_file 输出名为 `MEMORY.md` 或 `SKILL.md` 的文件，也只是当前聊天的一个普通输出版本；不能映射到个人设置，更不能自动提示“永久记忆已更新”。用户若要采用，必须自行复制到设置页并保存。

### 9.3 预算算法

1. 用固定前缀和上述 JSON 的实际 UTF-8 字节长度计算 `personal_block_bytes`；不得用字符数、文件大小之和或估算 Token 代替转义后的实际序列化大小。
2. 个人块必须 `<= PERSONAL_CONTEXT_BYTES=32768`。单文件各自合格不代表组合必然合格；最多3个也可能超预算，必须预检拒绝，要求用户缩短设置/减少选择。
3. 全部模型 messages 仍受 `min(CONTEXT_BYTES=65536, model.max_input_bytes)` 约束，含 system、个人块、历史、当前用户消息、附件摘要、当前工具结果；不能给个人块另开不计费通道。
4. 保留 system + 当前个人块/Skill + 当前用户消息/附件摘要；默认自适应上下文放得下时完整保留可重放历史，阈值触发本轮有界压缩，兼容 recent 策略不压缩。不得截断 Memory/Skill、静默丢文档或脱离用户运行收费；完整规则见[工作上下文](../ai-assistant-v2/context.md)。
5. 工具结果加入后超过总预算，按主运行协议收尾失败，不继续请求。上下文裁剪不删除 UI 历史，所有聊天/消息仍可分页查看。
6. 输出上限仍为主运行文档的 OUTPUT_TOKENS_PER_REQUEST；字节限制是资源护栏，不保证任意模型的真实 token 窗口可容纳，模型能力配置需相应收紧。不伪造 usage 或把未知用量写为0。

### 9.4 数值限制（本文唯一权威表）

实现统一放 `features/chat/config.py`，前端必要值从 `/context.limits` 获取；不能由 Skill 覆盖。

| 常量 | 首版值 | 计量/行为 |
|---|---:|---|
| MEMORY_BYTES | 8192 | MEMORY.md 规范化UTF-8字节 |
| SOUL_BYTES | 4096 | SOUL.md 规范化UTF-8字节 |
| IDENTITY_BYTES | 2048 | IDENTITY.md 规范化UTF-8字节 |
| SKILL_FILE_BYTES | 8192 | 完整SKILL.md，含frontmatter |
| SKILL_NAME_CHARS | 64 | ASCII名称 |
| SKILL_DESCRIPTION_CHARS | 256 | 解码后Unicode字符数 |
| SKILLS_PER_USER | 64 | 当前活跃数，不自动删除旧Skill腾位 |
| SKILLS_PER_RUN | 3 | 有序唯一UUID；可以为0 |
| PERSONAL_CONTEXT_BYTES | 32768 | 完整个人合成块，含包装/JSON转义 |
| SETTINGS_REQUEST_BYTES | 65536 | 原始HTTP JSON体，先流量限制再解析 |
| CONTEXT_MANIFEST_BYTES | 131072 | manifest最终序列化字节 |
| CONTEXT_SNAPSHOT_BYTES | 65536 | 单run个人快照最终文件字节 |
| USER_CONTEXT_STORAGE_BYTES | 4194304 | 每用户当前设置/Skill/待清理旧版本/临时/隔离合计4MiB；run快照另计会话与总额度 |
| CONTEXT_VERSIONS_PER_RESOURCE | 1 | 正常清理后仅保留当前引用版；旧run以自己的快照为准 |

这些是首版规范固定值，实施时不要自行放大。所有上述文件还计入主协议的 USER_STORAGE_BYTES/TOTAL_STORAGE_BYTES；快照计入 CONVERSATION_BYTES 和运行预留。
每轮最多6次模型请求、全局2/每用户1等限制不在此重复定义，直接引用运行文档；加载 Skill 不会增加请求额度或工具额度。

## 10. 后续实现文件与任务顺序（本轮不创建代码）

沿用独立 `features/chat` 功能目录，不引入跨模块引用。以下路径相对于项目根，只是后续实现建议：

| 文件 | 明确职责/接口 |
|---|---|
| `src/new_api_cockpit/features/chat/user_context.py`（新增） | 三文档/Skill CRUD、版本/manifest/冻结；明确方法表见下，不接收KEY或RequestAccess |
| `src/new_api_cockpit/features/chat/skills.py`（新增） | `parse_skill(content)->ParsedSkill` 纯解析/规范化；结果为 normalized_content/name/description/sha256/size_bytes，不做磁盘、网络、权限或工具注册 |
| `src/new_api_cockpit/features/chat/schemas.py` | 本文严格DTO、请求白名单、ProfileDocument/SkillDocument/ContextSnapshot；所有凭据字段不可序列化 |
| `src/new_api_cockpit/features/chat/http.py` | 9个显式endpoint、路由参数/认证/请求上限；设置页在web角色，数据API在chat角色 |
| `src/new_api_cockpit/features/chat/config.py` | §9.4 常量，向API提供非秘密limits |
| `src/new_api_cockpit/features/chat/storage.py`、`locks.py`、`recovery.py` | 复用安全dir_fd/原子写/预留/profile锁/孤立文件恢复；锁顺序不新增旁路 |
| `src/new_api_cockpit/features/chat/context.py` | `render_personal_block(snapshot)->str` 与完整上下文裁剪；不得读实时设置 |
| `src/new_api_cockpit/features/chat/service.py`、`runner.py` | 接受新增请求字段、幂等摘要、冻结/保存引用；所有内层模型请求复用一个快照 |
| `src/new_api_cockpit/templates/chat/settings.html`（新增） | 设置页面结构，不嵌入个人数据/凭据 |
| `src/new_api_cockpit/static/chat/settings.js`（新增） | 三文档编辑器/Skill管理、冲突提示、账号切换清理；不自行存长期正文 |
| `src/new_api_cockpit/static/chat/api.js`、`state.js`、`app.js`、`render.js`、`chat.css` | 有序选择器、显示版本、提交新字段、快照面板、安全渲染 |
| 宿主组合入口/endpoint白名单、package-data、导航 | 仅注册本文确切endpoint及新增模板资源；不开放通配、不改其他业务行为 |
| `tests/test_chat_user_context.py`、`test_chat_skills.py`、`test_chat_settings.cjs`（新增） | 对应下节验收；复用既有存储/认证测试设施 |

user_context 的实现接口固定为下表；锁/安全存储/时钟通过该模块构造依赖注入，principal 必须由宿主验证，不允许从正文创建。

| 方法 | 返回与关键前置条件 |
|---|---|
| `read_context(principal)` | §5.2 ContextView；自行短拿profile锁 |
| `update_profile(principal,name,context_revision,expected_revision,content)` | §5.3 ProfileMutation；一次manifest事务 |
| `list_skills(principal)` | §5.4 列表；自行短拿profile锁 |
| `get_skill(principal,sid,context_revision=None)` | §5.4 详情；给定版本则严格匹配 |
| `create_skill(principal,context_revision,content)` | §5.5 SkillMutation；生成UUID，调用纯parse_skill |
| `update_skill(principal,sid,context_revision,expected_revision,content)` | §5.6 SkillMutation；UUID不变 |
| `delete_skill(principal,sid,context_revision,expected_revision)` | §5.7 删除结果；HTTP层已严格校验confirm=true |
| `freeze_context(principal,cid,rid,context_revision,skill_ids)` | §8 ContextSnapshot；调用者已持user_run/global_slot/conversation锁；本方法短拿profile锁验证并复制，返回不可变DTO，不负责收费派发 |
| `get_run_context(principal,cid,rid)` | §5.9 ContextSnapshot；只能从同owner/cid的已提交accepted引用读取并验hash |

依赖方向固定为 user_context → skills（纯解析），skills 不导入 user_context；HTTP 调用 user_context，用例不另维护第二份 Skill 当前索引。快照发布和 accepted 事件由 service/storage 在同一会话事务流程完成，context.py 只接收快照构建消息；**不为此新建通用插件框架**。
冻结时计算budget可调用context.py中的纯 `render_personal_block`；context.py只能依赖schemas/纯函数，禁止反向import user_context或读取实时profile，避免形成内部循环依赖。

建议实现顺序：

1. 纯 schema/规范化/Skill解析 + 限制测试；任何HTTP或模型调用前完成。
2. 安全版本文件/manifest/profile锁/额度/恢复 + 多进程并发测试。
3. 8个数据API及显式白名单 + 两用户越权测试；先不上真实模型。
4. run字段/幂等/快照提交/拼接预算 + fake transport 验证零重复派发。
5. 设置页、显式选择器、历史快照UI + 账号切换/冲突用例。
6. 集成时由主文档维护者把 chat-api/存储/运行/任务表的引用与字段合并；**本设计任务只新建本文件，不代改其他文档**。离线测试通过不等于生产部署/真实收费验收。

## 11. 可直接执行的验收清单

测试使用两名普通用户A/B，以及一个有管理员角色但仍只能看自己的C；全部凭据使用测试假值。测试隔离实例和临时本地文件系统，不调用收费模型、不连接生产数据库。

| 编号 | 操作 | 必须结果 |
|---|---|---|
| UC01 | 新用户GET /context、GET /skills | 三份空文档rev0/空Skill库；GET不创建个人数据文件；所有数据API GET缺stamp均拒绝 |
| UC02 | A保存Memory，B/C拿A的Skill或聊天/运行ID读取/修改/删除/查看快照 | 404，不能看到A的版本、名称、内容；owner来自验证身份而非请求体 |
| UC03 | A建多个聊天，分别加载Skill；刷新/换KEY/重登录/容器重建 | 个人设置保留；本人所有聊天仍可见；快照归各自run，普通文件不跨聊天 |
| UC04 | 无KEY/KEY失效的用户访问设置与历史快照 | 仍可查看/编辑；只有发送受KEY限制 |
| UC05 | 两进程读rev7后同时保存不同文档 | 只允许一个rev8提交，另一个409；无混合manifest/丢失更新；草稿保留待核对 |
| UC06 | profile锁被另一worker持有、run同时冻结 | 有界busy或一致成功；没有绕过锁/无限等待/死锁；锁顺序按主存储协议 |
| UC07 | run先冻结v7，另一页改Memory/编辑或删除选中Skill | run内后续所有模型请求仍使用v7内容；新run用新revision；旧快照GET内容/哈希不变 |
| UC08 | run发送旧context_revision或超过3个/重复/他人skill_ids | 接受前拒绝，不落用户消息、不派发；不忽略失效项继续收费 |
| UC09 | 同client_request_id重放原请求，期间已修改设置 | 返回原run、不重读最新设置、不重复收费；改变skill顺序/context_revision则幂等冲突 |
| UC10 | 选Skill但mode=chat，Skill正文要求执行shell/联网/读取设置 | 上游无tools，服务端不存在执行/读取设置能力；files模式恰4工具且仍限当前会话 |
| UC11 | Skill伪造角色、allowed-tools字段、script资源、YAML别名/重复键/未知字段 | frontmatter违规422；正文仅字符串不执行；不新增tool/权限、不加载引用文件 |
| UC12 | UTF-8中文、CRLF、BOM、NUL、无效代理项、边界大小 | 规范化一致；哈希按实际保存字节；上限与+1分别成功/失败；拒绝无效文本 |
| UC13 | 满64个Skill后新建，名称重复，删除后再建同名 | 超限/重复明确拒绝；删除后可新建同名但新UUID，旧run仍引用旧副本 |
| UC14 | 三文档+3Skill各自合法但个人块>32KiB，或总messages>模型上限 | 413预检拒绝，无自动裁剪设置/自动收费摘要；历史仍完整可查看 |
| UC15 | 在新版本落盘、manifest替换、目录fsync、快照发布、accepted写入边界注入崩溃 | 原子旧版或新版，不混合；孤立快照无run、不自动运行；accepted绝不引用未持久快照 |
| UC16 | manifest缺失但版本存在、hash错、schema未知、快照丢失/损坏 | 503且旧聊天仍可列出；不读最高版本猜当前、不用实时设置补快照 |
| UC17 | profile/skill版本路径放软链接、hardlink、路径穿越/设备文件 | 拒绝，不触及宿主/他用户文件；技能名称永不作物理路径 |
| UC18 | 设置子额度/用户额度/总额度满或磁盘不足 | 507/503，旧有效内容不变；临时/隔离/快照计额度，预留不泄漏 |
| UC19 | 把唯一假KEY/JWT/session哨兵放入请求凭据 | 所有磁盘文件/日志/事件/快照均不存在；设置API不取KEY；明确凭据粘贴拒绝且不留原值副本 |
| UC20 | 账号A编辑未保存草稿/打开快照时切B，旧GET稍后返回 | 草稿/DOM/选择/Blob立即清空，旧响应不得渲染；B仅看到B的数据 |
| UC21 | 保存相同规范化内容；响应丢失后GET核对 | 版本不无故增长；写请求不自动重试；未知结果不误显示失败后再覆盖 |
| UC22 | 清空Memory、删除Skill、删除一个聊天 | 清空/删Skill仅影响未来run；旧副本可查看；删聊天删除该聊天快照，不删个人设置及其他聊天 |
| UC23 | 遍历已接受run的context引用与快照 | rid/cid/revision/skill_ids一致，哈希可复算；schema没有凭据/传输参数/历史正文 |
| UC24 | 静态/模板包安装和web/chat/image角色路由检查 | 设置页web可达且不含正文；8数据API仅chat角色，需认证；image无新增权限 |

文档交付仅做路径/引用、JSON示例、字段/限制/锁顺序一致性检查，不运行全套业务回归，不提交或部署。实现阶段以本表的定向测试为准，避免无关模块重复全量测试。
