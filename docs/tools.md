# 辅助工具与插件下载

系统侧栏的“文档”下有三个平级入口：

| 内容 | 页面 | 用途 |
| --- | --- | --- |
| 模型使用 | `/cockpit/manual` | 选择本人 KEY 和模型，查看十二类客户端的接入步骤与配置示例 |
| 辅助工具 | `/cockpit/tools` | 下载 VS Code 插件，设置账户连接、管理 KEY、生成 Claude / Codex 草稿；配置账户 MCP |
| API 文档 | `/cockpit/docs/` | 管理员查阅接口、在线调试及下载 OpenAPI，原权限和操作确认不变 |

普通用户可以打开模型使用、辅助工具和下载插件；API 文档仍仅管理员可访问，不因导航重组扩大接口权限。
模型使用页原有的 MCP 内容已移到辅助工具，旧的 `/cockpit/manual#manual-mcp-title` 书签会转到新位置。

## VS Code 独立插件与本站下载

插件 0.4.2 可独立连接 New API，不依赖 Cockpit 或账户 MCP。也可在 GitHub Release 下载 VSIX。本站提供的下载方式是在 **文档 → 辅助工具 → VS Code 插件与下载** 中点击“下载 VSIX”。包来自当前 Cockpit 镜像内的文件，不依赖 GitHub、市场或维护者电脑上的临时路径。
版本号、文件名、大小和 SHA-256 都从镜像内的包读取，不在页面写死。稳定下载入口是 `/cockpit/downloads/vscode`，每次下载都检查登录状态，支持普通用户。

1. VS Code 扩展面板 → 右上角“…” → **从 VSIX 安装**。已有旧版直接覆盖并重载窗口，账户连接保留。
2. 在 New API **安全与访问**创建用户 API PAT：余额需 `profile:read`；KEY 列表/修改需 `api_key:read` / `api_key:write`；复制 KEY 或配置客户端另需 `api_key:reveal`。
3. 执行 **Cockpit 余额：配置账户连接**，填写本站首页地址及用户 PAT，不使用 sk- 模型 KEY。地址与 PAT 保存在 VS Code 密钥库。
4. 状态栏显示账户和可用余额；侧栏独立 KEY 区支持申请、复制、删除和改分组。缓存不自动过期，手动刷新才更新，每次修改前后各查询一次，KEY 查询不受余额刷新间隔影响。
5. 选择 KEY → **配置 Claude / Codex** → 编辑未保存草稿 → 手动保存。Claude 使用实际的 VS Code 用户 settings.json；Codex 识别 CODEX_HOME，打开 config.toml + auth.json，两份都要保存后再重载客户端。

同名连接配置覆盖而不追加重复项，保留无关设置、模型选择、MCP 和注释。自动保存不会修改原配置；原文件在别处变更时停止覆盖。KEY 操作和结果确认都在插件内完成。
完整 KEY 仅在用户主动复制或配置客户端时读取，复用连接内存缓存，不查询模型目录或调用模型。

客户端配置草稿目前限本机窗口，不猜测 SSH、WSL 或容器中的用户目录。配置文件和编辑器备份可能包含明文 KEY，不要分享、提交仓库或同步。
余额直接读取 New API `/api/user/self`，额度单位首次读取后缓存一小时。KEY 改组使用官方 PUT；没有额外的 New API 镜像或补丁要求。应用与插件分别编号，Cockpit v0.4.1 随附插件 0.4.2。
完整说明与排错见[VS Code 插件手册](../vsix/README.md)。

## 账户 MCP

辅助工具的账户 MCP 区保留原来的配置步骤、Codex/Cursor 占位符示例和复制功能；不依赖模型令牌或模型选择，也不读取用户 PAT。
使用本人用户 API PAT，`profile:read` 查询余额，`usage:read` 查询本人用量；不需要 KEY 管理写权限。
服务地址为本站 `/cockpit/mcp`，不是模型的 `/v1`。具体协议、时间/金额口径和接入示例见[账户 MCP](mcp.md)。

## 构建与打包

Dockerfile 增加独立 Node 构建阶段：按 `vsix/package-lock.json` 安装构建依赖，从 `vsix/` 源码打包 VSIX，再生成安装包清单。Python 运行阶段只复制 VSIX 和清单，不带 Node、源码测试或 node_modules。
清单与安装包一起进入 Python 包的 `bundled_clients/` 数据目录。运行时校验大小、SHA-256 和包内版本/扩展标识；包缺失或损坏时隐藏下载按钮，下载接口返回 404，不跳到外部地址或发送 HTML 假装安装包。
构建失败会终止镜像构建，不会静默生成一个缺少插件的正式镜像。不新增数据库表、迁移、后台下载任务或 Nginx 路由。

源码直接启动时，可先在仓库根目录执行：

```sh
npm ci --prefix vsix --ignore-scripts
npm run --prefix vsix bundle
```

该命令重新打包插件并生成 `src/new_api_cockpit/bundled_clients/`；随后重启 Python 应用。这个生成目录不提交，也不作为 Docker 构建输入；镜像始终在构建阶段重新打包。
Python wheel/sdist 如需提供相同下载内容，先执行同一条 bundle 命令再构建 Python 包；单独安装未含下载数据的源码包时，页面会明确显示未包含安装包。
