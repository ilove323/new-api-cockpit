# 发版

维护者：ilove323。版本唯一来源为 pyproject.toml。
依赖范围继续使用 requirements.txt，不生成依赖锁文件。

## 首次设置

GitHub Actions 需获准运行，发布任务通过 GITHUB_TOKEN 获得 packages:write 和
contents:write。首次发布后检查 GHCR 包的可见性；公开仓库不保证首次创建的包
自动公开。希望用户匿名拉取时，在包设置里将可见性设为 public。

建议 main 禁止强制推送，并要求 CI 通过。当前只有一个维护者，
不强制要求另一个人的 PR 审批。这些仓库设置需维护者在 GitHub 设置页启用。
私密漏洞报告同样需要在 Security 设置中开启。

## 发布步骤

1. 更新 pyproject.toml 版本和 CHANGELOG.md，提交 PR 并通过 CI。
2. 合并到 main 后创建对应标签，如 v0.1.3，并推送标签。
3. release.yml 会复用 CI；版本与标签不一致时拒绝发布。
4. 构建 AMD64/ARM64 镜像，发布到 ghcr.io/ilove323/new-api-statistics。
5. 创建 GitHub Release，附带 Compose、环境变量及 Nginx 示例和校验和。

目前只支持 vX.Y.Z 正式标签，预发布标签会被拒绝。
发布镜像标签包括精确版本和 latest；生产部署固定精确版本或 digest。
工作流发布制品，不自动部署服务器。

## 使用镜像

首次 Release 成功发布后，下载该版本的 compose.release.yml、.env.example、
nginx.conf.example，按部署文档配置 .env 并创建监控库。设置：

```ini
IMAGE_TAG=<与配置模板对应的已发布版本>
```

```bash
docker compose -f compose.release.yml up -d
```

新版本单容器内置余额与配额定时器；配置监控库即可随应用运行，无需另启 worker。
Compose 模板必须与镜像同版：目前单容器改动尚未发布，现有 `0.1.3` 镜像仍是旧架构，
不能把新模板和旧镜像混用。测试当前改动请从源码构建，正式发版后再填对应 `IMAGE_TAG`。
从旧部署迁移前须停止旧后台服务并清理私有 override 的 worker 配置，见[升级说明](upgrading.md)。

Release 说明按 PR 标签分类生成；有数据库或配置变化时，
维护者必须补充升级说明，不能只依赖自动生成的提交列表。
