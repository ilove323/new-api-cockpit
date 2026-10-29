# 模型使用

入口：`/cockpit/manual`。登录后选择自己的模型 KEY 和模型，再按客户端教程配置。
账户 MCP、VS Code 余额插件放在[辅助工具](tools.md)，不与模型接入混用。

## 页面操作

1. 选本人令牌，读取模型，选择要用的模型 ID。
2. 用工具下拉框选择客户端。列表保留全部十五个客户端，选中后显示完整适用范围和协议。
3. 选择 macOS、Windows 或 Linux / WSL。Claude Code、Codex、CodeBuddy 还可以选择 CLI、桌面或扩展等接入方式，只展示该方式的步骤。
4. 在对应步骤复制地址、KEY、配置文件或启动命令，保存后到客户端选择模型。

工具选择不再铺开十五张卡片。令牌详情与“接入配置说明”默认收起，需要时展开。
切换工具、系统、接入方式、协议只更新本地教程，不重新读取令牌、模型或 KEY。
模型列表不自动选择第一项，也不根据模型名称猜测支持的协议。

### 复制什么，填在哪里

- **图形界面**：按行展示字段名和值，每行可单独复制。地址只复制地址，模型只复制 ID，KEY 行明确标注“复制 KEY”；也可复制全部字段作核对，但字段清单不是可导入的文件。
- **配置文件**：示例放在修改该文件的步骤内，标题显示保存路径。JSON、TOML、YAML 分别复制，不混在一个按钮里。同名连接或模型项替换，其他已有设置保留。
- **终端命令**：放在执行该命令的步骤内，按钮显示“复制命令”。复制不会自动安装、启动或调用模型。
- **含 KEY 的片段**：只有明确点击对应复制按钮才读取本人 KEY。预览始终是 `API_KEY`；复制地址、Provider、模型 ID、安装或启动命令不会读取 KEY。

每项示例只出现一次，不需要到文末寻找。切换选择、账号或离开页面会取消尚未完成的复制；失败不显示明文 KEY，也不自动重发。

## 三种协议

本站地址从当前页面的 `location.origin` 取得，保留 HTTP/HTTPS、域名和端口，不带 `/cockpit`。

| 协议 | 请求接口 | 说明 |
| --- | --- | --- |
| OpenAI Chat Completions | `/v1/chat/completions` | 不是 Responses 的别名，不能只改 URL 而保留另一种请求体 |
| OpenAI Responses | `/v1/responses` | Codex 原生使用此接口；客户端还需要正确处理流式事件和工具调用 |
| Anthropic Messages | `/v1/messages` | Claude Code、Claude Desktop Gateway 使用此接口 |

协议下拉框始终列出三种格式，但只启用当前客户端和接入方式支持的选项。
有多个可用协议时，改变选择会同步改变地址、配置字段和请求结构。
`/v1/models` 只证明该 KEY 能看到某个模型，不证明其渠道支持所有协议、图片或工具调用。
客户端能配置一种协议，也不代表本站所有模型都能用这种协议。教程不以模型名做白名单限制，不会自动发送验证请求。

## 客户端和地址

下表按 **2026-10-30** 的官方文档及维护者源码核对。每个网页教程同时保留官方原文链接。
“根地址”例如 `https://gateway.example.com`；`/v1` 和完整接口路径已由页面按客户端要求生成，不要再补一次。

