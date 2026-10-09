# 部署

以下命令均在项目根目录执行。

## 部署条件

- 已运行的 New API、PostgreSQL 和 Docker Compose。
- 统计容器加入 New API 的现有 Docker 网络，不新建或替换其数据库。
- 数据库账号具有 SELECT `logs`、`options`、`users`、`channels`、`tokens` 的权限，建议使用专用查询账号，不授予业务表写权限。
- 网页通过同源 HTTPS 反向代理访问。下文以宿主机 Nginx 为例，也可使用容器化 Nginx 或同等代理。

## 配置与启动

根据 [.env.example](../.env.example) 创建 `.env`，将权限设为 `600`，填写实际数据库连接参数：

| 参数 | 说明 |
| --- | --- |
| `PGHOST` | PostgreSQL 在共享 Docker 网络中的服务名或网络别名 |
| `PGPORT` | PostgreSQL 容器内部端口，通常为 `5432` |
| `PGDATABASE` | New API 使用的数据库名 |
| `PGUSER` / `PGPASSWORD` | 源库查询账号及密码；用户管理还可授予受限 PAT 补建函数 EXECUTE，不能授予业务表 UPDATE |
| `BIND_HOST` | 宿主机监听地址，默认 `127.0.0.1`；仅跨主机代理或可信内网调用需要调整 |
| `PORT` | 暴露到宿主机的端口，默认 `8091` |
| `PYTHON_IMAGE` | 构建使用的 Python 镜像，默认 `python:3.12-slim` |
| `PIP_INDEX_URL` | 可选 Python 包索引地址 |
| `NEW_API_NETWORK` | 已有 New API Docker 网络名，默认 `new-api-network` |
| `COCKPIT_COOKIE_SECURE` | 默认 `true`，会话 Cookie 仅通过 HTTPS 发送；仅可信 HTTP 开发/内网可显式设为 `false` |
| `NEW_API_INTERNAL_URL` | New API 在共享 Docker 网络中的内部地址，默认 `http://new-api:3000`；用于官方会话验证、用户/KEY 管理以及手工和定时用户配额增减 |
| `MONITOR_DATABASE_URL` | 独立监控库连接串；保存预算、月度归档、报警、通知配置、定时规则与用户/KEY/配额操作记录 |
| `NOTIFICATION_ENCRYPTION_KEY` | 飞书 Secret、钉钉 Webhook/加签密钥、邮件密码的 Fernet 加密密钥；生成一次后必须持久保存 |

用户配额操作还要求当前登录管理员在 New API 中已有 PAT，并且统计容器能通过
`NEW_API_INTERNAL_URL` 访问 New API 的管理接口。此地址应使用共享 Docker 网络中的
内部服务名，不要填公网地址；后端调用 New API 所需 PAT 每次从源库读取，外部接入 PAT 由调用方自行保管，不放到项目配置。

设置 `NEW_API_NETWORK` 为现有 New API 网络的实际名称，也可在本机专用的
`docker-compose.override.yml` 中覆盖 `networks.new-api.name`，无需改动通用 Compose 文件。
可通过 `docker network ls` 检查；Compose 中的名称只是默认配置，不保证与其他部署相同。
环境专用覆盖文件不应分发，已加入 Git 和 Docker 忽略规则。
`PGHOST` 不能填写 `127.0.0.1`，因为它在统计容器内指向统计容器自身。
不要为统计应用公开数据库端口。`.env` 不应提交、分发或打入镜像。

```bash
chmod 600 .env
docker compose up -d --build statistics
docker compose ps
docker compose logs --tail 100 statistics
```

配置独立监控库后，Docker entrypoint 会先执行增量迁移，失败则不启动该容器进程；
正常请求仅检查版本，不尝试建表。无需清空数据库。备份、更新与非 Docker 启动入口见[升级文档](upgrading.md)。

应用默认仅绑定宿主机 `127.0.0.1:8091`，供反向代理和探活使用。网页入口按“配合 Nginx”配置；
可信内网中的 API 直连见“无 Nginx 直接访问”。

