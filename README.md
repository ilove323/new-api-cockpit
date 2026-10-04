<div align="center">

# new-api-cockpit

**扩展 New API 的用量统计、账本监控、用户、令牌与配额管理**

[![CI](https://github.com/ilove323/new-api-cockpit/actions/workflows/ci.yml/badge.svg)](https://github.com/ilove323/new-api-cockpit/actions/workflows/ci.yml)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.12%2B-blue)](pyproject.toml)
[![Requires New API](https://img.shields.io/badge/requires-New%20API-green)](https://github.com/QuantumNous/new-api)

[快速开始](#快速开始) · [主要特性](#主要特性) · [部署要求](#部署要求) · [文档](#文档) · [帮助与贡献](#帮助与贡献)

</div>

## 项目说明

new-api-cockpit 是配合 [QuantumNous/new-api](https://github.com/QuantumNous/new-api)
使用的自托管扩展管理控制台，提供用量与成本分析、预算报警、用户与 KEY 管理及定时配额。

> [!IMPORTANT]
> **本项目必须依赖已部署的 New API，不能脱离 New API 独立使用。**
> 当前仅支持 New API 的 **PostgreSQL** 数据库，直接读取其用户、消费日志和站点配置。
> 登录使用 New API 原有的管理员账号密码。本项目不提供模型网关或独立用户系统。

应用采用 Flask + PostgreSQL，作为独立容器与 New API 部署在同一 Docker 网络，
由 Nginx 将统一 `/cockpit/` 前缀转发到应用。用量统计、用户管理、令牌管理和操作记录四个页面共用侧边栏。New API 普通查询只读；
用户管理仅在缺失 PAT 时通过受限函数补建，
用户资料、KEY 和配额修改均通过 New API 官方接口执行，不直接写对应业务字段。
预算、月度归档、报警配置及定时配额规则与执行记录保存在单独的监控库。

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
| 用户管理 | `/cockpit/users/` 合并用户资料与配额，显示用户组、状态、余额、KEY 数量与备注；最后一列支持编辑、重置密码、启停和删除；按组选择用户，预览后每组 5 人并发原子增减额度 |
| 令牌管理 | `/cockpit/keys/` 按用户合并第一列，每个 KEY 一行；支持脱敏查询、完整 KEY 精确定位用户、KEY 分组/限额/启停及每组 5 个批量改组；所属用户 PAT 缺失时受限补建 |
| 定时配额 | 用户管理齿轮内按用户组设置每日、每周、每月零点的每人增减额度；内置定时器，不自动补发或重试不明确请求 |
| 操作记录 | `/cockpit/operations/` 独立分页查询用户、KEY、手工配额、规则修改及定时执行记录，保留逐条结果与实际管理员，不保存凭据 |

Token 缓存语义取决于上游日志。部分报表数值涉及数学折算，
请先阅读[统计口径](docs/calculation.md)。应用不会修改 New API 原始日志和实际消费金额。
历史单价依赖消费日志保存的计费快照；缺失或无法安全解析时不借用当前价格。

## 部署要求

| 依赖 | 要求 |
| --- | --- |
| New API | 必须已部署，数据库字段符合[兼容范围](docs/compatibility.md) |
| PostgreSQL | New API 原库及只读查询账号；余额监控另建独立库 |
| Docker | Docker Engine 与 Docker Compose，共用 New API 的现有网络 |
| Nginx | 推荐复用现有 HTTPS 站点，转发统一 `/cockpit/` 前缀 |
| 登录账号 | 有效的 New API 管理员账号 |

SQLite 和 MySQL 后端目前不支持。不同 New API fork 的字段、配额单位和缓存语义
可能不同，接入前应核对兼容文档。

## 快速开始

### 1. 获取项目

```bash
git clone https://github.com/ilove323/new-api-cockpit.git new-api-cockpit
cd new-api-cockpit
cp .env.example .env
```

### 2. 配置数据库与网络

按[部署文档](docs/deployment.md)填写 `.env` 中的 New API PostgreSQL 只读连接参数、
现有 Docker 网络名称和端口。

需要余额报警、用户/KEY/配额写操作或定时配额时，先按[监控库初始化文档](docs/monitoring.md)执行建库 SQL，
再配置 `MONITOR_DATABASE_URL`；启用通知渠道还需配置 `NOTIFICATION_ENCRYPTION_KEY`。

### 3. 启动服务

```bash
chmod 600 .env
docker compose up -d --build
docker compose ps
```

配置监控库后，容器启动入口会先执行幂等增量迁移；正常页面请求不执行迁移。
已有部署更新前请阅读[升级说明](docs/upgrading.md)。

配置 `MONITOR_DATABASE_URL` 后，同一个 `statistics` 容器会自动运行余额和配额定时器，
无需其他应用容器。余额仍每天北京时间 10:00 检查；
配额在 `/cockpit/users/` 右上角齿轮里配置，未创建并启用规则时不会修改用户额度。
规则使用执行管理员在 New API 中已有的 PAT，无需在 `.env` 中配置 PAT。
定时器会随服务重启自动恢复，无需保持浏览器页面打开。配置了监控库的部署可通过
`/healthz` 确认两个内置定时器都已就绪，见[运行状态检查](docs/deployment.md#运行状态检查)。

本文对应当前仓库源码。预构建镜像必须包含相同实现并使用配套配置；
开发分支请从源码构建。
运行拓扑、模块和数据库职责见[架构说明](docs/architecture.md)。

### 4. 配置入口

将 [nginx.conf.example](nginx.conf.example) 中的 location 加入现有 HTTPS 站点，
检查配置并重载 Nginx，然后访问：

```text
https://<你的域名>/cockpit/statistics/
https://<你的域名>/cockpit/users/
https://<你的域名>/cockpit/keys/
https://<你的域名>/cockpit/operations/
```

使用 **New API 管理员账号密码**登录。

没有 Nginx 时，可按[直接端口访问说明](docs/deployment.md#无-nginx-直接访问)配置宿主机端口。
镜像部署应下载与目标版本匹配的发布配置文件，并设置对应 `IMAGE_TAG`；
不要混用不同实现的镜像与当前配置，详情见[发版说明](docs/releasing.md)。

## 文档

| 主题 | 内容 |
| --- | --- |
| [当前架构](docs/architecture.md) | 单容器运行、模块职责、数据流、数据库及权限边界 |
| [界面规范](docs/ui.md) | 公共样式、字体图标、按钮位置和响应式布局 |
| [部署](docs/deployment.md) | 环境变量、网络、Nginx、直接端口访问及认证 |
| [统计口径](docs/calculation.md) | 缓存包含关系、金额、倍率及数学折算 |
| [兼容范围](docs/compatibility.md) | New API 数据库字段与运行环境 |
| [余额监控](docs/monitoring.md) | 建库 SQL、额度设置、归档和报警规则 |
| [用户配额](docs/quota.md) | 手工批量增减、定时规则、执行记录、权限及失败处理 |
| [用户与令牌管理](docs/users.md) | 独立页面、所属用户 PAT、受限函数安装、批量改组与统一操作记录 |
| [通知渠道](docs/notifications.md) | 飞书与钉钉配置及通知行为 |
| [报警 API](docs/api.md) | 认证方式、请求示例与返回值 |
| [升级与备份](docs/upgrading.md) | 数据库迁移、备份和回退 |
| [性能机制](docs/performance.md) | 渠道同步、当月汇总复用、详情快照和验证方法 |
| [发版](docs/releasing.md) | GitHub Actions、版本标签及 GHCR 镜像 |

## 帮助与贡献

维护者与当前贡献者：[@ilove323](https://github.com/ilove323)。

- 问题反馈与功能建议：[GitHub Issues](https://github.com/ilove323/new-api-cockpit/issues)
- 开发与贡献：[CONTRIBUTING.md](CONTRIBUTING.md)
- 版本变化：[GitHub Releases](https://github.com/ilove323/new-api-cockpit/releases)
- 安全问题：[SECURITY.md](SECURITY.md)

反馈时请提供版本、复现步骤和脱敏日志，不要提交管理员密码、API Key 或客户数据。

## 致谢与许可证

感谢 [QuantumNous/new-api](https://github.com/QuantumNous/new-api)。
本项目是依赖 New API 的第三方扩展管理控制台，非 New API 官方组件。

Copyright 2026 ilove323。采用 [Apache-2.0](LICENSE)，允许商业使用。
New API 及其他依赖各自遵循其许可证。
第三方字体与图标许可见 [NOTICE](NOTICE)、[Lucide 许可](src/new_api_cockpit/static/LUCIDE-LICENSE)和 [Public Sans 许可](src/new_api_cockpit/static/fonts/OFL-LICENSE.txt)。