| 客户端 / 接入方式 | 支持的协议 | 地址与配置入口 | 官方资料 |
| --- | --- | --- | --- |
| Claude Code CLI | Messages | `ANTHROPIC_BASE_URL` 填根地址；在同一个终端配置并启动 | [网关](https://code.claude.com/docs/en/llm-gateway-connect)、[安装](https://code.claude.com/docs/en/setup) |
| Claude Code VS Code 扩展 | Messages | VS Code **用户设置 JSON** 的 `claudeCode.environmentVariables`，不是项目设置或另一个终端的 export | [扩展](https://code.claude.com/docs/en/vs-code) |
| Codex CLI / 桌面 / IDE 扩展 | Responses | `/v1`；同一个 `CODEX_HOME` 中的 `config.toml` + `auth.json` | [提供商](https://learn.chatgpt.com/docs/config-file/config-advanced#custom-model-providers)、[认证](https://learn.chatgpt.com/docs/auth#credential-storage) |
| WorkBuddy 桌面端 | Chat | 设置 → 模型 → 自定义；开启高级“自定义协议”后填完整 `/v1/chat/completions` | [模型设置](https://www.workbuddy.cn/docs/workbuddy/From-Beginner-to-Expert-Guide/Function-Description/Model) |
| Hermes Agent | Chat / Responses / Messages | `config.yaml` 的 `providers.newapi`；OpenAI 用 `/v1`，Messages 用根地址；`transport` 分别为 `chat_completions` / `codex_responses` / `anthropic_messages` | [自定义提供商](https://github.com/NousResearch/hermes-agent/blob/main/website/docs/integrations/providers.md#named-custom-providers) |
| Claude App / Claude Desktop | Messages | Developer → Configure Third-Party Inference → Gateway；根地址、Static API key、Bearer | [Anthropic 官方 Gateway](https://claude.com/docs/third-party/claude-desktop/gateway) |
| Cursor 桌面编辑器 | Chat | Settings → Models；OpenAI KEY、Override OpenAI Base URL（`/v1`）、Add Custom Model | [BYOK](https://cursor.com/help/models-and-usage/api-keys)、[维护者说明](https://forum.cursor.com/t/cursor-cannot-connect-to-the-third-party-relay-station/164487/5) |
| CC Switch | 三种上游协议；非原生格式需本地转换 | 先选 Claude Code / Codex 目标；Messages 上游用根地址，OpenAI 用 `/v1`；非原生格式启用 Routing | [维护者手册](https://github.com/farion1231/cc-switch/blob/main/docs/user-manual/en/2-providers/2.1-add.md) |
| TRAE 中文版 | Chat / Messages | 设置 → 模型 → 添加模型 → 自定义模型；打开“完整 URL”，按协议填完整接口 | [模型设置](https://docs.trae.cn/ide_models) |
| 独立 Qoder 应用 | Chat / Responses / Messages | Settings → Models → Add model → Custom；Base URL 用 `/v1`；OpenAI Compatible 内另选 Chat 或 Responses | [独立应用](https://docs.qoder.com/qoder/custom-models) |
| Chatbox | Chat / Responses / Messages | 自建供应商选择对应类型；API Host 用 `/v1`；OpenAI 另填 `/chat/completions` 或 `/responses`，Claude 类型自行补 `/messages` | [Chat](https://github.com/chatboxai/chatbox/blob/main/src/shared/providers/definitions/models/custom-openai.ts)、[Responses](https://github.com/chatboxai/chatbox/blob/main/src/shared/providers/definitions/models/custom-openai-responses.ts)、[Messages](https://github.com/chatboxai/chatbox/blob/main/src/shared/providers/definitions/models/custom-claude.ts) |
| OpenClaw | Chat / Responses / Messages | `openclaw.json` 的 `models.providers.newapi`；OpenAI 用 `/v1`，Messages 用根地址 | [自定义提供商](https://docs.openclaw.ai/gateway/config-tools/custom-providers) |
| Pi Agent | Chat / Responses / Messages | `models.json` 的 `providers.newapi`；OpenAI 用 `/v1`，Messages 用根地址；显式设置 `api` | [模型连接](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/models.md)、[配置字段](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/models-configuration.md) |
| DeepSeek Harness | Chat / Responses / Messages | 本地 Web UI → 设置 → 模型 → 添加模型提供商 → 自定义模型 API；OpenAI 用 `/v1`，Messages 用根地址 | [DeepSeek 官方教程](https://github.com/deepseek-ai/deepseek-harness/blob/master/docs/user/guide/providers.zh.md) |
| CodeBuddy IDE / VS Code 插件 | Chat | 用户级 `models.json`；`url` 用完整 `/v1/chat/completions` | [官方配置结构](https://www.codebuddy.ai/docs/cli/models)、[腾讯 CloudBase 接入](https://docs.cloudbase.net/ai/ai-tools/codebuddy) |
| CodeBuddy Code CLI | Chat / Responses；Messages 使用独立环境变量入口 | Chat：完整 URL；Responses：`api: openai-responses` 和 `/v1`；Messages：`CODEBUDDY_BASE_URL` 根地址 + `CODEBUDDY_API_KEY` | [Responses](https://www.codebuddy.ai/docs/cli/models#openai-responses-api-configuration-example)、[环境变量](https://www.codebuddy.ai/docs/cli/env-vars) |
| curl | Chat / Responses / Messages | 完整请求 URL、对应认证头和请求体 | [OpenAI 两种接口](https://developers.openai.com/api/docs/guides/migrate-to-responses)、[Messages](https://platform.claude.com/docs/en/api/messages/create)、[curl](https://curl.se/docs/manpage.html) |

Cursor 的 BYOK 不覆盖 Tab 补全。Qoder 独立应用和 Qoder IDE 的入口不同，不把本站 KEY 填进无法修改 URL 的厂商预置提供商。
CC Switch 的协议转换由本机 Routing 完成，Codex 的 `wire_api` 仍为 `responses`，不是改成 `chat`。
CodeBuddy 的 Responses 和 Messages 本教程明确限定为 **CLI 方式**，不据此宣称 IDE / 插件也支持。

## Windows、Linux 和 macOS

页面分开选择三个系统，不再把 macOS 与 Linux 合成一个选项。

| 客户端 | macOS | Windows 原生 | Linux / WSL |
| --- | --- | --- | --- |
| Claude Code | CLI / 扩展 | CLI / 扩展，命令用 PowerShell | CLI / 扩展 |
| Codex | CLI / 桌面 / IDE | CLI / 桌面 / IDE | CLI / IDE；[桌面版为预览](https://learn.chatgpt.com/docs/linux/linux-app) |
| WorkBuddy | 官方桌面版 | 官方桌面版 | 按官方[统信 UOS](https://www.workbuddy.cn/docs/workbuddy/From-Beginner-to-Expert-Guide/Installation-UOS-Guide) / 银河麒麟说明，不概括为任意发行版 |
| Hermes Agent | CLI | [原生安装](https://github.com/NousResearch/hermes-agent#quick-install)，不强制 WSL | CLI；WSL 使用 Linux 用户目录 |
| Claude Desktop | 桌面 Gateway | 桌面 Gateway | [Linux beta](https://code.claude.com/docs/en/desktop-linux)；先检查安装版是否提供 Gateway 入口 |
| Cursor / TRAE / Qoder / Chatbox | 桌面版 | 桌面版 | 按官方下载页选择发行版和架构，不把桌面版等同于无图形服务器 |
| CC Switch | 原生桌面版 | 原生桌面版 | 当前要求 glibc 2.35+、WebKitGTK 4.1；RHEL / Rocky / Alma 8–9 不在[支持范围](https://github.com/farion1231/cc-switch#installation) |
| OpenClaw | CLI / Control UI | [原生 CLI](https://docs.openclaw.ai/platforms/windows)；也可选择 WSL | CLI / Control UI |
| Pi Agent | CLI | [原生运行需 Bash 环境](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/windows.md)，可用 Git for Windows；也可 WSL | CLI |
| DeepSeek Harness | 本地 Web UI | 本地 Web UI | 本地 Web UI；[官方 npm 启动方式](https://github.com/deepseek-ai/deepseek-harness#run) |
| CodeBuddy | IDE / 插件 / CLI | IDE / 插件 / CLI | 按官方安装要求选择包或 CLI |
| curl | Bash / Zsh | **PowerShell 7.3+ + curl.exe** | Bash |

WSL、SSH、容器或远程扩展的配置写在**客户端实际运行的环境**里。Windows 本机配置不会自动传到 WSL / Linux。
桌面图标启动的应用也不会自动继承另一个终端里的 `export` 或 `$env:`。

### 常见配置位置

下表是未自定义配置目录、普通用户安装的默认位置；网页会随系统切换显示。

| 内容 | macOS | Linux | Windows |
| --- | --- | --- | --- |
| Codex | `~/.codex/config.toml`、`auth.json` | 同左 | `%USERPROFILE%\.codex\config.toml`、`auth.json` |
| Hermes | `~/.hermes/config.yaml` | 同左 | `%USERPROFILE%\.hermes\config.yaml` |
| Pi | `~/.pi/agent/models.json` | 同左 | `%USERPROFILE%\.pi\agent\models.json` |
| CodeBuddy | `~/.codebuddy/models.json` | 同左 | `%USERPROFILE%\.codebuddy\models.json` |
| OpenClaw | `~/.openclaw/openclaw.json` | 同左 | `%USERPROFILE%\.openclaw\openclaw.json` |
| Claude Code 的 VS Code 扩展 | `~/Library/Application Support/Code/User/settings.json` | `~/.config/Code/User/settings.json` | `%APPDATA%\Code\User\settings.json` |

`CODEX_HOME`、`HERMES_HOME` / profile、`PI_CODING_AGENT_DIR` 等自定义目录优先；VS Code Profile、Remote SSH、WSL 以 **Preferences: Open User Settings (JSON)** 实际打开的位置为准。
修改前备份。文件不要多出 `.txt` 后缀；JSON 不加注释或尾随逗号，TOML 顶层字段放在表头之前。
macOS/Linux 将含 KEY 的文件设为 `600`；Windows 限本人访问，不放到项目仓库。

## 各步骤中的配置

| 客户端 / 方式 | 放置位置 |
| --- | --- |
| Claude Code CLI | 第 2 步：当前终端连接；第 3 步：本次启动命令 |
| Claude Code VS Code 扩展 | 第 3 步：用户 settings.json，不显示 CLI 环境步骤 |
| Codex CLI | 第 2 步：config.toml；第 3 步：auth.json；第 5 步：启动命令 |
| Codex 桌面 / IDE | 第 2、3 步：两个文件；第 5 步：相应界面的重启和选模说明，不显示 CLI 启动命令 |
| WorkBuddy | 第 2 步：供应商；第 3 步：完整 URL、KEY 和模型 |
| Hermes | 第 2 步：providers.newapi；第 4 步：指定提供商与模型的命令 |
| Claude Desktop | 第 2 步：Gateway；第 3 步：Models |
| Cursor | 第 1 步：KEY；第 2 步：地址；第 3 步：模型 |
| CC Switch | 第 2 步：连接字段；第 4 步：目标客户端启动命令 |
| TRAE / Qoder / Chatbox / OpenClaw | 保留各自配置、保存和选模步骤，文件或表单字段紧跟对应步骤 |
| Pi Agent | 第 1 步：安装命令；第 3 步：models.json；第 4 步：启动命令 |
| DeepSeek Harness | 第 1 步：本机 Web UI 启动；第 3 步：提供商字段；第 4 步：模型目录 |
| CodeBuddy IDE / 插件 | 第 3 步：models.json；第 4 步：会话模型 ID |
| CodeBuddy CLI | 第 3 步：Chat / Responses 文件或 Messages 环境变量；第 4 步：启动命令 |
| curl | 第 3 步：对应终端和协议的完整请求 |

## 几个容易弄错的地方

### Codex 仍然是 config.toml + auth.json

`config.toml` 设置 `model_provider = "newapi"`、`cli_auth_credentials_store = "file"`；Provider 内写 `/v1` 地址、`wire_api = "responses"` 和 `requires_openai_auth = true`。
`auth.json` 单独保存 `auth_mode: apikey` 和 `OPENAI_API_KEY`，使用本人模型 KEY，不是用户 PAT；字段结构见 [OpenAI 源码](https://github.com/openai/codex/blob/main/codex-rs/login/src/auth/storage.rs)。
两个文件在同一个 `CODEX_HOME`。CLI 用 `--model` 选择当前会话；教程不写顶层 `model`，也不覆盖推理、上下文或搜索设置。
自定义模型别名若不在菜单中，需要匹配的模型目录元数据，不能承诺仅写入连接就一定会显示。
`codex login status` 只检查认证来源，不证明网关模型已验通。

### Pi Agent 不是旧包名

安装按[当前官方 README](https://github.com/earendil-works/pi/tree/main/packages/coding-agent#readme)，npm 包为 `@earendil-works/pi-coding-agent`，要求 Node.js 22.19+。
`models.json` 只合并 `providers.newapi`，用 `pi --provider=newapi --model=...` 启动；不设置默认模型、价格、上下文或推理能力。
Pi 的密钥字符串支持环境插值和命令解析，生成器按[官方转义规则](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/models.md)转义字面 `$` 和开头的 `!`，避免 KEY 被当成变量或命令。

### DeepSeek Harness 不把本站 KEY 填进内置 DeepSeek 卡片

先启动本地 Web UI，然后创建**自定义模型 API**提供商。一个提供商一种协议；Provider ID 创建后不可改名。
手动填写模型 ID 不依赖目录探测成功。设置保存后下一次请求生效，不重启服务。
[官方说明](https://github.com/deepseek-ai/deepseek-harness/blob/master/docs/user/guide/providers.zh.md)指出模型选择也会改变新会话默认值，教程明确提示此行为，不谎称“只影响本次会话”。
已保存凭据只写，教程不生成猜测的凭据 YAML。

### CodeBuddy 的文件与环境变量不是同一个入口

IDE / 插件使用完整 Chat URL 的 `models.json`。CLI 的 Responses 需显式 `api: openai-responses`，`url` 使用 `/v1`；Messages 使用[官方环境变量入口](https://www.codebuddy.ai/docs/cli/env-vars)，不把 Chat 文件简单改成 `/messages`。
同 id 模型替换，不重复追加；不生成 `availableModels`，已有白名单需用户明确加入所选模型。
为避免含 `${...}` 的真实 KEY 被文件解析器展开，生成器拒绝将这种 KEY 写入 CodeBuddy 文件，提示使用客户端安全凭据输入；错误不回显 KEY。

### 不自动修改默认模型

除客户端本身的行为提示外，教程不替用户改默认模型、角色映射、回退模型、允许列表、温度或上下文窗口。
CLI 用本次启动参数，GUI 保存连接后在模型菜单中明确选择。
工具调用、图片、思考等能力按模型和渠道的实际支持设置，不根据名称预填能力开关。

## 权限、KEY 与查询

- 模型客户端使用 `/keys` 中的模型 KEY（通常 `sk-` 开头）。账户 MCP 和余额插件使用 `/security` 中的用户 API PAT（新式通常 `nap_` 开头），两种凭据不能混用。
- 仅列出当前用户自己的令牌；管理员在本页也不能选择其他用户的 KEY。KEY 不进入页面明文、URL、日志或浏览器存储。
- 读取模型仍受令牌分组、模型权限、IP 白名单、额度、状态和有效期限制；不改为全站模型目录，不调整 `auto` 分组处理。
- 不创建 PAT 或令牌，不改额度，不发送模型生成或客户端验证请求。用户手动在客户端对话、测试连接、运行任务时，按实际调用计费。
- 保留现有 15 秒读取 / 复制期限、取消与会话失效处理。模型查询与 KEY 读取不自动重试，不新增轮询或凭据缓存。

| 方法 | 地址 | 用途 |
| --- | --- | --- |
| GET | `/cockpit/api/manual/tokens` | 本人令牌列表，只返回脱敏 KEY |
| POST | `/cockpit/api/manual/models` | 所选令牌的可用模型 |
| POST | `/cockpit/api/manual/key` | 用户明确点击含 KEY 复制后，读取完整 KEY |

POST 仍要求同源会话、`X-Cockpit-Manual: 1`、`X-Cockpit-Session` 和 `{"token_id": 123}`；脚本使用管理员 PAT 时仍限定本人。
不接受另一用户 ID、任意上游地址或模型 KEY 作为 Cockpit 认证凭据。令牌列表最多 2000 项，超出时报错，不静默遗漏。
API 调试器不允许执行这三个接口；无新增数据库表、权限、迁移或后台任务。

## 本地验证

- 纯配置生成与 DOM 用例覆盖三系统、所有接入方式及已启用协议，共 114 组组合；单独检查地址、文件路径、JSON/TOML/YAML、字面值转义和每一步的片段放置。
- 离线浏览器 `tests/browser/manual-regression.cjs` 使用真实模板和资源，覆盖桌面 / 手机布局、十五个工具下拉项、逐字段复制、KEY 不回显，以及切换时不增加上游请求。
- 上述验证不安装或启动十五个真实客户端，不调用真实模型，不等同于逐客户端真实网关验收。

TRAE 的两张官方截图仍为同源静态资源，保持比例并保留出处。截图版权见 `NOTICE`。
