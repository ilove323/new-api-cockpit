/*
 * /cockpit/manual: source-backed tutorials and a dependency-free text builder.
 * No requests, execution, DOM, storage, credential lookup, or clipboard access.
 * UI contract: preview with API_KEY (or omit key); pass an actual key ONLY in
 * the explicit copy handler. Render all returned strings as text, never HTML.
 * Protocols describe the documented client scope, NOT a live gateway test.
 */
(() => {
  'use strict';

  const guides = [
    {
      "id": "claude-code",
      "title": "Claude Code",
      "description": "选择 CLI 或 VS Code 扩展，按对应入口配置 Anthropic Messages。",
      "protocols": [
        "anthropic"
      ],
      "defaultProtocol": "anthropic",
      "steps": [
        {
          "title": "安装并打开终端",
          "body": "按官方安装页安装 Claude Code，运行 claude --version。{terminalHint}"
        },
        {
          "title": "设置本站地址和模型 KEY",
          "body": "在准备启动 Claude 的同一个终端执行下方配置；地址用站点根地址，不加 /v1。这里只设置当前终端，不会改系统环境变量。",
          "snippets": [
            0
          ]
        },
        {
          "title": "启动所选模型",
          "body": "进入自己的项目目录后执行下方命令。--model 只选择本次会话的模型，不改原默认模型。",
          "snippets": [
            1
          ]
        },
        {
          "title": "检查连接",
          "body": "在 Claude 中运行 /status，核对网关地址、认证来源和模型。第一次使用 API Key 时如出现授权提示，确认后再继续。"
        },
        {
          "title": "开始使用",
          "body": "手动发送一条简短消息，并在本站使用日志核对。后续 401 检查 KEY，404 检查根地址和模型 ID；超时先查日志，不连续重试。"
        }
      ],
      "notes": [
        "通过 --model 原样传入所选 ID，不按 Claude、GLM 等模型名称做白名单判断。",
        "ANTHROPIC_API_KEY 发送 x-api-key；ANTHROPIC_AUTH_TOKEN 发送 Bearer 凭据。本站示例使用前者。"
      ],
      "sources": [
        {
          "title": "Claude Code：连接 LLM 网关",
          "url": "https://code.claude.com/docs/en/llm-gateway-connect"
        },
        {
          "title": "Claude Code：启动参数与模型选择",
          "url": "https://code.claude.com/docs/en/model-config"
        },
        {
          "title": "Claude Code：VS Code 扩展",
          "url": "https://code.claude.com/docs/en/vs-code"
        },
        {
          "title": "安装与系统支持",
          "url": "https://code.claude.com/docs/en/setup"
        }
      ],
      "scope": "终端 / VS Code 扩展",
      "installURL": "https://code.claude.com/docs/en/setup",
      "platforms": [
        "macos",
        "windows",
        "linux"
      ],
      "checkedAt": "2026-10-30",
      "variants": [
        {
          "id": "cli",
          "title": "CLI 终端"
        },
        {
          "id": "vscode",
          "title": "VS Code 扩展"
        }
      ]
    },
    {
      "id": "codex",
      "title": "Codex",
      "description": "Codex CLI、桌面应用与 IDE 扩展：config.toml + auth.json 双文件接入 Responses。",
      "protocols": [
        "responses"
      ],
      "defaultProtocol": "responses",
      "steps": [
        {
          "title": "备份并找到配置目录",
          "body": "先退出正在运行的 Codex，备份 {codexDir} 中的 config.toml 和 auth.json。若设置了 CODEX_HOME，两个文件都放到该目录。WSL / SSH 中运行的 Codex 读取远端 Linux 的目录，不是 Windows 本机目录。"
        },
        {
          "title": "合并配置",
          "body": "将本步示例合并到 config.toml。model_provider 与 cli_auth_credentials_store 是顶层字段，放在所有 [表头] 前；同名 [model_providers.newapi] 只保留一份。base_url 带 /v1，wire_api 使用 responses。requires_openai_auth = true 表示从 Codex 认证文件读取凭据，请求仍发往本站。",
          "snippets": [
            0
          ]
        },
        {
          "title": "写入 auth.json",
          "body": "点击本步的“复制配置（含 KEY）”，保存为同目录的 auth.json。OPENAI_API_KEY 填的是上方所选的模型调用 KEY，不是用户 API PAT 或 ChatGPT 登录令牌。更换前备份旧认证文件，不要混入旧 OAuth 字段，也不要上传仓库、工单或聊天。",
          "snippets": [
            1
          ]
        },
        {
          "title": "设置文件权限并排除冲突",
          "body": "{permissionHint} 核对环境中的 OPENAI_API_KEY、CODEX_API_KEY 和 CODEX_HOME，避免其他凭据或目录覆盖。cli_auth_credentials_store = \"file\" 强制使用文件存储。"
        },
        {
          "title": "重新启动并选择模型",
          "body": "CLI 用本步命令启动所选模型；桌面应用和 IDE 扩展完全退出后重开，在会话菜单里选模型。示例不写顶层 model，不修改已有默认模型、推理强度或上下文窗口；自定义别名无法显示时可先用 CLI 指定模型 ID。",
          "snippets": [
            2
          ]
        },
        {
          "title": "检查配置是否生效",
          "body": "在终端运行 codex login status，确认已加载 API Key 认证；这只检查登录方式，不等于验证网关模型可用。核对最终提供商是 newapi、地址为本站 /v1；确认后发送一条简短消息，可能计费，再到本站使用日志核对模型与请求结果。若仍走旧站，检查项目级 .codex/config.toml、启动参数、CODEX_HOME 与环境覆盖项。"
        }
      ],
      "notes": [
        "config.toml 保存连接参数，auth.json 保存模型调用 KEY；两个文件各复制一次，不能把 JSON 粘进 TOML。",
        "这里的 requires_openai_auth 使用的是 Codex 的 API Key 认证文件格式，发送目标仍由自定义 base_url 决定。",
        "连接片段不再依赖 NEW_API_MODEL_KEY；MCP 使用另一个用户 API PAT，不能复用 auth.json 中的模型 KEY。",
        "Codex CLI 与 IDE 扩展共享认证缓存；修改或退出其中一个可能影响另一个，切换前请备份并确认所用目录。"
      ],
      "sources": [
        {
          "title": "OpenAI：Codex 配置与自定义提供商",
          "url": "https://learn.chatgpt.com/docs/config-file/config-advanced#custom-model-providers"
        },
        {
          "title": "OpenAI：认证与 auth.json 文件存储",
          "url": "https://learn.chatgpt.com/docs/auth#credential-storage"
        },
        {
          "title": "OpenAI 官方：auth.json 文件结构",
          "url": "https://github.com/openai/codex/blob/main/codex-rs/login/src/auth/storage.rs"
        },
        {
          "title": "OpenAI：API Key 与提供商认证选择",
          "url": "https://learn.chatgpt.com/docs/auth#alternative-model-providers"
        },
        {
          "title": "安装与系统支持",
          "url": "https://learn.chatgpt.com/docs/codex/cli"
        }
      ],
      "scope": "终端 / 桌面 / IDE 扩展",
      "installURL": "https://learn.chatgpt.com/docs/codex/cli",
      "platforms": [
        "macos",
        "windows",
        "linux"
      ],
      "checkedAt": "2026-10-30",
      "variants": [
        {
          "id": "cli",
          "title": "CLI 终端"
        },
        {
          "id": "desktop",
          "title": "桌面应用"
        },
        {
          "id": "vscode",
          "title": "IDE 扩展"
        }
      ]
    },
    {
      "id": "workbuddy",
      "title": "WorkBuddy",
      "description": "WorkBuddy 桌面端：用可视化自定义模型连接 OpenAI Chat。",
      "protocols": [
        "chat"
      ],
      "defaultProtocol": "chat",
      "steps": [
        {
          "title": "打开模型设置",
          "body": "从官方安装入口选择 Windows、macOS 或相应 Linux 发行版（官方提供统信 UOS、银河麒麟的安装说明）。打开 WorkBuddy 桌面端 → 设置 → 模型 → 自定义 / Custom，不是 CodeBuddy IDE 或移动端。"
        },
        {
          "title": "选择自定义供应商",
          "body": "选择“自定义 / Custom”，为连接起一个便于辨认的名字。使用本页 OpenAI Chat Completions 协议；工具调用、图片输入等能力必须由所选模型和渠道实际支持，不要仅凭模型名称勾选。",
          "snippets": [
            0
          ]
        },
        {
          "title": "填写 URL、KEY 和模型",
          "body": "按本步字段清单填写本站 /v1/chat/completions 完整接口 URL、所选模型 ID 和模型调用 KEY。在高级设置打开“自定义协议”，让客户端原样使用完整 URL，避免自动补全出重复 /v1 或 /chat/completions；不开启时应遵循客户端的标准地址补全规则。",
          "snippets": [
            1
          ]
        },
        {
          "title": "保存并在任务中选择",
          "body": "保存后回到任务的模型选择器，找到自定义模型分组，明确选择刚添加的连接。修改配置不会保证已有任务自动切换；必要时新建任务。配置通过客户端保存在本机，包含 KEY 的文件不能分享。"
        },
        {
          "title": "验证与排错",
          "body": "先核对当前任务显示的模型名，再手动发送一条简单消息，并在本站使用日志检查请求。出现 404 时优先核对“自定义协议”开关和完整路径；401 检查模型 KEY，而不是填写用户 API PAT。任务可能连续调用模型，留意余额和消耗。"
        }
      ],
      "notes": [
        "此教程使用自定义协议的完整请求地址，不是可导入的 models.json 文件。",
        "WorkBuddy 自定义模型产生的调用费用由本站余额承担；不要将产品积分与本站余额混为一谈。"
      ],
      "sources": [
        {
          "title": "WorkBuddy 官方：模型配置、自定义模型与协议开关",
          "url": "https://www.workbuddy.cn/docs/workbuddy/From-Beginner-to-Expert-Guide/Function-Description/Model"
        },
        {
          "title": "安装与系统支持",
          "url": "https://www.workbuddy.cn/docs/workbuddy/From-Beginner-to-Expert-Guide/Function-Description/Model"
        }
      ],
      "scope": "桌面办公工具",
      "installURL": "https://www.workbuddy.cn/docs/workbuddy/From-Beginner-to-Expert-Guide/Function-Description/Model",
      "platforms": [
        "macos",
        "windows",
        "linux"
      ],
      "checkedAt": "2026-10-30"
    },
    {
      "id": "hermes",
      "title": "Hermes",
      "description": "Hermes Agent：在 config.yaml 声明独立提供商，明确选择 Chat、Responses 或 Messages。",
      "protocols": [
        "chat",
        "responses",
        "anthropic"
      ],
      "defaultProtocol": "chat",
      "steps": [
        {
          "title": "安装并找到当前配置",
          "body": "安装 NousResearch Hermes Agent 后运行 hermes --version。Windows 支持原生 PowerShell；WSL 也可用。备份 {hermesFile}，自定义 HERMES_HOME 或 profile 时修改当前目录中的文件。"
        },
        {
          "title": "合并提供商配置",
          "body": "将下方 providers.newapi 合并到 {hermesFile}；已有 providers 时合并其下的 newapi，不新建第二个同名顶层字段。transport 明确指定所选协议，不靠网址或模型名称猜测。",
          "snippets": [
            0
          ]
        },
        {
          "title": "检查其他任务的提供商",
          "body": "只新增本连接，不覆盖原默认模型。标题、压缩和视觉任务可能有独立 auxiliary 配置；需要一并走本站时，在 Hermes 中明确选择对应提供商。"
        },
        {
          "title": "启动本次会话",
          "body": "在项目目录执行命令，用 --provider 和 --model 同时选择本次会话的连接和模型。",
          "snippets": [
            1
          ]
        },
        {
          "title": "核对并使用",
          "body": "检查启动显示的提供商、地址与模型后，再手动发送短消息。含 KEY 的 YAML 只保存在本人目录；{permissionHint}"
        }
      ],
      "notes": [
        "providers.newapi 是独立连接；通过 --provider=newapi 选择，不修改 model.default。",
        "transport 分别为 chat_completions、codex_responses、anthropic_messages；辅助任务的提供商需另行检查。"
      ],
      "sources": [
        {
          "title": "Hermes 官方：自定义提供商和三种 transport",
          "url": "https://github.com/NousResearch/hermes-agent/blob/main/website/docs/integrations/providers.md"
        },
        {
          "title": "Hermes 官方：模型配置示例",
          "url": "https://github.com/NousResearch/hermes-agent/blob/main/cli-config.yaml.example"
        },
        {
          "title": "Hermes 官方：配置目录、主模型与辅助模型",
          "url": "https://github.com/NousResearch/hermes-agent/blob/main/website/docs/user-guide/configuration.md"
        },
        {
          "title": "安装与系统支持",
          "url": "https://github.com/NousResearch/hermes-agent#quick-install"
        }
      ],
      "scope": "终端 Agent",
      "installURL": "https://github.com/NousResearch/hermes-agent#quick-install",
      "platforms": [
        "macos",
        "windows",
        "linux"
      ],
      "checkedAt": "2026-10-30"
    },
    {
      "id": "claude-app",
      "title": "Claude App",
      "description": "Claude 桌面应用：通过 Third-Party Inference 的 Gateway 接入 Messages。",
      "protocols": [
        "anthropic"
      ],
      "defaultProtocol": "anthropic",
      "steps": [
        {
          "title": "确认桌面版本与功能入口",
          "body": "安装 Claude Desktop，进入 Help → Troubleshooting → Enable Developer Mode，重启后进入 Developer → Configure Third-Party Inference。Linux 版处于 beta；若安装版没有此入口，使用 Claude Code 教程，不要修改不明 JSON 文件。组织下发的配置可能将此表单设为只读。"
        },
        {
          "title": "填写 Gateway 连接",
          "body": "在 Connection 选择 Gateway。Gateway base URL 填本站根地址，不加 /v1/messages；客户端会使用 Anthropic Messages 路径。Gateway credentials 选择 Static API key，认证方式选 Bearer，Gateway API key 填本页所选的模型调用 KEY。",
          "snippets": [
            0
          ]
        },
        {
          "title": "添加所选模型",
          "body": "进入 Models，填写本页模型 ID 和便于识别的 Display name。若当前版本要求 Tier alias，请按模型实际类型手动选择，不要将任意模型强制伪装成 Sonnet、Opus 或 Haiku。本教程不会替你生成默认模型映射；所选模型需支持 Messages 及客户端使用的工具能力。",
          "snippets": [
            1
          ]
        },
        {
          "title": "保存并确认当前连接",
          "body": "保存配置后按客户端提示重新启动。在模型菜单里选择刚添加的模型，再运行连接测试。测试可能发起模型请求并产生费用；界面要求登录 Claude 账号、组织许可或其他客户端权限时，本服务的 KEY 不能替代这些要求。"
        },
        {
          "title": "区分模型接入与 MCP",
          "body": "第三方推理决定“用哪个模型回答”；MCP 决定“能查哪些工具”，是两个独立设置。下方账户 MCP 使用用户 API PAT 查询本人余额，不能把模型调用 KEY 当作 MCP 凭据。若模型连接失败，先核对 Bearer、根地址和 Messages 协议，再检查模型权限与日志。"
        }
      ],
      "notes": [
        "Claude App 不读取 Claude Code 的 ANTHROPIC_BASE_URL 或 ~/.claude/settings.json；必须在桌面应用的 Gateway 设置中配置。",
        "第三方推理的功能范围取决于桌面版本和组织策略；不承诺所有 Chat、Cowork、Code 或移动端入口都使用这套连接。"
      ],
      "sources": [
        {
          "title": "Anthropic 官方：Claude Desktop Gateway",
          "url": "https://claude.com/docs/third-party/claude-desktop/gateway"
        },
        {
          "title": "Anthropic 官方：Linux 桌面版 beta",
          "url": "https://code.claude.com/docs/en/desktop-linux"
        },
        {
          "title": "Cloudflare 官方集成指南：Claude Desktop Gateway 字段与菜单",
          "url": "https://developers.cloudflare.com/ai-gateway/integrations/coding-agents/claude-desktop/"
        },
        {
          "title": "安装与系统支持",
          "url": "https://claude.com/docs/third-party/claude-desktop/gateway"
        }
      ],
      "scope": "桌面应用",
      "installURL": "https://claude.com/docs/third-party/claude-desktop/gateway",
      "platforms": [
        "macos",
        "windows",
        "linux"
      ],
      "checkedAt": "2026-10-30"
    },
    {
      "id": "cursor",
      "title": "Cursor",
      "description": "Cursor：覆盖 OpenAI 地址，手动添加会话模型。",
      "protocols": [
        "chat"
      ],
      "defaultProtocol": "chat",
      "steps": [
        {
          "title": "打开模型设置",
          "body": "安装 Cursor 桌面编辑器，进入 Cursor Settings → Models。在 OpenAI API Key 中填写下方 KEY 并启用开关；此处不是 Cursor CLI 的设置。",
          "snippets": [
            0
          ]
        },
        {
          "title": "填写网关地址",
          "body": "在 Override OpenAI Base URL 中填写本步的地址，格式为站点根地址加 /v1，不填完整 /chat/completions 路径。",
          "snippets": [
            1
          ]
        },
        {
          "title": "添加并选择模型",
          "body": "用 Add Custom Model 添加本步显示的模型 ID，然后在对话中选择它。Refresh Model List 更新的是 Cursor 内置目录，不会从本站 /v1/models 发现模型。",
          "snippets": [
            2
          ]
        },
        {
          "title": "保存并验证当前会话",
          "body": "确认 API Key 开关启用、自定义模型已添加且当前聊天实际选中了它。关闭旧聊天并新建会话后手动发一条简单消息，再检查本站使用日志；只看到模型出现在菜单，不等于请求已通过本站。"
        }
      ],
      "notes": [
        "Tab 补全使用 Cursor 内置模型，不走 BYOK。",
        "Cursor 请求会经其服务器构建提示，再发送到网关。"
      ],
      "sources": [
        {
          "title": "Cursor 官方：自带 API Key",
          "url": "https://cursor.com/help/models-and-usage/api-keys"
        },
        {
          "title": "Cursor 官方支持回复：第三方网关地址与手动添加模型",
          "url": "https://forum.cursor.com/t/cursor-cannot-connect-to-the-third-party-relay-station/164487/5"
        },
        {
          "title": "安装与系统支持",
          "url": "https://cursor.com/download"
        }
      ],
      "scope": "代码编辑器",
      "installURL": "https://cursor.com/download",
      "platforms": [
        "macos",
        "windows",
        "linux"
      ],
      "checkedAt": "2026-10-30"
    },
    {
      "id": "cc-switch",
      "title": "CC Switch",
      "description": "CC Switch：选择目标客户端，直连或通过本地路由转换协议。",
      "protocols": [
        "chat",
        "responses",
        "anthropic"
      ],
      "defaultProtocol": "anthropic",
      "steps": [
        {
          "title": "选择目标客户端",
          "body": "在本页选择 Claude Code 或 Codex，再到 CC Switch 对应客户端的 Direct / Routing 页添加“自定义配置”。"
        },
        {
          "title": "填写连接",
          "body": "按本步字段填写 Endpoint URL、API Key 和上游格式。Claude Code 直连 Messages，Codex 直连 Responses；跨协议时选择 Routing，并为目标客户端启用本地路由。",
          "snippets": [
            0
          ]
        },
        {
          "title": "不设置默认模型",
          "body": "不填写默认模型或 Claude 模型映射。Codex 的模型映射表首行会自动成为默认模型，本教程不生成该表；第 4 步通过 --model 命令选择本次模型。"
        },
        {
          "title": "启用并启动",
          "body": "启用该提供商，确认地址和凭据已写入目标工具。跨协议转换时本地路由必须保持运行，再用本步命令启动目标客户端。",
          "snippets": [
            1
          ]
        },
        {
          "title": "区分设置文件与连接验证",
          "body": "字段清单是逐项填写说明，不是能直接导入的 JSON。切换提供商前备份 CC Switch 当前配置；启用 Routing 后必须保持本地路由运行。打开目标客户端核对实际提供商和模型，不要同时使用其他工具反复覆盖相同配置。"
        }
      ],
      "notes": [
        "字段清单不是 CC Switch 导入文件。",
        "Codex 的 wire_api 始终为 responses；Chat / Messages 上游由 CC Switch Routing 转换，不改成 wire_api = \"chat\"。"
      ],
      "sources": [
        {
          "title": "CC Switch 项目说明",
          "url": "https://github.com/farion1231/cc-switch#readme"
        },
        {
          "title": "CC Switch 官方手册：添加提供商与原生/Routing 格式",
          "url": "https://github.com/farion1231/cc-switch/blob/main/docs/user-manual/en/2-providers/2.1-add.md"
        },
        {
          "title": "安装与系统支持",
          "url": "https://github.com/farion1231/cc-switch/releases"
        }
      ],
      "scope": "连接配置管理器",
      "installURL": "https://github.com/farion1231/cc-switch/releases",
      "platforms": [
        "macos",
        "windows",
        "linux"
      ],
      "checkedAt": "2026-10-30"
    },
    {
      "id": "trae",
      "title": "TRAE",
      "description": "TRAE 中文版 IDE：按完整请求地址添加自定义模型。",
      "protocols": [
        "chat",
        "anthropic"
      ],
      "defaultProtocol": "chat",
      "steps": [
        {
          "title": "打开自定义模型",
          "body": "进入 设置 → 模型 → 添加模型，选择“自定义模型”，而不是预置服务商。",
          "image": {
            "src": "/cockpit/static/manual-images/trae-add.webp",
            "alt": "TRAE 官方添加模型窗口，顶部是自定义模型入口",
            "caption": "TRAE：选择自定义模型",
            "source": "https://docs.trae.cn/ide_models",
            "width": 1114,
            "height": 1078
          }
        },
        {
          "title": "填写连接",
          "body": "选择 OpenAI Chat Completions 或 Anthropic Messages 格式，打开“完整 URL”开关，填写本步的请求地址、模型 ID 和 API 密钥。",
          "snippets": [
            0
          ]
        },
        {
          "title": "添加并选择",
          "body": "点击“添加模型”，通过连接验证后，在对话的模型菜单中手动选择。Auto 不会使用自定义模型。"
        },
        {
          "title": "管理模型",
          "body": "在自定义模型列表中，可以编辑、删除或启用该模型；启用后才会出现在对话模型列表。",
          "image": {
            "src": "/cockpit/static/manual-images/trae-manage.webp",
            "alt": "TRAE 官方自定义模型列表中的编辑、删除和启用开关",
            "caption": "TRAE：编辑、删除与启用开关",
            "source": "https://docs.trae.cn/ide_models",
            "width": 1276,
            "height": 146
          }
        },
        {
          "title": "保存后核对协议与地址",
          "body": "完整 URL 模式下，Chat 填 /v1/chat/completions，Messages 填 /v1/messages，不能只填根地址。成功添加后在对话里手动选择该模型，不要停留在 Auto。网络验证或测试消息可能计费，失败时先看错误与本站日志再重试。"
        }
      ],
      "notes": [
        "这是 TRAE 中文版的设置路径，国际版界面另行使用其模型设置。",
        "不预填 Temperature、上下文窗口、视觉或思考参数。"
      ],
      "sources": [
        {
          "title": "TRAE 中文版官方：自定义模型",
          "url": "https://docs.trae.cn/ide_models"
        },
        {
          "title": "TRAE 中文版官方：Auto 模式范围",
          "url": "https://docs.trae.cn/ide_auto-mode"
        },
        {
          "title": "安装与系统支持",
          "url": "https://docs.trae.cn/ide_get-started-with-trae"
        }
      ],
      "scope": "中文版 IDE / Agent 窗口",
      "installURL": "https://docs.trae.cn/ide_get-started-with-trae",
      "platforms": [
        "macos",
        "windows",
        "linux"
      ],
      "checkedAt": "2026-10-30"
    },
    {
      "id": "qoder",
      "title": "Qoder",
      "description": "Qoder 独立应用：添加自定义 Base URL 和会话模型。",
      "protocols": [
        "chat",
        "responses",
        "anthropic"
      ],
      "defaultProtocol": "chat",
      "steps": [
        {
          "title": "打开模型设置",
          "body": "从官方下载页安装独立 Qoder 应用（Windows、macOS、Linux），进入 Settings → Models → Add model。不要使用只有预置供应商的 Qoder IDE 入口。"
        },
        {
          "title": "选择自定义供应商",
          "body": "Provider → Custom：Chat / Responses 选择 OpenAI Compatible，再选对应 API 类型；Messages 选择 Anthropic Compatible。Base URL、API Key 和 Model ID 按本步字段填写。",
          "snippets": [
            0
          ]
        },
        {
          "title": "添加模型",
          "body": "点击 Next，填写本步的模型 ID，再按模型实际能力设置上下文、Vision、Thinking。Validate and Add Model 验证连接后保存；随后在对话中选择模型。",
          "snippets": [
            1
          ]
        },
        {
          "title": "重载与核对客户端版本",
          "body": "保存 Custom 提供商并为会话选中模型，必要时重启独立 Qoder 应用。若设置里只有固定服务商、没有自定义 URL，先确认打开的是独立 Qoder 而不是 Qoder IDE，再查官方文档确认版本支持。"
        }
      ],
      "notes": [
        "Base URL 使用站点根地址加 /v1，不填完整请求路径。",
        "不要把本站 KEY 填到 DeepSeek、智谱等预置供应商的官方地址。"
      ],
      "sources": [
        {
          "title": "Qoder IDE 官方：自定义模型（指定供应商）",
          "url": "https://docs.qoder.com/zh/user-guide/chat/custom-models"
        },
        {
          "title": "独立 Qoder 应用官方：自定义 Base URL",
          "url": "https://docs.qoder.com/qoder/custom-models"
        },
        {
          "title": "安装与系统支持",
          "url": "https://qoder.com/download"
        }
      ],
      "scope": "独立 Qoder 应用",
      "installURL": "https://qoder.com/download",
      "platforms": [
        "macos",
        "windows",
        "linux"
      ],
      "checkedAt": "2026-10-30"
    },
    {
      "id": "chatbox",
      "title": "Chatbox",
      "description": "Chatbox：选择 Chat、Responses 或 Claude 兼容供应商。",
      "protocols": [
        "chat",
        "responses",
        "anthropic"
      ],
      "defaultProtocol": "chat",
      "steps": [
        {
          "title": "添加供应商",
          "body": "在设置中添加自建供应商。按本页协议选择 OpenAI API Compatible、OpenAI Responses API Compatible 或 Claude API Compatible。",
          "snippets": [
            0
          ]
        },
        {
          "title": "填写地址",
          "body": "Chat / Responses 的 API Host 与 API Path 分开填写；Claude 兼容的 API Host 填根地址加 /v1，由客户端追加 /messages。按本步字段填写，不重复路径。",
          "snippets": [
            1
          ]
        },
        {
          "title": "选择会话模型",
          "body": "按本步示例填写 API Key 和模型 ID，然后在会话中选择供应商与模型。这里是逐项填写的字段，不是 Chatbox 导入文件。",
          "snippets": [
            2
          ]
        },
        {
          "title": "保存与选择会话供应商",
          "body": "先保存自建供应商，再在具体会话中选择这个供应商及模型。模型 ID 要原样使用本站列表，不能用显示名称代替。API Host 和 API Path 按不同协议拆开填写，出现重复 /v1 时先检查这两个字段。"
        }
      ],
      "notes": [
        "Chat、Responses、Messages 是三种不同的自建供应商类型，不能只替换请求路径。"
      ],
      "sources": [
        {
          "title": "Chatbox：自建供应商的协议选项",
          "url": "https://github.com/chatboxai/chatbox/blob/main/src/renderer/components/settings/provider/AddProviderModal.tsx"
        },
        {
          "title": "Chatbox：Chat 请求",
          "url": "https://github.com/chatboxai/chatbox/blob/main/src/shared/providers/definitions/models/custom-openai.ts"
        },
        {
          "title": "Chatbox：Responses 请求",
          "url": "https://github.com/chatboxai/chatbox/blob/main/src/shared/providers/definitions/models/custom-openai-responses.ts"
        },
        {
          "title": "Chatbox：Claude 兼容请求",
          "url": "https://github.com/chatboxai/chatbox/blob/main/src/shared/providers/definitions/models/custom-claude.ts"
        },
        {
          "title": "Chatbox：地址规范化",
          "url": "https://github.com/chatboxai/chatbox/blob/main/src/shared/utils/llm_utils.ts"
        },
        {
          "title": "安装与系统支持",
          "url": "https://chatboxai.app/"
        }
      ],
      "scope": "桌面聊天工具",
      "installURL": "https://chatboxai.app/",
      "platforms": [
        "macos",
        "windows",
        "linux"
      ],
      "checkedAt": "2026-10-30"
    },
    {
      "id": "openclaw",
      "title": "OpenClaw",
      "description": "OpenClaw：添加自定义提供商，在会话中选择模型。",
      "protocols": [
        "chat",
        "responses",
        "anthropic"
      ],
      "defaultProtocol": "chat",
      "steps": [
        {
          "title": "选择接口协议",
          "body": "Chat 对应 openai-completions；Responses 对应 openai-responses；Messages 对应 anthropic-messages。"
        },
        {
          "title": "合并提供商配置",
          "body": "先备份 {openclawFile}，把下方 models.providers.newapi 合并到已有 providers。不要覆盖 agents.defaults、模型允许列表或其他提供商。Windows 本教程使用原生 CLI；在 WSL 运行时改选 Linux，并使用 WSL 用户目录。",
          "snippets": [
            0
          ]
        },
        {
          "title": "选择会话模型",
          "body": "在 Control UI 新建聊天时，从模型菜单选择 newapi/模型ID。已有会话切换时，可用 /model 加模型引用及 -s，仅切换当前会话。",
          "snippets": [
            1
          ]
        },
        {
          "title": "备份、合并与重载",
          "body": "先备份自己的 openclaw.json，只合并 models.providers.newapi，不覆盖 agents、默认模型或原有提供商。保存后按当前运行方式重新加载配置，在会话菜单选择 newapi/模型 ID；配置中的 apiKey 为明文，仅允许本人读取。"
        }
      ],
      "notes": [
        "OpenAI 两种协议的 baseUrl 加 /v1；Anthropic 用根地址。",
        "模型目录只声明本页选择的模型；不写价格、上下文窗口或输出上限。"
      ],
      "sources": [
        {
          "title": "OpenClaw：自定义提供商",
          "url": "https://docs.openclaw.ai/gateway/config-tools/custom-providers"
        },
        {
          "title": "OpenClaw：按会话选择模型",
          "url": "https://docs.openclaw.ai/concepts/models#choose-a-model-for-a-session"
        },
        {
          "title": "OpenClaw：环境变量与字面量转义",
          "url": "https://docs.openclaw.ai/gateway/configuration/environment-variables"
        },
        {
          "title": "安装与系统支持",
          "url": "https://docs.openclaw.ai/install"
        },
        {
          "title": "OpenClaw 官方：Windows 原生与 WSL",
          "url": "https://docs.openclaw.ai/platforms/windows"
        }
      ],
      "scope": "本地 Gateway / Control UI",
      "installURL": "https://docs.openclaw.ai/install",
      "platforms": [
        "macos",
        "windows",
        "linux"
      ],
      "checkedAt": "2026-10-30"
    },
    {
      "id": "pi-agent",
      "title": "Pi Agent",
      "scope": "终端编程 Agent",
      "description": "Pi 官方 CLI：models.json 可选择 OpenAI Chat、Responses 或 Anthropic Messages。",
      "protocols": [
        "chat",
        "responses",
        "anthropic"
      ],
      "defaultProtocol": "chat",
      "platforms": [
        "macos",
        "windows",
        "linux"
      ],
      "checkedAt": "2026-10-30",
      "installURL": "https://github.com/earendil-works/pi/tree/main/packages/coding-agent#readme",
      "steps": [
        {
          "title": "安装 Pi",
          "body": "官方仓库已迁移至 earendil-works/pi，npm 包为 @earendil-works/pi-coding-agent。先准备 Node.js 22.19+，再手动执行安装命令。Windows 原生还需 Git for Windows 提供 Bash；WSL 使用 Linux 教程。",
          "snippets": [
            0
          ]
        },
        {
          "title": "找到配置文件",
          "body": "备份 {piFile}，文件不存在时创建父目录和文件。设置了 PI_CODING_AGENT_DIR 时，以该 Agent 目录为准。此文件不是 auth.json。"
        },
        {
          "title": "合并模型连接",
          "body": "把下方 providers.newapi 合并到 {piFile}。api 明确指定所选协议；只声明这次选择的模型，不填写价格、上下文或推理能力。",
          "snippets": [
            1
          ]
        },
        {
          "title": "启动并选择模型",
          "body": "在项目目录执行启动命令。也可在运行中的 Pi 输入 /model 重新加载配置并选择 newapi 下的模型；不要按保存默认值快捷键，除非你确实想改默认。",
          "snippets": [
            2
          ]
        },
        {
          "title": "检查调用",
          "body": "核对提供商和模型后手动发一条短消息，在本站使用日志核对。Pi 本地费用估算不等于本站账单；{permissionHint}"
        }
      ],
      "notes": [
        "CLI、models.json 和 /model 属于 Pi Agent，不是同名聊天应用。",
        "旧教程中的 @mariozechner/pi-coding-agent 属于历史包名，安装以当前官方 README 为准。"
      ],
      "sources": [
        {
          "title": "Pi 官方：安装与系统要求",
          "url": "https://github.com/earendil-works/pi/tree/main/packages/coding-agent#readme"
        },
        {
          "title": "Pi 官方：自定义模型与凭据",
          "url": "https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/models.md"
        },
        {
          "title": "Pi 官方：配置字段和协议",
          "url": "https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/models-configuration.md"
        },
        {
          "title": "Pi 官方：Windows 原生与 WSL",
          "url": "https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/windows.md"
        }
      ]
    },
    {
      "id": "deepseek-harness",
      "title": "DeepSeek Harness",
      "scope": "本地 Web UI / Agent",
      "description": "通过自定义模型 API 接入本站，不使用内置 DeepSeek 提供商的官方地址。",
      "protocols": [
        "chat",
        "responses",
        "anthropic"
      ],
      "defaultProtocol": "chat",
      "platforms": [
        "macos",
        "windows",
        "linux"
      ],
      "checkedAt": "2026-10-30",
      "installURL": "https://github.com/deepseek-ai/deepseek-harness#run",
      "steps": [
        {
          "title": "在本机启动 Web UI",
          "body": "安装当前版本要求的 Node.js 后，在本机终端执行命令，打开输出的本地地址（默认 http://127.0.0.1:3080）。这是本机界面地址，不是本站 API 地址。",
          "snippets": [
            0
          ]
        },
        {
          "title": "打开自定义模型 API",
          "body": "进入设置 → 模型 → 添加模型提供商，切换为自定义模型 API。不要直接在内置 DeepSeek 卡片填写本站 KEY，该卡片默认连接 DeepSeek 官方。"
        },
        {
          "title": "填写提供商连接",
          "body": "逐项复制下方字段到自定义表单。Provider ID 使用小写且创建后不可改名；一个提供商只选一种协议。",
          "snippets": [
            1
          ]
        },
        {
          "title": "添加所选模型",
          "body": "在模型目录中手动添加下方模型 ID。获取可用模型失败时仍可手动填写；保存后才会写入当前 profile。",
          "snippets": [
            2
          ]
        },
        {
          "title": "保存并选择",
          "body": "模型变更下一次请求即生效，不必重启服务。回到模型选择器选中刚建的提供商和模型；这也会成为新会话默认值，已有会话仍保留原模型。确认后再发送短消息。"
        }
      ],
      "notes": [
        "DeepSeek Harness 处于 developer preview，界面和配置可能变更。教程使用官方 Web UI，不手工拼装凭据 YAML。",
        "已保存密钥为只写；设置位于 DSH_HOME 的 profile，凭据另存 .credentials.yaml。不要将这些文件加入仓库。"
      ],
      "sources": [
        {
          "title": "DeepSeek 官方：运行方式",
          "url": "https://github.com/deepseek-ai/deepseek-harness#run"
        },
        {
          "title": "DeepSeek 官方：自定义提供商、三种协议和模型选择",
          "url": "https://github.com/deepseek-ai/deepseek-harness/blob/master/docs/user/guide/providers.zh.md"
        }
      ]
    },
    {
      "id": "codebuddy",
      "title": "CodeBuddy",
      "scope": "IDE / VS Code 插件 / CLI",
      "description": "IDE 与插件用 Chat 配置；CLI 另支持 Responses 和 Anthropic 兼容连接。",
      "protocols": [
        "chat"
      ],
      "defaultProtocol": "chat",
      "platforms": [
        "macos",
        "windows",
        "linux"
      ],
      "checkedAt": "2026-10-30",
      "installURL": "https://www.codebuddy.ai/docs/cli/installation",
      "variants": [
        {
          "id": "ide",
          "title": "IDE / VS Code 插件"
        },
        {
          "id": "cli",
          "title": "CodeBuddy Code CLI",
          "protocols": [
            "chat",
            "responses",
            "anthropic"
          ]
        }
      ],
      "steps": [
        {
          "title": "安装正确的客户端",
          "body": "从官网安装 CodeBuddy IDE，或在 VS Code 商店安装 Tencent Cloud CodeBuddy。Windows、macOS、Linux 使用相同 models.json 结构；不是 WorkBuddy 桌面版。"
        },
        {
          "title": "找到本人配置",
          "body": "备份 {buddyFile}，缺少目录时先创建。本教程使用用户级文件；项目中的 .codebuddy/models.json 优先级更高，可能覆盖同名模型。"
        },
        {
          "title": "合并所选模型",
          "body": "将下方 models 数组中的这一项合并到 {buddyFile}；同 id 的旧项替换，不重复追加，不覆盖原有其他模型。IDE / 插件教程只使用官方已确认的 Chat 格式，url 是完整请求地址。",
          "snippets": [
            0
          ]
        },
        {
          "title": "重载并选择",
          "body": "保存 UTF-8 JSON 后重启客户端，关闭 Auto，在模型菜单中选择下方模型 ID。若已有 availableModels 白名单，把所选 ID 加进去；本教程不生成或覆盖白名单。",
          "snippets": [
            1
          ]
        },
        {
          "title": "检查连接",
          "body": "发送一条短消息后核对本站日志。不要把用户 API PAT 填到 apiKey；也不要把模型 KEY 写进项目共享配置。{permissionHint}"
        }
      ],
      "notes": [
        "CodeBuddy IDE / 插件与 CLI 的可配置协议不同，请先选择接入方式。",
        "CLI 的 Anthropic 环境变量连接使用根地址；Chat 的 models.json 使用完整 /v1/chat/completions，Responses 使用 /v1 基础地址。"
      ],
      "sources": [
        {
          "title": "CodeBuddy 官方：models.json 与 Responses",
          "url": "https://www.codebuddy.ai/docs/cli/models"
        },
        {
          "title": "CodeBuddy 官方：Anthropic 兼容环境变量",
          "url": "https://www.codebuddy.ai/docs/cli/env-vars"
        },
        {
          "title": "CodeBuddy 官方：安装",
          "url": "https://www.codebuddy.ai/docs/cli/installation"
        },
        {
          "title": "腾讯 CloudBase 官方：CodeBuddy IDE 配置",
          "url": "https://docs.cloudbase.net/ai/ai-tools/codebuddy"
        }
      ]
    },
    {
      "id": "curl",
      "title": "curl",
      "description": "curl：按所选协议生成一次请求。",
      "protocols": [
        "chat",
        "responses",
        "anthropic"
      ],
      "defaultProtocol": "chat",
      "steps": [
        {
          "title": "选择接口",
          "body": "选择 Chat、Responses 或 Anthropic Messages，第 3 步会显示对应的请求命令。"
        },
        {
          "title": "选择终端",
          "body": "{terminalHint} Windows 示例只适用于 PowerShell 7.3+ 与 curl.exe，不适用于 CMD、Windows PowerShell 5.1。"
        },
        {
          "title": "发送请求",
          "body": "复制到终端后手动执行，请求内容是“你好”。请求体的 model 使用本页所选 ID，不设置客户端默认模型。",
          "snippets": [
            0
          ]
        },
        {
          "title": "在自己的终端手动验证",
          "body": "先选系统和协议，再复制相应片段；Windows 使用 PowerShell 7.3+ 与 curl.exe，不要把 CMD、Bash、PowerShell 的续行符混用。此示例会真实生成内容并消耗额度，仅在确认后手动执行，不要把完整命令发到公共群聊。"
        }
      ],
      "notes": [
        "Messages 示例带 max_tokens 和 anthropic-version；OpenAI 两种接口使用 Bearer 凭据。"
      ],
      "sources": [
        {
          "title": "OpenAI 官方：Chat 到 Responses 的请求示例",
          "url": "https://developers.openai.com/api/docs/guides/migrate-to-responses"
        },
        {
          "title": "OpenAI 官方：文本生成与 Responses",
          "url": "https://developers.openai.com/api/docs/guides/text"
        },
        {
          "title": "Anthropic 官方：Messages 请求",
          "url": "https://platform.claude.com/docs/en/api/messages/create"
        },
        {
          "title": "安装与系统支持",
          "url": "https://curl.se/download.html"
        },
        {
          "title": "curl 官方：命令选项",
          "url": "https://curl.se/docs/manpage.html"
        }
      ],
      "scope": "终端 HTTP 请求",
      "installURL": "https://curl.se/download.html",
      "platforms": [
        "macos",
        "windows",
        "linux"
      ],
      "checkedAt": "2026-10-30"
    }
  ];

  function freeze(value) {
    for (const child of Object.values(value)) if (child && typeof child === 'object') freeze(child);
    return Object.freeze(value);
  }
  freeze(guides);

  function input(value, label) {
    if (typeof value !== 'string' || !value.trim()) throw new Error(label + '不能为空，且必须是字符串。');
    // C0/C1, line separators, bidi controls and unpaired UTF-16 surrogates.
    if (/[\u0000-\u001f\u007f-\u009f\u2028\u2029\u200e\u200f\u202a-\u202e\u2066-\u2069]/u.test(value)
        || /[\ud800-\udfff]/u.test(value)) throw new Error(label + '不能包含控制字符或无效 Unicode。');
    return value;
  }

  function rootOrigin(value) {
    input(value, '站点地址');
    // Check raw input BEFORE URL normalisation (which removes dot paths,
    // default ports and empty query/hash delimiters).
    const parts = /^(https?):\/\/(\[[0-9a-f:.]+\]|[^\s/:?#@\\]+)(?::([0-9]+))?\/?$/i.exec(value);
    if (!parts) throw new Error('站点地址必须是 http(s) 根地址，可带端口，但不能带用户信息、路径、查询或片段。');
    let url;
    try { url = new URL(value); } catch { throw new Error('站点地址不是合法的 http(s) 地址。'); }
    if (!url.hostname || url.username || url.password || url.pathname !== '/' || url.search || url.hash) {
      throw new Error('站点地址只能包含 http(s)、主机和可选端口。');
    }
    // Keep explicit ports, including :443/:80; strip only the root slash.
    return url.protocol + '//' + url.hostname + (parts[3] === undefined ? '' : ':' + parts[3]);
  }

  const json = value => JSON.stringify(value, null, 2);
  const posixQuote = value => "'" + value.replace(/'/g, "'\\''") + "'";
  // PowerShell treats U+2018..U+201B as single-quote delimiters too.
  const powershellQuote = value => "'" + value.replace(/['\u2018-\u201b]/g, char => char + char) + "'";
  const quote = (value, platform) => platform === 'powershell' ? powershellQuote(value) : posixQuote(value);
  const shellLanguage = platform => platform === 'powershell' ? 'powershell' : 'bash';
  const block = (title, language, content, containsKey = true, destination = '') => ({title, language, content, containsKey, destination});
  const fields = (title, entries) => [{...block(title + '（逐项填写，不是导入文件）', 'text',
    entries.map(([name, value]) => name + '：' + value).join('\n'), entries.some(entry => entry[2] === true)),
    fields: entries.map(([label, value, containsKey = false]) => ({label, value, containsKey}))}];
  const env = (name, value, platform) => platform === 'powershell'
    ? '$env:' + name + ' = ' + quote(value, platform)
    : 'export ' + name + '=' + quote(value, platform);
  const launch = (client, model, platform) => block(client + ' CLI · 本次启动选择模型',
    shellLanguage(platform), client + ' --model=' + quote(model, platform), false);
  // OpenClaw interpolates configuration string VALUES, not JSON object keys.
  const clawLiteral = value => value.replace(/\$\{([A-Z_][A-Z0-9_]*)\}/g, (_, name) => '$${' + name + '}');
  const piLiteral = value => value.replace(/\$/g, () => '$$').replace(/^!/, '$!');
  const apiType = protocol => ({chat: 'openai-completions', responses: 'openai-responses', anthropic: 'anthropic-messages'})[protocol];
  function operatingSystem(value = 'macos') {
    // Shell aliases remain accepted by the pure builder; the page exposes three OS choices.
    if (value === 'posix') return 'macos';
    if (value === 'powershell') return 'windows';
    if (!['macos', 'windows', 'linux'].includes(value)) throw new Error('系统只能是 macos、windows 或 linux。');
    return value;
  }
  function paths(os) {
    const home = os === 'windows' ? '%USERPROFILE%\\' : '~/';
    return {
      codexDir: home + '.codex', hermesFile: home + '.hermes' + (os === 'windows' ? '\\' : '/') + 'config.yaml',
      piFile: home + '.pi' + (os === 'windows' ? '\\agent\\' : '/agent/') + 'models.json',
      buddyFile: home + '.codebuddy' + (os === 'windows' ? '\\' : '/') + 'models.json',
      openclawFile: home + '.openclaw' + (os === 'windows' ? '\\' : '/') + 'openclaw.json',
      vscodeFile: os === 'windows' ? '%APPDATA%\\Code\\User\\settings.json'
        : os === 'macos' ? '~/Library/Application Support/Code/User/settings.json' : '~/.config/Code/User/settings.json',
      terminalHint: os === 'windows' ? '本页 Windows 命令使用 PowerShell；在 WSL 内运行时请改选 Linux。' : '在本机 Bash 或 Zsh 终端执行；SSH 远端运行时，配置写到远端用户目录。',
      permissionHint: os === 'windows' ? '含 KEY 的文件只允许本人读取，不放入项目仓库。' : '含 KEY 的配置文件保存后设为仅本人可读写（chmod 600），不要加入仓库。',
    };
  }
  function resolve(id, options = {}) {
    const item = guides.find(guide => guide.id === id);
    if (!item) throw new Error('未知工具，不能生成教程。');
    const os = operatingSystem(options.platform), values = paths(os);
    const variant = options.variant ?? item.variants?.[0]?.id ?? '';
    const surface = item.variants?.find(entry => entry.id === variant);
    if (item.variants && !surface) throw new Error('请选择此工具支持的接入方式。');
    const result = {...item, ...surface, id: item.id, title: item.title, variantTitle: surface?.title || '', variant, os};
    if (id === 'claude-code' && variant === 'vscode') result.installURL = 'https://code.claude.com/docs/en/vs-code';
    if (id === 'codex' && variant !== 'cli') result.installURL = 'https://learn.chatgpt.com/docs/' + (variant === 'desktop' ? 'app' : 'ide');
    if (id === 'codebuddy' && variant === 'ide') result.installURL = 'https://www.codebuddy.ai/docs/ide/';
    if (id === 'workbuddy') result.installURL = 'https://www.workbuddy.cn/docs/workbuddy/From-Beginner-to-Expert-Guide/Installation-'
      + ({macos: 'Mac', windows: 'Win', linux: 'UOS'})[os] + '-Guide';
    let steps = item.steps;
    if (id === 'claude-code' && variant === 'vscode') steps = [
      {title: '安装 VS Code 扩展', body: '在 VS Code 扩展商店安装 Anthropic 的 Claude Code。此方案不要求在终端再执行 CLI 的 export。'},
      {title: '打开用户设置 JSON', body: '运行 Preferences: Open User Settings (JSON)。普通 VS Code 默认文件为 {vscodeFile}；Profile、Remote SSH 或 WSL 窗口请以该命令实际打开的位置为准，不要写入项目的 .vscode/settings.json。'},
      {title: '合并连接配置', body: '将下方 claudeCode.environmentVariables 合并到用户设置；同名项替换，不重复追加。清除已有 ANTHROPIC_AUTH_TOKEN 冲突项。API Key 用本站模型 KEY，不能填用户 PAT。', snippets: [0]},
      {title: '重载并选择模型', body: '保存 settings.json，运行 Developer: Reload Window。在 Claude 扩展的模型菜单选择新增模型；ANTHROPIC_CUSTOM_MODEL_OPTION 只添加可选项，不改原默认模型。'},
      {title: '核对调用', body: '新建会话并手动发一条短消息，在本站使用日志核对。若仍要求登录，检查的是 VS Code 用户设置里的环境变量，而不是另一个终端的 export。'},
    ];
    if (id === 'codex' && variant !== 'cli') steps = item.steps.map((step, index) => index === 4 ? {
      title: '重启并选择会话模型', body: variant === 'desktop'
        ? '完全退出桌面应用后重开，在会话模型菜单选择上方模型。Linux 桌面版处于预览阶段，按官方支持的发行版安装。自定义别名需要匹配的模型目录元数据；没有菜单项时不要猜配置，可改用 CLI 的 --model。'
        : '保存两个文件后重载 IDE 窗口，在 Codex 扩展的会话中选择模型。Remote SSH / WSL 扩展运行在远端时，使用远端 CODEX_HOME 的文件；本机用户目录不会自动传过去。',
    } : step);
    if (id === 'codebuddy' && variant === 'cli') {
      result.protocols = ['chat', 'responses', 'anthropic'];
      const messages = options.protocol === 'anthropic';
      steps = [
        {title: '安装并打开 CLI', body: '按 CodeBuddy Code 官方安装页安装 CLI，运行 codebuddy --version。{terminalHint} Windows 原生优先使用 Git Bash；缺少时客户端可回退到 PowerShell。'},
        {title: messages ? '配置当前终端连接' : '找到用户 models.json', body: messages
          ? '此方案使用官方 Anthropic 兼容环境变量入口，不混用 Chat 的 models.json。终端环境只对随后从该终端启动的 CodeBuddy 生效。'
          : '备份 {buddyFile}。用户级文件适用所有项目；项目 .codebuddy/models.json 会覆盖同 id 的模型。'},
        {title: messages ? '设置地址与 KEY' : '合并所选模型', body: messages
          ? '执行下方命令，CODEBUDDY_BASE_URL 使用根地址；不要在后面补 /v1/messages。检查并移除旧模型环境覆盖项，再启动所选模型。'
          : '将这一项合并到 models 数组；同 id 替换，不重复追加。Chat 使用完整 /v1/chat/completions；Responses 用 api: openai-responses 和 /v1 基础地址，不是简单更换路径。', snippets: [0]},
        {title: '启动本次会话', body: '进入项目目录后执行命令，--model 只选择本次会话的模型。确认启动显示的连接和模型。', snippets: [1]},
        {title: '检查调用', body: '用 /status、/model 检查当前模型，再手动发送短消息并核对本站使用日志。辅助任务可能独立选模；本教程不覆盖大模型、小模型或子 Agent 默认映射。{permissionHint}'},
      ];
    }
    result.steps = steps.map(step => ({...step, body: step.body.replace(/\{([a-zA-Z]+)\}/g, (all, name) => values[name] || all)}));
    result.platformHint = os === 'windows' ? 'Windows 原生；本页命令使用 PowerShell。WSL 内运行请选择 Linux。'
      : os === 'macos' ? 'macOS；终端命令使用 Bash / Zsh，桌面启动不继承另一个终端的环境变量。'
        : 'Linux；选择客户端提供的发行版和架构，终端使用 Bash。';
    if (id === 'workbuddy' && os === 'linux') result.platformHint = 'Linux：官方提供统信 UOS、银河麒麟安装说明，不代表任意发行版均受支持。';
    if (id === 'cc-switch' && os === 'linux') result.platformHint = 'Linux：按 Releases 选择包；当前文档要求 glibc 2.35+、WebKitGTK 4.1，RHEL / Rocky / Alma 8–9 不在支持范围。';
    if (id === 'claude-app' && os === 'linux') result.platformHint = 'Claude Linux 桌面版处于 beta。先确认安装版提供 Gateway 入口；没有入口可使用 Claude Code。';
    return result;
  }

  function curlText(origin, model, key, platform, protocol) {
    const path = {chat: '/v1/chat/completions', responses: '/v1/responses', anthropic: '/v1/messages'}[protocol];
    const payload = protocol === 'responses' ? {model, input: '你好'}
      : {model, ...(protocol === 'anthropic' ? {max_tokens: 1024} : {}), messages: [{role: 'user', content: '你好'}]};
    const headers = ['Content-Type: application/json', protocol === 'anthropic' ? 'x-api-key: ' + key : 'Authorization: Bearer ' + key];
    if (protocol === 'anthropic') headers.push('anthropic-version: 2023-06-01');
    const lines = [
      (platform === 'powershell' ? 'curl.exe' : 'curl') + ' --fail-with-body --max-time 60 --request POST ' + quote(origin + path, platform),
      ...headers.map(header => '  -H ' + quote(header, platform)),
      '  --data-raw ' + quote(JSON.stringify(payload), platform),
    ];
    const content = platform === 'powershell'
      ? "# PowerShell 7.3+：保留原生程序参数中的 JSON 引号\nif ($PSVersionTable.PSVersion -lt [version]'7.3') { throw '需要 PowerShell 7.3 或更高版本。' }\n$PSNativeCommandArgumentPassing = 'Standard'\n" + lines.join(' `\n')
      : lines.join(' \\\n');
    return [block('curl · ' + protocol + '（手动执行可能计费）', shellLanguage(platform), content)];
  }

  function build(id, options) {
    input(id, '工具 ID');
    if (!options || typeof options !== 'object' || Array.isArray(options)) throw new Error('请提供配置参数对象。');
    const selected = resolve(id, options);
    const origin = rootOrigin(options.origin);
    const model = input(options.model, '模型 ID');
    const key = input(options.key === undefined ? 'API_KEY' : options.key, 'API Key');
    if (options.platform !== undefined) input(options.platform, '系统');
    const os = operatingSystem(options.platform), filePaths = paths(os);
    const platform = os === 'windows' ? 'powershell' : 'posix';
    const protocol = options.protocol === undefined || options.protocol === '' ? selected.defaultProtocol : options.protocol;
    input(protocol, '协议');
    if (!selected.protocols.includes(protocol)) throw new Error('所选协议不在此教程确认的客户端范围内。');

    switch (id) {
      case 'claude-code': {
        if (selected.variant === 'vscode') return [block('Claude Code · VS Code 用户设置（合并勿覆盖）', 'json',
          json({'claudeCode.environmentVariables': [
            {name: 'ANTHROPIC_BASE_URL', value: origin}, {name: 'ANTHROPIC_API_KEY', value: key},
            {name: 'ANTHROPIC_CUSTOM_MODEL_OPTION', value: model},
          ]}), true, filePaths.vscodeFile)];
        const clear = platform === 'powershell' ? 'Remove-Item Env:ANTHROPIC_AUTH_TOKEN -ErrorAction SilentlyContinue' : 'unset ANTHROPIC_AUTH_TOKEN';
        return [block('Claude Code CLI · 当前终端环境（x-api-key）', shellLanguage(platform),
          [clear, env('ANTHROPIC_BASE_URL', origin, platform), env('ANTHROPIC_API_KEY', key, platform)].join('\n')),
          launch('claude', model, platform)];
      }
      case 'codex':
        return [
          block('Codex · 合并到用户 config.toml（顶层键放在表头前）', 'toml',
            'model_provider = "newapi"\ncli_auth_credentials_store = "file"\n\n'
            + '[model_providers.newapi]\nname = "New API"\nbase_url = ' + JSON.stringify(origin + '/v1')
            + '\nwire_api = "responses"\nrequires_openai_auth = true', false, filePaths.codexDir + (os === 'windows' ? '\\' : '/') + 'config.toml'),
          block('Codex · auth.json（认证文件，先备份原登录）', 'json',
            json({auth_mode: 'apikey', OPENAI_API_KEY: key}), true, filePaths.codexDir + (os === 'windows' ? '\\' : '/') + 'auth.json'),
          ...(selected.variant === 'cli' ? [launch('codex', model, platform)] : []),
        ];
      case 'workbuddy':
        return [...fields('WorkBuddy · 供应商', [['供应商', '自定义 / Custom']]),
          ...fields('WorkBuddy · 自定义模型（完整 URL）', [
            ['自定义协议（高级设置）', '开启'],
            ['URL', origin + '/v1/chat/completions'], ['API Key', key, true], ['模型名 / Model ID', model],
          ])];
      case 'hermes':
        return [block('Hermes · 合并 providers.newapi', 'yaml',
          'providers:\n  newapi:\n    api: ' + JSON.stringify(origin + (protocol === 'anthropic' ? '' : '/v1'))
          + '\n    transport: ' + {chat: 'chat_completions', responses: 'codex_responses', anthropic: 'anthropic_messages'}[protocol]
          + '\n    api_key: ' + JSON.stringify(key), true, filePaths.hermesFile),
          block('Hermes · 选择本次提供商和模型', shellLanguage(platform), "hermes --provider=newapi --model=" + quote(model, platform), false)];
      case 'claude-app':
        return [...fields('Claude App · 桌面第三方推理 Gateway', [
          ['Connection type', 'Gateway'], ['Gateway base URL', origin],
          ['Credential kind', 'Static API key'], ['Gateway auth scheme', 'Bearer'],
          ['Gateway API key', key, true],
        ]), ...fields('Claude App · Models', [['Model ID', model], ['Display name', model]])];
      case 'cursor':
        return [...fields('Cursor · OpenAI API Key', [
          ['OpenAI API Key', key, true],
          ['OpenAI API Key 开关', '启用'],
        ]),
          ...fields('Cursor · 网关地址', [['Override OpenAI Base URL', origin + '/v1']]),
          ...fields('Cursor · 添加模型', [['Add Custom Model（模型 ID）', model]])];
      case 'cc-switch': {
        const target = options.target === undefined ? 'claude-code' : options.target;
        if (!['claude-code', 'codex'].includes(target)) throw new Error('CC Switch 目标只能是 Claude Code 或 Codex。');
        const direct = protocol === (target === 'claude-code' ? 'anthropic' : 'responses');
        return [...fields('CC Switch · ' + (direct ? 'Direct' : 'Routing'), [
          ['目标工具', target === 'claude-code' ? 'Claude Code' : 'Codex'],
          ['上游格式', {chat: 'OpenAI Chat Completions', responses: 'OpenAI Responses', anthropic: 'Anthropic Messages'}[protocol]],
          ['Endpoint URL', origin + (protocol === 'anthropic' ? '' : '/v1')],
          ['API Key', key, true], ['模式', direct ? 'Direct（原生直连）' : 'Routing（启动本地路由，并为目标工具启用）'],
        ]), launch(target === 'claude-code' ? 'claude' : 'codex', model, platform)];
      }
      case 'trae':
        return fields('TRAE 中文版 · 自定义模型', [
          ['API 格式', protocol === 'anthropic' ? 'Anthropic Messages' : 'OpenAI Chat Completions'],
          ['完整 URL 开关', '打开'], ['自定义请求地址', origin + (protocol === 'anthropic' ? '/v1/messages' : '/v1/chat/completions')],
          ['模型 ID', model], ['API 密钥', key, true], ['使用方式', '手动选择此模型；Auto 不使用自定义模型'],
        ]);
      case 'chatbox':
        return [...fields('Chatbox · 供应商协议', [
          ['协议类型', {chat: 'OpenAI API Compatible', responses: 'OpenAI Responses API Compatible', anthropic: 'Claude API Compatible'}[protocol]],
        ]), ...fields('Chatbox · 服务地址', [
          ['API Host（apiHost）', origin + '/v1'],
          ...(protocol === 'anthropic' ? [] : [['API Path（apiPath）', protocol === 'responses' ? '/responses' : '/chat/completions']]),
        ]), ...fields('Chatbox · 密钥与模型', [
          ['API Key', key, true], ['模型 ID', model],
        ])];
      case 'qoder':
        return [...fields('Qoder 独立应用 · 不适用于 IDE', [
          ['适用客户端', '独立 Qoder 应用'],
          ['Provider', 'Custom → ' + (protocol === 'anthropic' ? 'Anthropic Compatible' : 'OpenAI Compatible')],
          ...(protocol === 'anthropic' ? [] : [['API 类型', protocol === 'responses' ? 'Responses API' : 'Chat Completions API']]),
          ['Base URL', origin + '/v1'], ['API Key', key, true],
        ]), ...fields('Qoder · 添加模型', [['Model ID', model]])];
      case 'openclaw': {
        return [block('OpenClaw · 合并到用户 openclaw.json（不要覆盖整份文件）', 'json', json({
          models: {providers: {newapi: {
            baseUrl: origin + (protocol === 'anthropic' ? '' : '/v1'),
            api: {chat: 'openai-completions', responses: 'openai-responses', anthropic: 'anthropic-messages'}[protocol],
            auth: 'api-key', apiKey: clawLiteral(key), models: [{id: clawLiteral(model), name: clawLiteral(model)}],
          }}},
        }), true, filePaths.openclawFile), block('OpenClaw · 会话模型 ID（在聊天模型菜单中选择）', 'text', 'newapi/' + model, false)];
      }
      case 'pi-agent':
        return [block('Pi · 官方 npm 安装（手动执行）', shellLanguage(platform),
          'npm install -g --ignore-scripts @earendil-works/pi-coding-agent', false),
          block('Pi · 合并 providers.newapi', 'json', json({providers: {newapi: {
            baseUrl: origin + (protocol === 'anthropic' ? '' : '/v1'), api: apiType(protocol),
            apiKey: piLiteral(key), models: [{id: model, name: model}],
          }}}), true, filePaths.piFile),
          block('Pi · 选择本次提供商和模型', shellLanguage(platform), 'pi --provider=newapi --model=' + quote(model, platform), false)];
      case 'deepseek-harness':
        return [block('DeepSeek Harness · 启动本机 Web UI', shellLanguage(platform), 'npx @deepseek-ai/dsh web', false),
          ...fields('DeepSeek Harness · 自定义模型 API', [
            ['Provider ID', 'newapi-' + protocol], ['显示名称', 'New API ' + protocol],
            ['基础 URL', origin + (protocol === 'anthropic' ? '' : '/v1')],
            ['API 协议', {chat: 'OpenAI Chat Completions', responses: 'OpenAI Responses', anthropic: 'Anthropic Messages'}[protocol]],
            ['API 密钥', key, true],
          ]), ...fields('DeepSeek Harness · 模型目录', [['模型 ID', model], ['显示名称', model]])];
      case 'codebuddy': {
        if (selected.variant === 'cli' && protocol === 'anthropic') return [
          block('CodeBuddy CLI · 当前终端 Anthropic 兼容连接', shellLanguage(platform),
            [env('CODEBUDDY_BASE_URL', origin, platform), env('CODEBUDDY_API_KEY', key, platform)].join('\n')),
          launch('codebuddy', model, platform),
        ];
        if (/\$\{/.test(key)) throw new Error('此 KEY 含客户端会展开的环境变量语法，请改用客户端的安全凭据输入。');
        return [block('CodeBuddy · 合并到 models 数组（同 id 替换）', 'json', json({models: [{
          id: model, name: model, vendor: 'New API', apiKey: key,
          ...(protocol === 'responses' ? {api: 'openai-responses'} : {}),
          url: origin + (protocol === 'responses' ? '/v1' : '/v1/chat/completions'),
        }]}), true, filePaths.buddyFile), selected.variant === 'cli' ? launch('codebuddy', model, platform)
          : block('CodeBuddy · 在模型菜单中选择', 'text', model, false)];
      }
      case 'curl':
        return curlText(origin, model, key, platform, protocol);
      default:
        throw new Error('此工具尚无已确认的配置生成方式。');
    }
  }

  function buildMcp(id, origin) {
    const url = rootOrigin(origin) + '/cockpit/mcp';
    if (id === 'codex') return block('Codex · 合并到 config.toml（MCP 使用独立的用户 PAT）', 'toml',
      '[mcp_servers.newapi_account]\nurl = ' + JSON.stringify(url)
      + '\nbearer_token_env_var = "NEW_API_USER_PAT"\nenabled_tools = ["get_balance", "get_usage", "list_keys", "list_key_groups", "create_key", "delete_key", "change_key_group", "reveal_key"]', false);
    if (id === 'cursor') return block('Cursor · 合并到 MCP 配置（替换用户 PAT 占位符）', 'json', json({
      mcpServers: {newapi_account: {url, headers: {Authorization: 'Bearer USER_API_TOKEN'}}},
    }), false);
    throw new Error('未知 MCP 客户端。');
  }

  const api = Object.freeze({guides, resolve, build, buildMcp});
  if (typeof window === 'object') window.CockpitManualGuides = api;
  if (typeof module === 'object' && module.exports) module.exports = api;
})();
