# 发版与镜像

维护者：ilove323。版本唯一来源为 `pyproject.toml`。
依赖范围使用 `requirements.txt`，不生成依赖锁文件。
历史发布说明统一放在 [GitHub Releases](https://github.com/ilove323/new-api-statistics/releases)，
仓库文档只描述当前代码与操作方法。

## 仓库权限

GitHub Actions 需获准运行，发布任务通过 `GITHUB_TOKEN` 获得 `packages:write` 和
`contents:write`。GHCR 包需设为 public 才能匿名拉取，公开仓库不保证包自动公开。

建议 `main` 禁止强制推送，并要求 CI 通过；当前只有一个维护者，
不强制要求另一人的 PR 审批。权限、分支保护与私密漏洞报告在 GitHub 设置页启用。

## 发布流程

1. 确认当前源码、文档、Compose 与数据库迁移一致，更新 `pyproject.toml` 正式版本。
2. 通过 PR 合并到 `main` 并通过 CI；准备发布说明，列明功能、升级要求和安全边界。
3. 创建与版本完全匹配的 `vX.Y.Z` 标签并推送该标签。
4. `release.yml` 复用 CI，校验版本与标签后构建 AMD64/ARM64 镜像，发布到 GHCR。
5. 工作流创建 GitHub Release，附带配套 Compose、环境变量/Nginx 示例、校验和及自动生成的变更列表。
6. 在 GitHub Release 正文补充必要的升级、数据库与配置说明，不在当前文档目录积累按版本命名的历史文件。

目前只支持 `vX.Y.Z` 正式标签，预发布标签会被拒绝。
镜像标签包括精确版本与 `latest`；生产部署应固定精确版本或 digest。
工作流只发布制品，不部署服务器。

开发提交推送到 `develop` 不触发普通 push CI 或正式发版；PR、`main` push、
手动运行和发版流程按 `.github/workflows/` 中的触发条件执行。

## 使用预构建镜像

从目标 Release 下载 `compose.release.yml`、`.env.example`、`nginx.conf.example`，
配置数据库与现有网络，并按[监控库初始化](monitoring.md)创建独立监控库。

```ini
IMAGE_TAG=<与配置模板对应的正式版本>
```

```bash
docker compose -f compose.release.yml up -d statistics
```

当前架构是一个 `statistics` 容器，余额和配额定时器随应用启动。
镜像、配置和操作文档必须匹配；若当前分支的实现尚未包含在正式镜像中，应按[部署文档](deployment.md)从源码构建，
不要将当前分支的 Compose 与不含对应实现的发布镜像混用。
升级与回退遵循[升级说明](upgrading.md)，不会通过发版自动清空监控库。