## 运行状态检查

同一个 `statistics` 容器包含网页、每天北京时间 10:00 的余额检查和按规则执行的零点配额任务。
配置 `MONITOR_DATABASE_URL` 后，两个定时器随应用启动，浏览器关闭后仍会运行。
没有启用的配额规则时，不会自行增减用户额度。暂停全部应用会同时暂停两个定时器。

在应用宿主机执行（非默认 `PORT` 请替换 `8091`）：

```bash
docker compose ps statistics
curl --fail-with-body http://127.0.0.1:8091/healthz
```

配置监控库并正常就绪时返回 HTTP `200`：

```json
{"status":"ok","timers":{"configured":true,"ready":true,"threads_running":true,"leaders_active":true}}
```

`threads_running` 表示当前网页进程的两个调度线程正在运行；`leaders_active` 表示监控库中
余额和配额两种领导锁均有持有者。多个网页进程可以待命，但每种定时任务只有一个领导者执行。
调度线程停止或领导锁尚未就绪时返回 HTTP `503`；先检查容器日志、监控库连接及迁移版本，
健康检查只检查线程和锁，不拉取消费、不发报警、不修改用户额度。
该结果表示调度就绪，不等于某条规则已成功执行；具体执行结果在独立 `/cockpit/operations/` 页面查看。

未配置可选监控库时返回 `{"status":"ok","timers":{"configured":false,"ready":true}}`，
提供基础统计和用户/KEY 列表查询；所有用户、KEY、手工配额写操作需要监控库保存操作记录。不启动余额或配额定时器。
`/healthz` 用于容器或宿主机探活；现有 Nginx 无需新增公开转发路径。

## 配合 Nginx

把 [nginx.conf.example](../nginx.conf.example) 的 `/cockpit` 与 `/cockpit/` location 加入现有 HTTPS `server` 块，
New API 的 `/`、`/api/` 和 `/sign-in` 等路由继续转发到 New API。
四个页面、统一的 `/cockpit/api/` 业务接口和静态资源均由 Cockpit 处理。
`proxy_pass` 不加末尾斜杠，必须把完整路径前缀转发到应用。
使用 `Host $http_host` 保留原域名和端口，同源管理请求校验依赖此信息。
侧边栏使用相对路径，始终保留浏览器当前协议、域名与端口。

本项目的页面、API 和静态资源统一使用 `/cockpit/` 前缀。
对外余额与报警接口分别为 `/cockpit/api/statistics/balance` 和 `/cockpit/api/statistics/alert`。
API 调用方使用完整 `/cockpit/api/statistics/` 路径。

```bash
nginx -t
nginx -s reload
```

随后访问 `https://<你的域名>/cockpit/statistics/`、`https://<你的域名>/cockpit/users/`、`https://<你的域名>/cockpit/keys/` 或 `https://<你的域名>/cockpit/operations/`。如果修改了 `PORT`，应同步修改
[nginx.conf.example](../nginx.conf.example) 中的 upstream 端口。HTTP 请求应跳转 HTTPS，避免明文传输登录凭据。

如果 Nginx 也在容器内，需要将其加入共享 Docker 网络，并将 upstream 改为
`http://statistics:8000`（或为统计容器设置唯一网络别名后使用该别名）。
此时不能使用 `127.0.0.1:8091`，因为那会指向 Nginx 容器自身。

## 无 Nginx 直接访问

直连应用端口可用于宿主机 `/healthz` 探活或可信内网中的反向代理转发。
网页登录还需要同源 New API `/api/` 和 `/sign-in` 路由，单独开放应用端口不能提供完整的登录入口。
正式网页使用请按上节配置 HTTPS 反向代理。

确需跨主机直连时，在 `.env` 中设置：

```ini
BIND_HOST=0.0.0.0
PORT=8091
```

重新创建容器，使端口映射生效（镜像部署请使用对应的 Compose 文件）：

```bash
docker compose up -d --build --force-recreate statistics
docker compose ps
```

