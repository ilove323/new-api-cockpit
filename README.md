<div align="center">

# new-api-cockpit

**New API 的用量统计与扩展管理后台**

[![CI](https://github.com/ilove323/new-api-cockpit/actions/workflows/ci.yml/badge.svg)](https://github.com/ilove323/new-api-cockpit/actions/workflows/ci.yml)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.12%2B-blue)](pyproject.toml)
[![Requires New API](https://img.shields.io/badge/requires-New%20API-green)](https://github.com/QuantumNous/new-api)

[功能](#功能) · [部署](#部署) · [界面预览](#界面预览) · [文档](#文档)

</div>

给 [New API](https://github.com/QuantumNous/new-api) 补一套统计和管理页面：查用量、对账、设余额告警，
以及批量管理用户、令牌和额度。页面放在现有站点的 `/cockpit/` 下，登录沿用 New API 管理员账号；
在同站点登录过 New API，可以直接进入。

本项目需要已部署的 New API，目前支持 PostgreSQL 后端。应用用 Flask 编写，单独运行一个容器，
和 New API 共用 Docker 网络，通过同一 HTTPS 入口访问。

## 功能

页面通过侧边栏切换：

| 页面 | 地址 | 可以做什么 |
| --- | --- | --- |
| 用量统计 | `/cockpit/statistics/` | 按用户、模型、令牌和分组筛选，查看 Token、消费排名和价格明细，导出 Excel；从明细跳到相同时间段的 New API 日志 |
| 用户管理 | `/cockpit/users/` | 编辑资料、密码、用户组和状态；按组选择用户，预览后批量增减额度；设置每天、每周或每月执行的额度规则 |
| 令牌管理 | `/cockpit/keys/` | 按用户查看 KEY，搜索、查看和复制令牌，修改分组、限额及状态，批量把 KEY 切换到其他分组 |
| 操作记录 | `/cockpit/operations/` | 查看用户、KEY、配额和定时规则的操作，定位目标用户及逐条执行结果 |
| API 文档 | `/cockpit/docs/` | 按流程试调用接口，填写参数、查看响应、复制程序示例，查阅和下载 OpenAPI 描述 |

统计页按渠道标签区分上游账本，也有“全部”和“未分组”。每个账本单独设置预算、报警阈值和监控开关，
共用飞书、钉钉和邮件通知配置，各通知渠道可以独立启停、同时发送。邮件支持 SMTP、STARTTLS、SMTPS，
可使用账号认证或匿名发信，配置方法见[通知渠道](docs/notifications.md)。费用按月、按渠道归档，查看时按渠道当前分组汇总。
每天北京时间 10:00 检查余额，也可以手动检查。

**金额以消费日志为准。** 同一模型按请求发生时的价格分行，匹配当前价格的行显示对应档位。
倍率变化时的缓存 Token 配平属于数学换算，原始日志和实际金额不变。
表格金额显示两位小数，悬浮详情、API 和 Excel 保留原精度。具体见[统计口径](docs/calculation.md)。

用户和 KEY 的修改通过 New API 官方接口执行，每组最多 5 个并发请求。
所有写操作需要监控库保存操作记录；只记录已发起的操作。KEY 管理使用所属用户的 PAT，缺失时通过受限函数补建。

页面使用的业务接口也可以供脚本调用，统一在 `/cockpit/api/` 下，使用已有 New API 管理员 PAT。
统计、用户、令牌和操作记录的地址、参数及调用示例见 [API 参考](docs/api.md)。
余额查询为 `/cockpit/api/statistics/balance`，报警检查为 `/cockpit/api/statistics/alert`；后者会按配置发送通知。
登录后从侧边栏打开 API 文档即可在线调试，使用方法见[交互式文档](docs/interactive-api.md)。
请求发往当前站点，修改和通知需要确认，不自动重试。

## 部署

准备好 New API、PostgreSQL、Docker Compose，以及 Nginx 或同等反向代理。
New API 需要支持 JWT 登录和刷新接口，数据库字段见[兼容范围](docs/compatibility.md)。

### 1. 下载与配置

```bash
git clone https://github.com/ilove323/new-api-cockpit.git
cd new-api-cockpit
cp .env.example .env
chmod 600 .env
```

按[部署文档](docs/deployment.md)填写 `.env`：

- `PG*`：New API 原库的专用查询账号，授予所需表的 SELECT 权限。
- `NEW_API_NETWORK`、`NEW_API_INTERNAL_URL`：现有 Docker 网络和 New API 内部地址。
- `MONITOR_DATABASE_URL`：独立监控库，保存预算、归档、通知配置、定时规则和操作记录。建库方法见[监控库配置](docs/monitoring.md#独立数据库)。留空时提供只读查询。
- `NOTIFICATION_ENCRYPTION_KEY`：保存通知凭据时填写（飞书、钉钉、邮件认证密码），并与监控库一起备份。

需要补建用户 PAT 时，由源库表所有者安装 [source_pat_function.sql](sql/source_pat_function.sql)，
再给查询账号授予函数执行权限，步骤见[用户管理部署](docs/users.md#安装补建函数与最小权限)。
应用账号无需业务表的 UPDATE 权限，PAT 也无需填入 `.env`。

### 2. 启动

```bash
docker compose up -d --build statistics
docker compose ps
curl --fail-with-body http://127.0.0.1:8091/healthz
```

配置监控库后，启动入口自动应用缺失的迁移，余额和配额定时器随应用运行。
配额规则在用户管理页右上角的设置中配置，保存后从下一个周期执行。

上面是源码构建方式。使用发布镜像时，从对应 [Release](https://github.com/ilove323/new-api-cockpit/releases)
下载配置文件，设置 `IMAGE_TAG` 后按[镜像部署说明](docs/releasing.md#使用预构建镜像)启动。
已有安装请先看[升级、备份与回退](docs/upgrading.md)。

### 3. 接入现有站点

把 [nginx.conf.example](nginx.conf.example) 中的 location 加入 New API 的 HTTPS 站点，检查配置并重载。
浏览器访问 `https://<你的域名>/cockpit/`，由侧边栏切换页面。
New API 的 `/api/`、`/sign-in` 和 Cockpit 必须在同一协议、域名和端口下，会话才能共用。

没有有效登录时会打开登录页，密码和二次验证码由 New API 验证。
使用 SSO、通行密钥或人机验证的站点，可从登录页进入官方登录流程，完成后返回。
详细流程见[登录与会话](docs/authentication.md)。

应用端口默认只监听宿主机 `127.0.0.1:8091`，用于反向代理和探活，不应直接公开到公网。

## 界面预览

以下截图使用演示数据。

### 用量统计

![用量统计](docs/assets/statistics.png)

<details>
<summary>用户管理、令牌管理、操作记录、通知设置与登录页</summary>

### 用户管理

![用户管理](docs/assets/users.png)

### 令牌管理

![令牌管理](docs/assets/keys.png)

### 操作记录

![操作记录](docs/assets/operations.png)

### 通知设置

![邮件通知设置](docs/assets/notifications.jpg)

### 管理员登录

![管理员登录页](docs/assets/login.jpg)

</details>

## 文档

- 安装与维护：[部署](docs/deployment.md) · [兼容范围](docs/compatibility.md) · [升级与备份](docs/upgrading.md) · [发版与镜像](docs/releasing.md)
- 统计与告警：[统计口径](docs/calculation.md) · [余额监控](docs/monitoring.md) · [通知渠道](docs/notifications.md)
- 管理功能：[用户与令牌](docs/users.md) · [手工与定时配额](docs/quota.md) · [登录与会话](docs/authentication.md)
- 程序接入：[交互式文档](docs/interactive-api.md) · [API 参考](docs/api.md)（认证、统计、归档、用户、KEY、配额、规则和操作记录）
- 开发：[架构](docs/architecture.md) · [性能机制](docs/performance.md) · [界面规范](docs/ui.md) · [贡献指南](CONTRIBUTING.md)

问题和建议请提交到 [Issues](https://github.com/ilove323/new-api-cockpit/issues)，附上版本、复现步骤和脱敏日志。
安全问题见 [SECURITY.md](SECURITY.md)，请勿公开密码、令牌或客户数据。

## 许可证

维护者：[@ilove323](https://github.com/ilove323)。感谢 [QuantumNous/new-api](https://github.com/QuantumNous/new-api)。
本项目为第三方扩展，采用 [Apache-2.0](LICENSE) 许可证，允许商业使用。
New API 和其他依赖各自遵循其许可证；字体与图标归属见 [NOTICE](NOTICE)、
[Lucide 许可](src/new_api_cockpit/static/LUCIDE-LICENSE)、[Public Sans 许可](src/new_api_cockpit/static/fonts/OFL-LICENSE.txt) 和 [Scalar 许可](src/new_api_cockpit/static/SCALAR-LICENSE)。
