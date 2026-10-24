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
      "description": "Claude Code CLI 与 VS Code 扩展：连接 Anthropic Messages 网关。",
      "protocols": [
        "anthropic"
      ],
      "defaultProtocol": "anthropic",
      "steps": [
        {
          "title": "设置终端连接",
          "body": "在终端运行本步的环境变量配置，设置站点根地址和 API Key。ANTHROPIC_BASE_URL 不加 /v1；清除冲突的 ANTHROPIC_AUTH_TOKEN。",
          "snippets": [
            0
          ]
        },
        {
          "title": "按本次模型启动",
          "body": "在同一终端运行本步的启动命令。--model 使用本页所选模型 ID，只影响这次启动；不写入默认模型或 Sonnet / Opus / Haiku 映射。",
          "snippets": [
            2
          ]
        },
        {
          "title": "配置 VS Code 扩展",
          "body": "运行 Preferences: Open User Settings (JSON)，合并本步的 claudeCode.environmentVariables 配置，再重载编辑器。ANTHROPIC_CUSTOM_MODEL_OPTION 只把所选 ID 加入模型菜单，不设置默认模型；在菜单中手动选择它。",
          "snippets": [
            1
          ]
        },
        {
          "title": "检查连接",
          "body": "CLI 用 /status 查看地址和凭据。扩展在自己的模型菜单中选择模型；不要用 CLI 终端环境替代扩展的设置。"
        },
        {
          "title": "保存位置与环境范围",
          "body": "CLI 的 export 仅影响当前终端；若要持久化，先备份本人 shell 配置再合并。VS Code 扩展必须将 JSON 合并到用户设置，不是工作区共享文件。编辑完用完全退出再打开的方式重载；不要将包含 KEY 的设置加入仓库。"
        },
        {
          "title": "核对调用结果与排错",
          "body": "先发送一条不含敏感信息的短消息，确认后再进行长任务。到本站使用日志核对模型、时间和消费；401 检查 KEY，403 检查模型或 IP 权限，404 检查地址路径及模型 ID。超时不代表未计费，先查日志，不要连续点击重试。"
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
          "body": "先退出 Codex、桌面应用及 IDE 扩展，备份已有 config.toml 和 auth.json。默认目录：macOS/Linux 为 ~/.codex/，Windows 为 %USERPROFILE%\\.codex\\；如果设置了 CODEX_HOME，两个文件都放在该目录，不要分散保存。"
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
          "body": "macOS/Linux 在本机执行 chmod 600 ~/.codex/auth.json（自定义 CODEX_HOME 时改路径）；Windows 只允许本人读取。检查环境中的 OPENAI_API_KEY、CODEX_API_KEY 或旧提供商覆盖项，避免加载其他凭据。cli_auth_credentials_store = \"file\" 强制读取文件，不依赖终端导出变量，桌面启动也能读取。"
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
          "title": "OpenAI：API Key 与提供商认证选择",
          "url": "https://learn.chatgpt.com/docs/auth#alternative-model-providers"
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
          "body": "安装或更新 WorkBuddy 桌面端，进入设置里的模型配置，打开自定义模型列表并选择添加。不要把 CodeBuddy IDE、插件或移动端的设置路径当成 WorkBuddy 桌面端。"
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
        }
      ]
    },
    {
      "id": "hermes",
      "title": "Hermes",
      "description": "NousResearch Hermes Agent：config.yaml 中的自定义 OpenAI 兼容提供商。",
      "protocols": [
        "chat"
      ],
      "defaultProtocol": "chat",
      "steps": [
        {
          "title": "确认是 Hermes Agent 并备份",
          "body": "本教程针对 NousResearch 的 Hermes Agent，不是同名聊天客户端。先完成官方安装并运行 hermes；备份 ~/.hermes/config.yaml。使用不同 profile 或 HERMES_HOME 时，在当前 profile 的配置目录中操作；Windows 建议按官方说明在 WSL 中运行，用 WSL 的 home 目录。"
        },
        {
          "title": "合并模型配置",
          "body": "将本步 config.yaml 示例合并到已有 model: 配置，不要重复添加顶层 model。provider 使用 custom，base_url 填本站 /v1，api_key 填所选模型调用 KEY。保留原默认模型，第 4 步再用 --model 选择本次会话的模型。",
          "snippets": [
            0
          ]
        },
        {
          "title": "检查旧凭据与辅助任务",
          "body": "如果原配置或环境里已有 OPENAI_BASE_URL、OPENAI_API_KEY、自定义 provider 或启动参数，请核对优先级，避免覆盖本次连接。辅助任务（标题、压缩、视觉等）可能另有 auxiliary 配置；不要仅改主模型就认为所有任务都已走本站。"
        },
        {
          "title": "重新启动本次会话",
          "body": "退出旧 Hermes 进程，用本步 hermes --model 命令启动。模型 ID 按列表原样填写，不自行加 openai/ 或其他前缀；已存在的 default 配置保持不变，由 --model 仅覆盖本次会话。",
          "snippets": [
            1
          ]
        },
        {
          "title": "验证并保护密钥",
          "body": "检查启动时的 provider、base URL 与模型，再发送简单文本并核对本站日志。如果仍在调用旧服务，检查 profile、环境变量和辅助模型设置。config.yaml 含明文 KEY，macOS/Linux 将文件权限设为 600；不要上传、截图或分享。"
        }
      ],
      "notes": [
        "选择 custom，不是 OpenRouter、Codex OAuth，也不是只能用于辅助任务的 main。",
        "这里只生成主模型连接和本次启动命令，不自动安装、启动 Hermes 或触发辅助模型调用。"
      ],
      "sources": [
        {
          "title": "Hermes 官方：模型配置示例",
          "url": "https://github.com/NousResearch/hermes-agent/blob/main/cli-config.yaml.example"
        },
        {
          "title": "Hermes 官方：配置目录、主模型与辅助模型",
          "url": "https://github.com/NousResearch/hermes-agent/blob/main/website/docs/user-guide/configuration.md"
        }
      ]
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
          "body": "此教程仅针对支持第三方推理配置的 Claude 桌面应用（macOS/Windows），不是 claude.ai 网页或手机 App。升级后，在 Help → Troubleshooting → Enable Developer Mode 启用开发者模式，再进入 Developer → Configure Third-Party Inference；如果入口被组织策略隐藏，请联系管理员，不要照搬 Claude Code 环境变量。"
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
          "title": "Cloudflare 官方集成指南：Claude Desktop Gateway 字段与菜单",
          "url": "https://developers.cloudflare.com/ai-gateway/integrations/coding-agents/claude-desktop/"
        }
      ]
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
          "body": "进入 Cursor Settings → Models，按本步示例填入 OpenAI API Key，保存并启用对应开关。",
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
        },
        {
          "title": "核对调用结果与排错",
          "body": "先发送一条不含敏感信息的短消息，确认后再进行长任务。到本站使用日志核对模型、时间和消费；401 检查 KEY，403 检查模型或 IP 权限，404 检查地址路径及模型 ID。超时不代表未计费，先查日志，不要连续点击重试。"
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
        }
      ]
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
        },
        {
          "title": "核对调用结果与排错",
          "body": "先发送一条不含敏感信息的短消息，确认后再进行长任务。到本站使用日志核对模型、时间和消费；401 检查 KEY，403 检查模型或 IP 权限，404 检查地址路径及模型 ID。超时不代表未计费，先查日志，不要连续点击重试。"
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
        }
      ]
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
        },
        {
          "title": "核对调用结果与排错",
          "body": "先发送一条不含敏感信息的短消息，确认后再进行长任务。到本站使用日志核对模型、时间和消费；401 检查 KEY，403 检查模型或 IP 权限，404 检查地址路径及模型 ID。超时不代表未计费，先查日志，不要连续点击重试。"
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
        }
      ]
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
          "body": "在独立 Qoder 应用进入 Settings → Models → + Add。Qoder IDE 是另一个客户端，只有预置服务商的入口不能填写本站网关地址。"
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
        },
        {
          "title": "核对调用结果与排错",
          "body": "先发送一条不含敏感信息的短消息，确认后再进行长任务。到本站使用日志核对模型、时间和消费；401 检查 KEY，403 检查模型或 IP 权限，404 检查地址路径及模型 ID。超时不代表未计费，先查日志，不要连续点击重试。"
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
        }
      ]
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
        },
        {
          "title": "核对调用结果与排错",
          "body": "先发送一条不含敏感信息的短消息，确认后再进行长任务。到本站使用日志核对模型、时间和消费；401 检查 KEY，403 检查模型或 IP 权限，404 检查地址路径及模型 ID。超时不代表未计费，先查日志，不要连续点击重试。"
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
        }
      ]
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
          "body": "将 JSON 的 models.providers.newapi 合并到 ~/.openclaw/openclaw.json。不替换整份文件，不修改 agents.defaults、默认模型、回退模型或允许列表。",
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
        },
        {
          "title": "核对调用结果与排错",
          "body": "先发送一条不含敏感信息的短消息，确认后再进行长任务。到本站使用日志核对模型、时间和消费；401 检查 KEY，403 检查模型或 IP 权限，404 检查地址路径及模型 ID。超时不代表未计费，先查日志，不要连续点击重试。"
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
          "body": "macOS/Linux 使用 POSIX；Windows 使用 PowerShell 7.3+ 与 curl.exe。"
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
        },
        {
          "title": "核对调用结果与排错",
          "body": "先发送一条不含敏感信息的短消息，确认后再进行长任务。到本站使用日志核对模型、时间和消费；401 检查 KEY，403 检查模型或 IP 权限，404 检查地址路径及模型 ID。超时不代表未计费，先查日志，不要连续点击重试。"
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
        }
      ]
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
  const block = (title, language, content, containsKey = true) => ({title, language, content, containsKey});
  const fields = (title, entries, containsKey = true) => [block(title + '（字段清单，非导入文件）', 'text',
    entries.map(([name, value]) => name + '：' + value).join('\n'), containsKey)];
  const env = (name, value, platform) => platform === 'powershell'
    ? '$env:' + name + ' = ' + quote(value, platform)
    : 'export ' + name + '=' + quote(value, platform);
  const launch = (client, model, platform) => block(client + ' CLI · 本次启动选择模型',
    shellLanguage(platform), client + ' --model=' + quote(model, platform), false);
  // OpenClaw interpolates configuration string VALUES, not JSON object keys.
  const clawLiteral = value => value.replace(/\$\{([A-Z_][A-Z0-9_]*)\}/g, (_, name) => '$${' + name + '}');

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
    const selected = guides.find(item => item.id === id);
    if (!selected) throw new Error('未知工具，不能生成配置。');
    if (!options || typeof options !== 'object' || Array.isArray(options)) throw new Error('请提供配置参数对象。');
    const origin = rootOrigin(options.origin);
    const model = input(options.model, '模型 ID');
    const key = input(options.key === undefined ? 'API_KEY' : options.key, 'API Key');
    const platform = options.platform === undefined ? 'posix' : input(options.platform, '终端类型');
    if (!['posix', 'powershell'].includes(platform)) throw new Error('终端类型只能是 posix 或 powershell。');
    const protocol = options.protocol === undefined || options.protocol === '' ? selected.defaultProtocol : options.protocol;
    input(protocol, '协议');
    if (!selected.protocols.includes(protocol)) throw new Error('所选协议不在此教程确认的客户端范围内。');

    switch (id) {
      case 'claude-code': {
        const clear = platform === 'powershell' ? 'Remove-Item Env:ANTHROPIC_AUTH_TOKEN -ErrorAction SilentlyContinue' : 'unset ANTHROPIC_AUTH_TOKEN';
        return [block('Claude Code CLI · 当前终端环境（x-api-key）', shellLanguage(platform),
          [clear, env('ANTHROPIC_BASE_URL', origin, platform), env('ANTHROPIC_API_KEY', key, platform)].join('\n')),
          block('Claude Code · VS Code 用户设置（合并勿覆盖）', 'json',
            json({'claudeCode.environmentVariables': [
              {name: 'ANTHROPIC_BASE_URL', value: origin}, {name: 'ANTHROPIC_API_KEY', value: key},
              {name: 'ANTHROPIC_CUSTOM_MODEL_OPTION', value: model},
            ]})),
          launch('claude', model, platform)];
      }
      case 'codex':
        return [
          block('Codex · 合并到用户 config.toml（顶层键放在表头前）', 'toml',
            'model_provider = "newapi"\ncli_auth_credentials_store = "file"\n\n'
            + '[model_providers.newapi]\nname = "New API"\nbase_url = ' + JSON.stringify(origin + '/v1')
            + '\nwire_api = "responses"\nrequires_openai_auth = true', false),
          block('Codex · auth.json（认证文件，先备份原登录）', 'json',
            json({auth_mode: 'apikey', OPENAI_API_KEY: key})),
          launch('codex', model, platform),
        ];
      case 'workbuddy':
        return [...fields('WorkBuddy · 供应商', [['供应商', '自定义 / Custom']], false),
          ...fields('WorkBuddy · 自定义模型（完整 URL）', [
            ['自定义协议（高级设置）', '开启'],
            ['URL', origin + '/v1/chat/completions'], ['API Key', key], ['模型名 / Model ID', model],
          ])];
      case 'hermes':
        return [block('Hermes · 合并到当前 profile 的 config.yaml', 'yaml',
          'model:\n  provider: "custom"\n  base_url: ' + JSON.stringify(origin + '/v1')
          + '\n  api_key: ' + JSON.stringify(key)), launch('hermes', model, platform)];
      case 'claude-app':
        return [...fields('Claude App · 桌面第三方推理 Gateway', [
          ['Connection type', 'Gateway'], ['Gateway base URL', origin],
          ['Credential kind', 'Static API key'], ['Gateway auth scheme', 'Bearer'],
          ['Gateway API key', key],
        ]), ...fields('Claude App · Models', [['Model ID', model], ['Display name', model]], false)];
      case 'cursor':
        return [...fields('Cursor · OpenAI API Key', [
          ['OpenAI API Key', key],
          ['OpenAI API Key 开关', '启用'],
        ]),
          ...fields('Cursor · 网关地址', [['Override OpenAI Base URL', origin + '/v1']], false),
          ...fields('Cursor · 添加模型', [['Add Custom Model（模型 ID）', model]], false)];
      case 'cc-switch': {
        const target = options.target === undefined ? 'claude-code' : options.target;
        if (!['claude-code', 'codex'].includes(target)) throw new Error('CC Switch 目标只能是 Claude Code 或 Codex。');
        const direct = protocol === (target === 'claude-code' ? 'anthropic' : 'responses');
        return [...fields('CC Switch · ' + (direct ? 'Direct' : 'Routing'), [
          ['目标工具', target === 'claude-code' ? 'Claude Code' : 'Codex'],
          ['上游格式', {chat: 'OpenAI Chat Completions', responses: 'OpenAI Responses', anthropic: 'Anthropic Messages'}[protocol]],
          ['Endpoint URL', origin + (protocol === 'anthropic' ? '' : '/v1')],
          ['API Key', key], ['模式', direct ? 'Direct（原生直连）' : 'Routing（启动本地路由，并为目标工具启用）'],
        ]), launch(target === 'claude-code' ? 'claude' : 'codex', model, platform)];
      }
      case 'trae':
        return fields('TRAE 中文版 · 自定义模型', [
          ['API 格式', protocol === 'anthropic' ? 'Anthropic Messages' : 'OpenAI Chat Completions'],
          ['完整 URL 开关', '打开'], ['自定义请求地址', origin + (protocol === 'anthropic' ? '/v1/messages' : '/v1/chat/completions')],
          ['模型 ID', model], ['API 密钥', key], ['使用方式', '手动选择此模型；Auto 不使用自定义模型'],
        ]);
      case 'chatbox':
        return [...fields('Chatbox · 供应商协议', [
          ['协议类型', {chat: 'OpenAI API Compatible', responses: 'OpenAI Responses API Compatible', anthropic: 'Claude API Compatible'}[protocol]],
        ], false), ...fields('Chatbox · 服务地址', [
          ['API Host（apiHost）', origin + '/v1'],
          ...(protocol === 'anthropic' ? [] : [['API Path（apiPath）', protocol === 'responses' ? '/responses' : '/chat/completions']]),
        ], false), ...fields('Chatbox · 密钥与模型', [
          ['API Key', key], ['模型 ID', model],
        ])];
      case 'qoder':
        return [...fields('Qoder 独立应用 · 不适用于 IDE', [
          ['适用客户端', '独立 Qoder 应用'],
          ['Provider', 'Custom → ' + (protocol === 'anthropic' ? 'Anthropic Compatible' : 'OpenAI Compatible')],
          ...(protocol === 'anthropic' ? [] : [['API 类型', protocol === 'responses' ? 'Responses API' : 'Chat Completions API']]),
          ['Base URL', origin + '/v1'], ['API Key', key],
        ]), ...fields('Qoder · 添加模型', [['Model ID', model]], false)];
      case 'openclaw': {
        return [block('OpenClaw · 合并到用户 openclaw.json（不要覆盖整份文件）', 'json', json({
          models: {providers: {newapi: {
            baseUrl: origin + (protocol === 'anthropic' ? '' : '/v1'),
            api: {chat: 'openai-completions', responses: 'openai-responses', anthropic: 'anthropic-messages'}[protocol],
            auth: 'api-key', apiKey: clawLiteral(key), models: [{id: clawLiteral(model), name: clawLiteral(model)}],
          }}},
        })), block('OpenClaw · 会话模型 ID（在聊天模型菜单中选择）', 'text', 'newapi/' + model, false)];
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
      + '\nbearer_token_env_var = "NEW_API_USER_PAT"\nenabled_tools = ["get_balance", "get_usage"]', false);
    if (id === 'cursor') return block('Cursor · 合并到 MCP 配置（替换用户 PAT 占位符）', 'json', json({
      mcpServers: {newapi_account: {url, headers: {Authorization: 'Bearer USER_API_TOKEN'}}},
    }), false);
    throw new Error('未知 MCP 客户端。');
  }

  const api = Object.freeze({guides, build, buildMcp});
  if (typeof window === 'object') window.CockpitManualGuides = api;
  if (typeof module === 'object' && module.exports) module.exports = api;
})();