仅在防火墙或安全组中允许可信代理来源，避免公网开放 `8091`。
业务 API 仍通过 HTTPS 入口调用，不通过明文应用端口传递管理员 PAT：

```bash
curl --fail-with-body 'https://<你的域名>/cockpit/api/statistics/balance' \
  -H "Authorization: Bearer $ADMIN_PAT"
```

脚本通过管理员 PAT Bearer 调用业务 API，页面仍使用浏览器会话；余额、报警只接受 PAT。所有携带凭据的连接使用 HTTPS。
只有在已配齐同源路由的可信 HTTP 开发环境中，才显式设置 `COCKPIT_COOKIE_SECURE=false`；
它只控制 Cookie 的 Secure 属性，不改变同源要求。

## 登录与权限

网页使用 `/cockpit/login`，先尝试复用同源 New API 登录；没有有效会话才显示账号登录。
密码和二次验证码直接交给官方接口，服务端每次经 `/api/user/self` 校验会话及 `role >= 10`、`status = 1`。
New API 撤销会话、账号禁用或降权后，Cockpit 也拒绝访问；上游暂不可用返回 503，恢复后可重试。
浏览器必须能访问同源 New API `/api/` 和官方 `/sign-in` 页面；内部地址仅用于后台校验，不发送给浏览器。
无需新增表、迁移或数据库权限；不需要 New API 的 SESSION_SECRET，也不配置 Redis。详见[登录与会话](authentication.md)。
统计查询不会修改 New API 数据；管理员在 `/cockpit/users/`
确认配额增减时，服务才会通过 New API 官方管理接口修改所选用户额度，详情见
[用户配额](quota.md)。启用定时配额规则后，主程序内置定时器会在对应周期按执行管理员权限调用同一接口。
网页访问不启动任务，应用启动也不会无条件发额度；仅执行符合既有到期窗口的启用规则。

## 用户与令牌管理授权

用户、KEY 和配额写操作均需要监控库用于审计。New API 源库须能 SELECT `tokens`；
缺失用户 PAT 的补建仅授权受限函数，不授予业务表 UPDATE，安装 SQL 见[用户管理](users.md#安装补建函数与最小权限)。
业务页面经过管理员会话认证；`/cockpit/api/` 下的业务接口也接受管理员 PAT，权限与审计不变。
登录页和无敏感信息的静态资源公开。接口地址、确认头及示例见 [API 参考](api.md)。
Nginx 统一转发 `/cockpit/`。数据库仅在内部网络开放。

## 数据库初始化与迁移

建库、应用迁移和源库函数的执行位置如下：

| SQL | 位置 | 执行方式 |
|---|---|---|
| 监控建库 | [余额监控](monitoring.md#独立数据库) | PostgreSQL 管理员首次创建独立库和账号 |
| 应用表结构 | [migrations](../src/new_api_cockpit/migrations/) 的 `001`～`011` | 容器启动自动按编号执行尚未登记的文件 |
| 缺失 PAT 补建函数 | [source_pat_function.sql](../sql/source_pat_function.sql) | New API 原库表所有者单独安装，再向查询角色授权 EXECUTE |

`source_pat_function.sql` 安装源库 PAT 补建函数：目标用户没有 PAT 时补建，已有 PAT 原样返回。
函数仅允许这一项写入，用户余额、密码和 KEY 由官方 API 修改。
脚本位于源码仓库根目录的 `sql/`，Docker 镜像中为 `/app/sql/source_pat_function.sql`。
函数授权与安全边界见[用户管理](users.md#安装补建函数与最小权限)。

监控迁移保存在 Python 应用包中，随镜像和安装包分发。
`schema_migrations.version` 记录文件名：新库执行完整链，已有库只执行缺失项。
不能将已发布迁移合并进 `001` 或重编号；否则已登记旧文件的安装不会获得必要改动。
新增表结构请追加迁移，升级前备份并停止应用，见[升级](upgrading.md#数据库迁移)。
