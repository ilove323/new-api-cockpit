# 模型使用

入口：`/cockpit/manual`。普通用户登录后也能进入，用自己的令牌接入客户端；其他管理页面仍只对管理员开放。

## 使用步骤

1. 选择本人令牌，读取模型，再选择实际模型 ID。
2. 选择工具、系统和接口协议。CC Switch 还需要选择目标客户端。
3. 按步骤填写连接信息，在对应步骤复制配置或命令，再到客户端中选择模型。

地址取当前页面的 `location.origin`，保留域名、HTTPS 和端口，不包含 `/cockpit`。
没有令牌时，页面链接到 New API 的 `/keys`，不是管理员的 `/cockpit/keys/`。

模型不自动选第一项，也不根据名称判断 GLM、Claude 或 GPT 能否使用某个接口。
三种协议始终列在下拉框中，按客户端的接入方式启用：Codex 原生用 Responses，Claude Code 原生用 Messages；CC Switch 可以通过本地路由转换协议。
`/v1/models` 返回的协议元数据不作为客户端协议白名单。

## 模型选择与配置

配置里不固定默认模型，不覆盖角色映射、回退模型、允许列表或其他功能参数。
模型 ID 来自用户当前选择，没有写死的 `default`、`sonnet`、`opus` 或示例模型名。

- **Claude Code**：CLI 用 `--model` 选择本次启动的模型，不生成 `ANTHROPIC_MODEL` 或 `ANTHROPIC_DEFAULT_*`。VS Code 的 `claudeCode.environmentVariables` 配置连接；`ANTHROPIC_CUSTOM_MODEL_OPTION` 仅把所选模型加入菜单，不将它设为默认。
- **Codex**：TOML 只设置 Provider 连接，不写顶层 `model`、搜索开关、推理强度或上下文窗口。CLI 用单独的 `--model` 启动命令。桌面应用和 IDE 扩展通过会话模型菜单选择；自定义别名需要对应模型目录才能显示。
- **CC Switch**：只生成连接字段和目标工具的启动命令。不生成 Codex 模型映射表，因为其第一行会被 CC Switch 用作默认模型。
- **OpenClaw**：仅合并 `models.providers.newapi` 的连接和模型目录。不写 `agents.defaults` 或 `models.mode`；在聊天模型菜单中选择模型。
- **Cursor、TRAE、Qoder、Chatbox**：添加所选模型后，在会话模型菜单中选择，不修改全局默认模型。
- **curl**：请求体的 `model` 是这一次请求的模型，不是客户端默认配置。

CLI 启动命令不含 KEY，复制后不会读取 KEY，也不会自动执行。

### 配置示例的位置

| 客户端 | 对应步骤 |
| --- | --- |
| Claude Code | 第 1 步：终端环境；第 2 步：启动命令；第 3 步：VS Code 用户设置 |
| Codex | 第 2 步：config.toml；第 3 步：auth.json；第 5 步：启动命令 |
| WorkBuddy | 第 2 步：供应商；第 3 步：URL、KEY、模型 |
| Hermes | 第 2 步：config.yaml；第 4 步：启动命令 |
| Claude App | 第 2 步：Gateway 连接；第 3 步：Models |
| Cursor | 第 1 步：KEY 与开关；第 2 步：网关地址；第 3 步：模型 ID |
| CC Switch | 第 2 步：连接字段；第 4 步：目标客户端启动命令 |
| TRAE | 第 2 步：协议、完整 URL、模型 ID 和 KEY |
| Qoder | 第 2 步：供应商、地址和 KEY；第 3 步：模型 ID |
| Chatbox | 第 1 步：供应商协议；第 2 步：Host / Path；第 3 步：KEY 和模型 |
| OpenClaw | 第 2 步：openclaw.json；第 3 步：会话模型引用 |
| curl | 第 3 步：请求命令 |

账户 MCP 的地址、客户端配置和 PAT 环境变量说明放在第 2 步“添加远程 HTTP MCP”中。


## 工具设置

