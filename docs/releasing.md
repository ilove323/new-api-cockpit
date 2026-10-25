# 发版与镜像

维护者：ilove323。正式版本唯一来源为 `pyproject.toml`，历史发布说明保存在 [GitHub Releases](https://github.com/ilove323/new-api-cockpit/releases)。
部署使用预构建镜像；源码构建部署另见[独立手册](deployment-source.md)。本页说明维护者如何生成和发布配套制品，不在服务器上执行安装。

## 仓库权限

GitHub Actions 需要 `packages:write` 和 `contents:write`。GHCR 包须设为 public 才能匿名拉取，公开仓库不会自动使包公开。
`main` 建议禁止强制推送并要求 CI 通过；当前只有一个维护者，不强制另一人的 PR 审批。

## 发布流程

1. 确认源码、Compose、部署文档、迁移及功能范围一致，在 `pyproject.toml` 更新为新的正式版本，插件需要更新时，执行 `npm --prefix vsix version A.B.C --no-git-tag-version` 同步插件及锁文件。应用和插件分别编号，发布记录明确镜像内包含的插件版本；不能用新代码覆盖已发布标签或旧版本镜像。
2. 通过 PR 合并到 `main` 并通过 CI，准备功能、升级、数据库及兼容性说明。
3. 创建与版本匹配的 `vX.Y.Z` 标签并推送标签；目前仅支持正式标签，不接受预发布后缀。
4. `release.yml` 复用 CI，校验标签与版本后构建 AMD64/ARM64 镜像，推送 `ghcr.io/ilove323/new-api-cockpit:X.Y.Z`；保留 `latest` 供发现，部署固定精确版本或 digest。
5. 工作流执行 `deploy/build_release_bundle.py`，生成全量安装包及独立校验文件，同时保留已有 New API 接入所用的散文件附件。
6. 工作流从已推送的镜像导出 VSIX 和校验清单，与安装包、`image-digest.txt` 一起上传 Release；提前创建 Release 时仅补充附件，不覆盖手写说明。核对镜像、附件均成功发布后再部署；只创建 Release 页面不表示镜像和安装包已就绪。

开发分支推送不发布镜像或 Release，发布工作流也不部署服务器。尚未发布的功能不能通过把现有正式镜像换上新模板获得。
Python 依赖使用 `requirements.txt`；前端构建由 `frontend/studio/package-lock.json` 锁定，提交配套生成文件，运行镜像不在启动时构建前端。

## 发布附件

| 使用方式 | 附件 | 校验 |
|---|---|---|
| 新服务器全量安装 | `new-api-cockpit-vX.Y.Z-deploy.tar.gz` | 同名 `.tar.gz.sha256` |
| 接入已有 New API | `compose.release.yml`、`default.env.example`、`nginx.conf.example`、`image-studio-capabilities.example.json`、`source_pat_function.sql` | `SHA256SUMS`，只覆盖这组散文件 |
| VS Code 插件 | `new-api-cockpit-balance-A.B.C.vsix`、`vscode-extension.json` | 清单中的 SHA-256；安装包从发布镜像导出，不单独重建 |
| 固定镜像 | `image-digest.txt` | 记录发布镜像的多架构索引 digest，可用于拉取和固定部署 |

完整安装包只包含 `install/` 下的公开部署文件：

- 不含 `build` 的七服务 Compose，三池共用同一 `COCKPIT_IMAGE`。
- `release.env` 与 `release.json`：固定版本、标签和 GHCR 镜像。
- 首次私有配置生成器、交互式管理员初始化器、建库/授权/PAT SQL。
- HTTP 默认站和 Cockpit Nginx location、图片能力示例。
- `migrations.txt`：由同一提交生成的监控迁移文件名清单；真正的迁移 SQL 随应用镜像分发。

安装包不包含应用源码、构建 override、`.env`、真实账号、密钥、数据或部署日志。部署密钥在目标服务器首次生成，不从构建环境打包带出。

`v0.4.1` 随附插件 `0.4.2`：插件直接调用 New API 本人账户和令牌接口，不依赖 Cockpit 或账户 MCP；KEY 改组沿用官方 PUT，不构建、发布或升级 New API 镜像。

`v0.4.0` 是首次同时提供全量安装包、聊天、账户 MCP 和 VS Code 插件的正式版本。`v0.3.0` 的既有附件不包含这些组件，不能补传新版安装包来伪装旧版本兼容。

## 使用预构建镜像

- **全量安装：** 按[发布镜像部署](deployment.md)选择正式版本，校验安装包，直接 `pull` / `up --no-build`。
- **接入已有 New API：** 按[已有系统接入](existing-installation.md)下载和校验散文件，填写同版本 `IMAGE_TAG` 后拉取镜像。
- **升级：** 保留原配置、密钥、instance ID 和数据，按[升级与回退](upgrading.md)核对差异；不自动清库或重建账号。

`IMAGE_TAG=X.Y.Z` 用于已有系统接入模板；完整安装使用安装包生成的 `COCKPIT_IMAGE=ghcr.io/ilove323/new-api-cockpit:X.Y.Z`。两套模板和参数不混用。
