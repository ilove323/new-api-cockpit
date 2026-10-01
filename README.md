<div align="center">

# New API Statistics

**为 New API 提供用量统计、成本分析、余额报警与管理员配额操作**

[![CI](https://github.com/ilove323/new-api-statistics/actions/workflows/ci.yml/badge.svg)](https://github.com/ilove323/new-api-statistics/actions/workflows/ci.yml)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.12%2B-blue)](pyproject.toml)
[![Requires New API](https://img.shields.io/badge/requires-New%20API-green)](https://github.com/QuantumNous/new-api)

[快速开始](#快速开始) · [主要特性](#主要特性) · [部署要求](#部署要求) · [文档](#文档) · [帮助与贡献](#帮助与贡献)

</div>

## 项目说明

New API Statistics 是配合 [QuantumNous/new-api](https://github.com/QuantumNous/new-api)
使用的自托管统计工具，提供用户与模型用量分析、Excel 报表、预算监控及消息通知。

> [!IMPORTANT]
> **本项目必须依赖已部署的 New API，不能脱离 New API 独立使用。**
> 当前仅支持 New API 的 **PostgreSQL** 数据库，直接读取其用户、消费日志和站点配置。
> 登录使用 New API 原有的管理员账号密码。本项目不提供模型网关或独立用户系统。

应用采用 Flask + PostgreSQL，作为独立容器与 New API 部署在同一 Docker 网络，
由 Nginx 将 `/statistics/` 与 `/quota/` 转发到统计应用。对 New API 数据库只执行查询；
配额增减另由 New API 官方管理接口执行，不直接写其用户表。
预算、月度归档、报警配置及定时配额规则与执行记录保存在单独的监控库。

## 界面预览

![New API Statistics 用量统计界面](docs/assets/dashboard.png)

截图中的站点、用户、模型及金额均为虚构演示数据。

## 主要特性

| 功能 | 说明 |
| --- | --- |
| 用量分析 | 查看用户、模型的输入、输出、缓存读取与缓存写入 Token；按请求发生时的价格归并，能匹配当前价格档位则显示档位，其他显示 `-`；明细表支持用户、模型、令牌、分组多选筛选及列选择 |
| 消费排名 | 模型消费、用户 Token 用量、用户消费标签页切换 |
| 时间筛选 | 精确到秒，支持上个月、本月、近 30 天、近 7 天和近 1 天 |
| Excel 导出 | 多工作表汇总，数字单元格与 SUM 合计公式 |
| 管理员认证 | 复用 New API 管理员账号，读取原有权限和密码哈希 |
| 余额监控 | “全部”、当前渠道标签和“未分组”独立设额度与报警；逐渠道归档，历史按当前归属汇总 |
| 定时检查 | 每天北京时间 10:00 检查，也可通过铃铛或 API 手动触发 |
| 通知渠道 | 所有账本共用飞书企业自建应用或钉钉 Webhook 通知配置 |
| 报警 API | 实时余额与报警检查，使用 New API 管理员 PAT Bearer 认证 |
| 用户配额 | 独立 `/quota/` 页面列出用户组、状态和配额，默认只显示启用用户；支持按组选择、预览和每组 5 人并发增减，无选择人数上限，显示逐人结果；通过 New API 官方增减接口执行，不直接写用户表 |
| 定时配额 | `/quota/` 齿轮内按用户组设置每日、每周、每月零点的每人增减额度；主程序内置定时器、逐用户执行记录，不自动补发或重试不明确的请求 |

Token 缓存语义取决于上游日志。部分报表数值涉及数学折算，
请先阅读[统计口径](docs/calculation.md)。应用不会修改 New API 原始日志和实际消费金额。
历史单价依赖消费日志保存的计费快照；缺失或无法安全解析时不借用当前价格。

## 部署要求

| 依赖 | 要求 |
| --- | --- |
| New API | 必须已部署，数据库字段符合[兼容范围](docs/compatibility.md) |
| PostgreSQL | New API 原库及只读查询账号；余额监控另建独立库 |
| Docker | Docker Engine 与 Docker Compose，共用 New API 的现有网络 |
| Nginx | 推荐复用现有 HTTPS 站点，转发 `/statistics/` 与 `/quota/` |
| 登录账号 | 有效的 New API 管理员账号 |

SQLite 和 MySQL 后端目前不支持。不同 New API fork 的字段、配额单位和缓存语义
可能不同，接入前应核对兼容文档。

## 快速开始

### 1. 获取项目

```bash
git clone https://github.com/ilove323/new-api-statistics.git
cd new-api-statistics
cp .env.example .env
```

### 2. 配置数据库与网络

按[部署文档](docs/deployment.md)填写 `.env` 中的 New API PostgreSQL 只读连接参数、
现有 Docker 网络名称和端口。

需要余额报警或定时配额时，先按[监控库初始化文档](docs/monitoring.md)执行建库 SQL，
再配置 `MONITOR_DATABASE_URL`；启用通知渠道还需配置 `NOTIFICATION_ENCRYPTION_KEY`。

### 3. 启动服务

```bash
chmod 600 .env
docker compose up -d --build
docker compose ps
```

配置监控库后，容器启动入口会先执行幂等增量迁移；正常页面请求不执行迁移。
旧、新定时调度器不能混用，更新前请阅读[升级说明](docs/upgrading.md)。

配置 `MONITOR_DATABASE_URL` 后，同一个 `statistics` 容器会自动运行余额和配额定时器，
不需要单独启动 worker 或启用 Compose profile。余额仍每天北京时间 10:00 检查；
配额在 `/quota/` 右上角齿轮里配置，未创建并启用规则时不会修改用户额度。
规则使用执行管理员在 New API 中已有的 PAT，无需在 `.env` 中配置 PAT。
定时器会随服务重启自动恢复，无需保持浏览器页面打开。配置了监控库的部署可通过
`/healthz` 确认两个内置定时器都已就绪，见[运行状态检查](docs/deployment.md#运行状态检查)。

**单容器调度属于当前未发布改动**。请用当前源码构建，或使用包含此功能的匹配发布镜像；
已发布的 `0.1.3` 镜像仍采用旧 worker 架构，不能只套用新 Compose 模板。
旧部署切换前必须停止全部旧后台容器，具体见[升级说明](docs/upgrading.md)。

### 4. 配置入口

将 [nginx.conf.example](nginx.conf.example) 中的 location 加入现有 HTTPS 站点，
检查配置并重载 Nginx，然后访问：

```text
https://<你的域名>/statistics/
https://<你的域名>/quota/
```

使用 **New API 管理员账号密码**登录。

没有 Nginx 时，可按[直接端口访问说明](docs/deployment.md#无-nginx-直接访问)配置宿主机端口。
镜像部署应下载与目标版本匹配的发布配置文件，并设置对应 `IMAGE_TAG`；
当前未发布的单容器模板不能配旧 `0.1.3` 镜像，详情见[发版说明](docs/releasing.md)。

## 文档

| 主题 | 内容 |
| --- | --- |
| [部署](docs/deployment.md) | 环境变量、网络、Nginx、直接端口访问及认证 |
| [统计口径](docs/calculation.md) | 缓存包含关系、金额、倍率及数学折算 |
| [兼容范围](docs/compatibility.md) | New API 数据库字段与运行环境 |
| [余额监控](docs/monitoring.md) | 建库 SQL、额度设置、归档和报警规则 |
| [用户配额](docs/quota.md) | 手工批量增减、定时规则、执行记录、权限及失败处理 |
| [通知渠道](docs/notifications.md) | 飞书与钉钉配置及通知行为 |
| [报警 API](docs/api.md) | 认证方式、请求示例与返回值 |
| [升级与备份](docs/upgrading.md) | 数据库迁移、备份和回退 |
| [发版](docs/releasing.md) | GitHub Actions、版本标签及 GHCR 镜像 |

## 帮助与贡献

维护者与当前贡献者：[@ilove323](https://github.com/ilove323)。

- 问题反馈与功能建议：[GitHub Issues](https://github.com/ilove323/new-api-statistics/issues)
- 开发与贡献：[CONTRIBUTING.md](CONTRIBUTING.md)
- 版本变化：[CHANGELOG.md](CHANGELOG.md)
- 安全问题：[SECURITY.md](SECURITY.md)

反馈时请提供版本、复现步骤和脱敏日志，不要提交管理员密码、API Key 或客户数据。

## 致谢与许可证

感谢 [QuantumNous/new-api](https://github.com/QuantumNous/new-api)。
本项目是依赖 New API 的第三方统计工具，非 New API 官方组件。

Copyright 2026 ilove323。采用 [Apache-2.0](LICENSE)，允许商业使用。
New API 及其他依赖各自遵循其许可证。
第三方图标许可见 [NOTICE](NOTICE) 和 [Lucide 许可](src/new_api_statistics/static/LUCIDE-LICENSE)。
