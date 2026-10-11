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
          "body": "选择“CLI 环境变量”片段，在终端设置站点根地址和 API Key。ANTHROPIC_BASE_URL 不加 /v1；清除冲突的 ANTHROPIC_AUTH_TOKEN。"
        },
        {
          "title": "按本次模型启动",
          "body": "选择“CLI 启动命令”片段，在同一终端启动 Claude Code。--model 使用本页所选模型 ID，只影响这次启动；不写入默认模型或 Sonnet / Opus / Haiku 映射。"
        },
        {
          "title": "配置 VS Code 扩展",
          "body": "运行 Preferences: Open User Settings (JSON)，合并下方 claudeCode.environmentVariables 片段，再重载编辑器。ANTHROPIC_CUSTOM_MODEL_OPTION 只把所选 ID 加入模型菜单，不设置默认模型；在菜单中手动选择它。"
        },
        {
          "title": "检查连接",
          "body": "CLI 用 /status 查看地址和凭据。扩展在自己的模型菜单中选择模型；不要用 CLI 终端环境替代扩展的设置。"
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
      "description": "Codex CLI、桌面应用与 IDE 扩展：自定义 Responses 提供商。",
      "protocols": [
        "responses"
      ],
      "defaultProtocol": "responses",
      "steps": [
        {
          "title": "合并配置",
          "body": "将连接片段合并到用户 config.toml：macOS/Linux 是 ~/.codex/config.toml，Windows 是 %USERPROFILE%\\.codex\\config.toml。model_provider 放在表头之前；已有同名表时合并字段，不重复添加。"
        },
        {
          "title": "设置凭据",
          "body": "在启动客户端的进程环境中设置 NEW_API_MODEL_KEY。CLI 和 IDE 扩展共享 Codex 配置，但终端导出的变量不会自动传给从桌面打开的应用或编辑器。"
        },
        {
          "title": "选择本次模型",
          "body": "CLI 使用下方 --model 启动命令；桌面应用与 IDE 扩展在会话模型菜单里选择。连接片段不写顶层 model，也不改 web_search、推理强度或上下文窗口。"
        }
      ],
      "notes": [
        "当前 Codex 的网关入口是 Responses；这不是 New API 或所选模型不支持 Chat。",
        "自定义模型别名需要对应的模型目录才能出现在客户端菜单；CLI 可直接用本页生成的 --model 启动命令。"
      ],
      "sources": [
        {
          "title": "OpenAI：自定义模型提供商",
          "url": "https://learn.chatgpt.com/docs/config-file/config-advanced#custom-model-providers"
        },
        {
          "title": "OpenAI：Codex 支持的接口",
          "url": "https://learn.chatgpt.com/docs/models#other-models"
        },
        {
          "title": "OpenAI：CLI 启动参数",
          "url": "https://learn.chatgpt.com/docs/cli/reference"
        },
        {
          "title": "OpenAI：IDE 扩展设置",
          "url": "https://learn.chatgpt.com/docs/ide/settings"
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
          "body": "进入 Cursor Settings → Models，填入 OpenAI API Key，保存并启用对应开关。"
        },
        {
          "title": "填写网关地址",
          "body": "Override OpenAI Base URL 填站点根地址加 /v1，不填完整 /chat/completions 路径。"
        },
        {
          "title": "添加并选择模型",
          "body": "用 Add Custom Model 添加本页所选模型 ID，然后在对话中选择它。Refresh Model List 更新的是 Cursor 内置目录，不会从本站 /v1/models 发现模型。"
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
          "body": "按下方字段填写 Endpoint URL、API Key 和上游格式。Claude Code 直连 Messages，Codex 直连 Responses；跨协议时选择 Routing，并为目标客户端启用本地路由。"
        },
        {
          "title": "不设置默认模型",
          "body": "不填写默认模型或 Claude 模型映射。Codex 的模型映射表首行会自动成为默认模型，本教程不生成该表；使用下方 --model 命令选择本次模型。"
        },
        {
          "title": "启用并启动",
          "body": "启用该提供商，确认地址和凭据已写入目标工具。跨协议转换时本地路由必须保持运行，再用下方命令启动目标客户端。"
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
          "body": "选择 OpenAI Chat Completions 或 Anthropic Messages 格式，打开“完整 URL”开关，填写下方请求地址、模型 ID 和 API 密钥。"
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
          "body": "Provider → Custom：Chat / Responses 选择 OpenAI Compatible，再选对应 API 类型；Messages 选择 Anthropic Compatible。Base URL、API Key 和 Model ID 按下方字段填写。"
        },
        {
          "title": "添加模型",
          "body": "点击 Next，按模型实际能力填写上下文、Vision、Thinking。Validate and Add Model 验证连接后保存；随后在对话中选择模型。"
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
          "body": "在设置中添加自建供应商。按本页协议选择 OpenAI API Compatible、OpenAI Responses API Compatible 或 Claude API Compatible。"
        },
        {
          "title": "填写地址",
          "body": "Chat / Responses 的 API Host 与 API Path 分开填写；Claude 兼容的 API Host 填根地址加 /v1，由客户端追加 /messages。按下方字段填写，不重复路径。"
        },
        {
          "title": "选择会话模型",
          "body": "填写 API Key，手动添加本页模型 ID，然后在会话中选择供应商与模型。字段清单不是 Chatbox 导入文件。"
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
          "body": "将 JSON 的 models.providers.newapi 合并到 ~/.openclaw/openclaw.json。不替换整份文件，不修改 agents.defaults、默认模型、回退模型或允许列表。"
        },
        {
          "title": "选择会话模型",
          "body": "在 Control UI 新建聊天时，从模型菜单选择 newapi/模型ID。已有会话切换时，可用 /model 加模型引用及 -s，仅切换当前会话。"
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
          "body": "选择 Chat、Responses 或 Anthropic Messages，下方生成对应的地址、请求头与请求体。"
        },
        {
          "title": "选择终端",
          "body": "macOS/Linux 使用 POSIX；Windows 使用 PowerShell 7.3+ 与 curl.exe。"
        },
        {
          "title": "发送请求",
          "body": "复制到终端后手动执行，请求内容是“你好”。请求体的 model 使用本页所选 ID，不设置客户端默认模型。"
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
  const fields = (title, entries) => [block(title + '（字段清单，非导入文件）', 'text',
    entries.map(([name, value]) => name + '：' + value).join('\n'))];
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
            'model_provider = "newapi"\n\n'
            + '[model_providers.newapi]\nname = "New API"\nbase_url = ' + JSON.stringify(origin + '/v1')
            + '\nwire_api = "responses"\nenv_key = "NEW_API_MODEL_KEY"', false),
          block('Codex · 启动进程的环境变量（不是 TOML）', shellLanguage(platform), env('NEW_API_MODEL_KEY', key, platform)),
          launch('codex', model, platform),
        ];
      case 'cursor':
        return fields('Cursor · OpenAI 兼容聊天', [
          ['Override OpenAI Base URL', origin + '/v1'], ['OpenAI API Key', key],
          ['OpenAI API Key 开关', '启用'], ['Add Custom Model（模型 ID）', model],
        ]);
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
        return fields('Chatbox · 自建供应商', [
          ['协议类型', {chat: 'OpenAI API Compatible', responses: 'OpenAI Responses API Compatible', anthropic: 'Claude API Compatible'}[protocol]],
          ['API Host（apiHost）', origin + '/v1'],
          ...(protocol === 'anthropic' ? [] : [['API Path（apiPath）', protocol === 'responses' ? '/responses' : '/chat/completions']]),
          ['API Key', key], ['模型 ID', model],
        ]);
      case 'qoder':
        return fields('Qoder 独立应用 · 不适用于 IDE', [
          ['适用客户端', '独立 Qoder 应用'],
          ['Provider', 'Custom → ' + (protocol === 'anthropic' ? 'Anthropic Compatible' : 'OpenAI Compatible')],
          ...(protocol === 'anthropic' ? [] : [['API 类型', protocol === 'responses' ? 'Responses API' : 'Chat Completions API']]),
          ['Base URL', origin + '/v1'], ['API Key', key], ['Model ID', model],
        ]);
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

  const api = Object.freeze({guides, build});
  if (typeof window === 'object') window.CockpitManualGuides = api;
  if (typeof module === 'object' && module.exports) module.exports = api;
})();