| 工具 | 协议和地址 | 操作入口 | 官方资料 |
| --- | --- | --- | --- |
| Claude Code | Messages，根地址 | CLI 环境变量；扩展使用 VS Code 用户设置中的 `claudeCode.environmentVariables` | [网关配置](https://code.claude.com/docs/en/llm-gateway-connect)、[模型参数](https://code.claude.com/docs/en/model-config) |
| Codex | Responses，根地址 + `/v1` | 用户 `config.toml` 的 Provider + 同目录 `auth.json` 的 KEY；`--model` 选择本次模型 | [Provider](https://learn.chatgpt.com/docs/config-file/config-advanced#custom-model-providers)、[CLI 参数](https://learn.chatgpt.com/docs/cli/reference) |
| WorkBuddy | Chat，完整 `/v1/chat/completions` | 桌面设置 → 模型 → 自定义 / Custom；打开高级“自定义协议”，原样使用完整 URL | [官方模型配置](https://www.workbuddy.cn/docs/workbuddy/From-Beginner-to-Expert-Guide/Function-Description/Model) |
| Hermes Agent | Chat，根地址 + `/v1` | 当前 profile 的 `config.yaml`，`model.provider: custom`；KEY 放 `model.api_key`，本次启动用 `--model` | [官方配置示例](https://github.com/NousResearch/hermes-agent/blob/main/cli-config.yaml.example) |
| Claude App（桌面） | Messages，根地址 | Help → Troubleshooting → Enable Developer Mode；Developer → Configure Third-Party Inference → Gateway；Static API key + Bearer | [官方集成指南](https://developers.cloudflare.com/ai-gateway/integrations/coding-agents/claude-desktop/) |
| Cursor | Chat，根地址 + `/v1` | Settings → Models → OpenAI API Key、Override OpenAI Base URL、Add Custom Model | [KEY 设置](https://cursor.com/help/models-and-usage/api-keys)、[手动添加模型](https://forum.cursor.com/t/cursor-cannot-connect-to-the-third-party-relay-station/164487/5) |
| CC Switch | Messages 上游用根地址；Chat / Responses 上游用根地址 + `/v1` | 选目标客户端 → 自定义配置；原生协议 Direct，跨协议 Routing | [维护者手册](https://github.com/farion1231/cc-switch/blob/main/docs/user-manual/en/2-providers/2.1-add.md) |
| TRAE 中文版 | Chat / Messages，完整接口 URL | 设置 → 模型 → 添加模型 → 自定义模型，打开“完整 URL”；对话手动选模型，不使用 Auto | [模型配置](https://docs.trae.cn/ide_models)、[Auto 模式](https://docs.trae.cn/ide_auto-mode) |
| Qoder 独立应用 | Chat / Responses / Messages，Base URL 为根地址 + `/v1` | Settings → Models → + Add → Provider → Custom；选择 OpenAI Compatible 或 Anthropic Compatible | [独立应用](https://docs.qoder.com/qoder/custom-models)、[IDE 的不同入口](https://docs.qoder.com/zh/user-guide/chat/custom-models) |
| Chatbox | Chat / Responses：Host 为根地址 + `/v1`，Path 为 `/chat/completions` / `/responses`；Messages：Host 为根地址 + `/v1` | 添加自建供应商，分别选择 OpenAI API Compatible、OpenAI Responses API Compatible、Claude API Compatible | [协议选项源码](https://github.com/chatboxai/chatbox/blob/main/src/renderer/components/settings/provider/AddProviderModal.tsx)、[地址处理](https://github.com/chatboxai/chatbox/blob/main/src/shared/utils/llm_utils.ts) |
| OpenClaw | Chat / Responses：根地址 + `/v1`；Messages：根地址 | `openclaw.json` 的 `models.providers`，再在聊天中选择 `newapi/模型ID` | [Provider 字段](https://docs.openclaw.ai/gateway/config-tools/custom-providers)、[会话选模](https://docs.openclaw.ai/concepts/models#choose-a-model-for-a-session) |
| curl | 完整 `/v1/chat/completions`、`/v1/responses`、`/v1/messages` | POSIX shell 或 PowerShell 7.3+ 的 `curl.exe` | [OpenAI 请求](https://developers.openai.com/api/docs/guides/migrate-to-responses)、[Messages 请求](https://platform.claude.com/docs/en/api/messages/create) |

Qoder 独立应用和 Qoder IDE 不是同一个配置入口。只有预置服务商、没有自定义 Base URL 字段的客户端，不能把本站 KEY 填进厂商官方接口。
Chatbox 的 Messages 适配器由客户端追加 `/messages`，与 Claude Code 的地址规则不同。
CC Switch 跨协议接入需要本地 Routing 持续运行；Codex 的 `wire_api` 仍为 `responses`，不能改成 `chat`。

## 接入前检查

- **两种凭据不要混用**：模型客户端使用 `/keys` 中的模型 KEY（通常 `sk-` 开头）；[账户 MCP](mcp.md) 使用 `/security` 中的用户 API PAT（新式通常 `nap_` 开头）。本页不会为 MCP 读取、生成或复制真实 PAT。
- 先检查本人令牌启用、到期、额度、分组、模型限制和 IP 白名单。选择模型后，再按客户端确认的协议配置；没有协议元数据时不猜测。
- 根地址、Base URL 和完整路径各不相同。以本站生成的字段为准，保留 HTTPS 与非默认端口，不重复追加 `/v1`。
- 修改前备份；JSON/TOML/YAML 按各自格式合并，不覆盖整份已有配置，不更改原默认模型、模型映射、温度或上下文窗口。
- 保存后完全退出并重开客户端，再为当前会话选择刚配置的提供商与模型。先核对地址/KEY/模型，再手动发送短消息，最后在本站使用日志核对。客户端测试和任务可能计费，教程不会自动执行它们。

## Codex：config.toml + auth.json

默认目录为 macOS/Linux 的 `~/.codex/` 或 Windows 的 `%USERPROFILE%\.codex\`。
设置 `CODEX_HOME` 后两个文件都放在该目录。先退出客户端，备份两份旧文件；更改认证可能影响共享配置的 CLI 与 IDE。

把以下**连接字段合并**到 `config.toml`，顶层字段放在任何表头前，同名 provider 表只保留一份：

```toml
model_provider = "newapi"
cli_auth_credentials_store = "file"

[model_providers.newapi]
name = "New API"
base_url = "https://gateway.example.com/v1"
wire_api = "responses"
requires_openai_auth = true
```

`requires_openai_auth` 启用 Codex 的 API Key 认证存储格式，实际请求仍发往自定义 `base_url`。
不再生成 `env_key = "NEW_API_MODEL_KEY"`。将网页生成的另一个片段保存为同目录 `auth.json`：

```json
{
  "auth_mode": "apikey",
  "OPENAI_API_KEY": "API_KEY"
}
```

其中 `API_KEY` 替换为本人**模型调用 KEY**，不是 MCP PAT。网页第 2 步直接显示 config.toml，第 3 步显示 auth.json，并各有复制按钮；只有点击 auth.json 的“复制配置（含 KEY）”才读取所选 KEY。
不要和旧 OAuth 字段直接混合；不要把认证文件加入仓库、发给他人或记录到工单。
macOS/Linux 运行 `chmod 600 ~/.codex/auth.json`，自定义目录时相应修改路径；Windows 限本人访问。

重开后运行 `codex login status` 确认认证方式，再用本页生成的 `codex --model='模型ID'` 启动。
此片段不设置顶层 `model`，不覆盖已有默认模型、搜索、推理或上下文设置。桌面与 IDE 在会话菜单选模。
如果地址或 KEY 没生效，检查 `CODEX_HOME`、项目级配置、环境中的 `OPENAI_API_KEY` / `CODEX_API_KEY`、启动参数和组织策略。
`login status` 成功不等于网关模型已验通；真实对话后仍须核对本站日志。

依据：[官方认证与文件存储](https://learn.chatgpt.com/docs/auth#credential-storage)、[提供商认证](https://learn.chatgpt.com/docs/auth#alternative-model-providers)、[配置字段](https://learn.chatgpt.com/docs/config-file/config-advanced#custom-model-providers)。

## 新增客户端的范围

### WorkBuddy

使用桌面端的自定义模型 UI。完整 URL 模式下须开启高级“自定义协议”，否则客户端会校验并自动补齐标准路径。
保存后在任务的自定义模型分组中明确选中连接；已有任务不保证自动切换。教程提供字段清单，不伪造 models.json 导入格式。
KEY 由客户端保存在本机，模型产生的费用由本站账户承担，与产品积分分开。

### Hermes Agent

使用 NousResearch Hermes Agent 的当前 profile 配置，不是任意同名客户端。
合并到 `~/.hermes/config.yaml` 的 `model` 表；自定义 home/profile 时修改对应目录，不重复添加同名表：

```yaml
model:
  provider: "custom"
  base_url: "https://gateway.example.com/v1"
  api_key: "API_KEY"
```

不填写默认模型，使用网页生成的 `hermes --model='模型ID'` 启动本次会话。
检查环境/启动参数覆盖，保护含 KEY 的 YAML；Windows 按官方支持方式使用 WSL 的 home。
`auxiliary` 中标题、压缩、视觉等可能独立配置提供商；仅配置主模型不意味着它们全部改走本站。

### Claude App（桌面）

范围是提供 Third-Party Inference 入口的 macOS/Windows 桌面版，不包含网页或手机端。
更新并启用 Developer Mode 后，在 Gateway 配置中填写本站根地址、Static API key、Bearer 和模型 KEY，再添加真实 Model ID 与显示名。
若版本要求 Tier alias，应按实际模型手动选择，本页不强制映射 Sonnet/Opus/Haiku，也不承诺任意模型兼容全部客户端工具。
不要以 Claude Code 的环境变量或 settings.json 代替桌面 Gateway 设置。
保存后按提示重开、选模；连接测试可能计费。客户端仍可能要求自己的账号、许可或组织策略权限，网关 KEY 不能替代。

## 账户 MCP

本页另设独立的账户 MCP 区，不依赖模型或 KEY 选择。
显示当前站点 `/cockpit/mcp`、PAT 最小权限和 Codex/Cursor 占位符配置。
复制 MCP 示例从不调用 `/manual/key`，也不把模型 KEY 填入 MCP。
完整参数、认证边界、时间/金额口径与配置见[账户 MCP](mcp.md)。

## 截图

TRAE 教程的“打开自定义模型”和“管理模型”步骤附有两张官方截图，点击可看原图，并提供原文链接。
图片作为同源静态资源随应用发布，不从第三方服务器加载，也不带用户信息。保持原图比例，不裁剪。
图片出处与原始资源见项目 `NOTICE`；截图属于原权利人，不以本项目许可证重新授权。

## KEY 与查询

列表只返回本人令牌，管理员在本页也只能选择自己的令牌。KEY 始终脱敏展示。
查询模型时，后端使用所选 KEY 调用 `/v1/models`；令牌的分组、模型权限和 IP 限制保持不变。
不会创建 PAT 或令牌，不修改额度，不调用生成接口。客户端自己的验证、对话和自动请求正常计费。

每项配置和命令都放在使用它的步骤中，各有独立的复制按钮，不再到教程底部切换片段：

- 含 KEY 的配置显示“复制配置（含 KEY）”，点击时读取本人令牌 KEY，填入剪贴板。
- Provider 引用、CLI 启动命令等不含 KEY 的片段显示“复制配置”，直接复制，不读取 KEY。

真实 KEY 不进入页面预览、URL、日志或浏览器存储。复制失败不显示明文，也不自动重发。
切换令牌、模型、工具、CC Switch 目标或账号，会取消旧复制操作。
令牌列表是只读 GET，认证失效时使用共用登录桥接续期并重试一次；模型查询和 KEY 读取不自动重试。

## 接口

| 方法 | 地址 | 返回 |
| --- | --- | --- |
| GET | `/cockpit/api/manual/tokens` | 本人令牌选项和官方令牌管理路径，仅返回脱敏 KEY |
| POST | `/cockpit/api/manual/models` | 所选令牌的可用模型及上游声明的协议 |
| POST | `/cockpit/api/manual/key` | 用于明确复制的完整模型 KEY，禁止缓存 |

POST 必须为 JSON，带 `X-Cockpit-Manual: 1`，请求体仅接受 `{"token_id": 123}`。
网页调用使用同源会话及 `X-Cockpit-Session`；脚本可以用管理员 PAT，但仍只读取该管理员自己的令牌。
不接受另一用户 ID、任意上游 URL 或模型调用 KEY 作为 Cockpit 认证凭据。
令牌列表最多读取 2000 个，超出时报错，不静默遗漏。
这三个接口不允许在 API 文档调试器中执行，避免在那里回显完整 KEY。

无新增数据库表、迁移、后台任务、教程历史或凭据缓存。
