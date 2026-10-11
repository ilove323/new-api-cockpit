# 模型使用

入口：`/cockpit/manual`。普通用户登录后也能进入，用自己的令牌接入客户端；其他管理页面仍只对管理员开放。

## 使用步骤

1. 选择本人令牌，读取模型，再选择实际模型 ID。
2. 选择工具、系统和接口协议。CC Switch 还需要选择目标客户端。
3. 按教程填写连接信息，复制需要的配置片段，在客户端中选择模型。

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

## 工具设置

| 工具 | 协议和地址 | 操作入口 | 官方资料 |
| --- | --- | --- | --- |
| Claude Code | Messages，根地址 | CLI 环境变量；扩展使用 VS Code 用户设置中的 `claudeCode.environmentVariables` | [网关配置](https://code.claude.com/docs/en/llm-gateway-connect)、[模型参数](https://code.claude.com/docs/en/model-config) |
| Codex | Responses，根地址 + `/v1` | 用户 `config.toml` 的 Provider；进程环境中的 KEY；`--model` 选择本次模型 | [Provider](https://learn.chatgpt.com/docs/config-file/config-advanced#custom-model-providers)、[CLI 参数](https://learn.chatgpt.com/docs/cli/reference) |
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

## 截图

TRAE 教程的“打开自定义模型”和“管理模型”步骤附有两张官方截图，点击可看原图，并提供原文链接。
图片作为同源静态资源随应用发布，不从第三方服务器加载，也不带用户信息。保持原图比例，不裁剪。
图片出处与原始资源见项目 `NOTICE`；截图属于原权利人，不以本项目许可证重新授权。

## KEY 与查询

列表只返回本人令牌，管理员在本页也只能选择自己的令牌。KEY 始终脱敏展示。
查询模型时，后端使用所选 KEY 调用 `/v1/models`；令牌的分组、模型权限和 IP 限制保持不变。
不会创建 PAT 或令牌，不修改额度，不调用生成接口。客户端自己的验证、对话和自动请求正常计费。

页面只有一个复制按钮：

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
