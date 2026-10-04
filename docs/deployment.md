# 部署

以下命令均在项目根目录执行。

## 部署条件

- 已运行的 New API、PostgreSQL 和 Docker Compose；Nginx 为可选。
- 统计容器加入 New API 的现有 Docker 网络，不新建或替换其数据库。
- 数据库账号具有 SELECT `logs`、`options`、`users`、`channels`、`tokens` 的权限，建议使用专用查询账号，不授予业务表写权限。
- 使用 Nginx 时默认其运行在同一宿主机上；容器化 Nginx 和无 Nginx 的访问方式见下文。

## 配置与启动

根据 [.env.example](../.env.example) 创建 `.env`，将权限设为 `600`，填写实际数据库连接参数：

| 参数 | 说明 |
| --- | --- |
| `PGHOST` | PostgreSQL 在共享 Docker 网络中的服务名或网络别名 |
| `PGPORT` | PostgreSQL 容器内部端口，通常为 `5432` |
| `PGDATABASE` | New API 使用的数据库名 |
| `PGUSER` / `PGPASSWORD` | 源库查询账号及密码；用户管理还可授予受限 PAT 补建函数 EXECUTE，不能授予业务表 UPDATE |
| `BIND_HOST` | 宿主机监听地址；配合 Nginx 使用 `127.0.0.1`，无 Nginx 直接访问使用 `0.0.0.0` |
| `PORT` | 暴露到宿主机的端口，默认 `8091` |
| `PYTHON_IMAGE` | 构建使用的 Python 镜像，默认 `python:3.12-slim` |
| `PIP_INDEX_URL` | 可选 Python 包索引地址 |
| `NEW_API_NETWORK` | 已有 New API Docker 网络名，默认 `new-api-network` |
| `NEW_API_INTERNAL_URL` | New API 在共享 Docker 网络中的内部地址，默认 `http://new-api:3000`；用于用户/KEY 管理以及手工和定时用户配额增减 |
| `MONITOR_DATABASE_URL` | 独立监控库连接串；保存预算、月度归档、报警、通知配置、定时规则与用户/KEY/配额操作记录 |
| `NOTIFICATION_ENCRYPTION_KEY` | 通知渠道 Secret 的 Fernet 加密密钥；生成一次后必须持久保存 |

用户配额操作还要求当前登录管理员在 New API 中已有 PAT，并且统计容器能通过
`NEW_API_INTERNAL_URL` 访问 New API 的管理接口。此地址应使用共享 Docker 网络中的
内部服务名，不要填公网地址；PAT 只从 New API 数据库读取并在服务端使用。

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

应用默认仅绑定宿主机 `127.0.0.1:8091`。使用 Nginx 时继续阅读下一节；不使用 Nginx 时按“无 Nginx 直接访问”配置外部监听。

## 运行状态检查

同一个 `statistics` 容器包含网页、每天北京时间 10:00 的余额检查和按规则执行的零点配额任务。
配置 `MONITOR_DATABASE_URL` 后，两个定时器随应用启动；无需另起 worker，也不依赖浏览器保持打开。
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
不要额外启动独立调度器。健康检查只检查线程和锁，不拉取消费、不发报警、不修改用户额度。
该结果表示调度就绪，不等于某条规则已成功执行；具体执行结果在独立 `/cockpit/operations/` 页面查看。

未配置可选监控库时返回 `{"status":"ok","timers":{"configured":false,"ready":true}}`，
提供基础统计和用户/KEY 列表查询；所有用户、KEY、手工配额写操作需要监控库保存操作记录。不启动余额或配额定时器。
`/healthz` 用于容器或宿主机探活；现有 Nginx 无需新增公开转发路径。

## 配合 Nginx

把 [nginx.conf.example](../nginx.conf.example) 的 `/cockpit` 与 `/cockpit/` location 加入现有 HTTPS `server` 块，
保留 New API 原来的 `/` 配置。四个页面、各自 API 和静态资源均在 `/cockpit/` 下。
`proxy_pass` 不加末尾斜杠，必须把完整路径前缀转发到应用。
使用 `Host $http_host` 保留原域名和端口，同源管理请求校验依赖此信息。
侧边栏使用相对路径，始终保留浏览器当前协议、域名与端口。

只保留 `/cockpit/` 页面及 API，不提供前缀外的管理路由。
对外余额与报警接口分别为 `/cockpit/statistics/api/balance` 和 `/cockpit/statistics/api/alert`。
API 调用方使用完整 `/cockpit/statistics/api/` 路径。

```bash
nginx -t
nginx -s reload
```

随后访问 `https://<你的域名>/cockpit/statistics/`、`https://<你的域名>/cockpit/users/`、`https://<你的域名>/cockpit/keys/` 或 `https://<你的域名>/cockpit/operations/`。如果修改了 `PORT`，应同步修改
`../nginx.conf.example` 中的 upstream 端口。HTTP 请求应跳转 HTTPS，避免明文传输登录凭据。

如果 Nginx 也在容器内，需要将其加入共享 Docker 网络，并将 upstream 改为
`http://statistics:8000`（或为统计容器设置唯一网络别名后使用该别名）。
此时不能使用 `127.0.0.1:8091`，因为那会指向 Nginx 容器自身。

## 无 Nginx 直接访问

如果单独部署且没有 Nginx，在 `.env` 中设置：

```ini
BIND_HOST=0.0.0.0
PORT=8091
```

重新创建容器，使端口映射生效：

```bash
docker compose up -d --build --force-recreate
docker compose ps
```

`docker compose ps` 应显示类似 `0.0.0.0:8091->8000/tcp`。在服务器防火墙或云安全组中，
只向需要访问的来源开放 TCP `8091`，然后直接访问：

```text
http://<服务器IP>:8091/cockpit/statistics/
http://<服务器IP>:8091/cockpit/users/
http://<服务器IP>:8091/cockpit/keys/
http://<服务器IP>:8091/cockpit/operations/
```

报警 API 地址相应为：

```text
http://<服务器IP>:8091/cockpit/statistics/api/alert
```

示例：

```bash
curl -H 'Authorization: Bearer <管理员PAT>' \
  http://<服务器IP>:8091/cockpit/statistics/api/alert
```

该方式确实对外提供宿主机端口，不需要 Nginx。由于登录使用 HTTP Basic Auth，普通 HTTP 会以可还原形式传输凭据，
只适合受信任内网；如果跨公网访问，必须在入口增加 HTTPS，不应把 `8091` 裸露给整个互联网。

## 登录与权限

网页使用 New API 原有管理员用户名和密码，
读取 `users.password` 的 bcrypt 哈希进行校验。仅允许 `role >= 10`、`status = 1`
且未软删除的账号；每次请求重新检查，禁用、降权和修改密码立即生效。
不配置独立网页登录账号。统计查询不会修改 New API 数据；管理员在 `/cockpit/users/`
确认配额增减时，服务才会通过 New API 官方管理接口修改所选用户额度，详情见
[用户配额](quota.md)。启用定时配额规则后，主程序内置定时器会在对应周期按执行管理员权限调用同一接口。
网页访问不启动任务，应用启动也不会无条件发额度；仅执行符合既有到期窗口的启用规则。

## 用户管理授权

`/cockpit/keys/` 的写操作需要监控库用于审计。New API 源库须额外 SELECT `tokens`；
缺失用户 PAT 的补建仅授权受限函数，不授予业务表 UPDATE，安装 SQL 见[用户管理](users.md#安装补建函数与最小权限)。
页面、静态资源和 `/cockpit/keys/api/` 都经过认证。Nginx 统一转发 `/cockpit/`；
无 Nginx 时访问 `http://<应用主机>:8091/cockpit/keys/`。不公开数据库端口，不另起容器。

## 数据库初始化与迁移

两类 SQL 的目标数据库不同，不能混用：

| SQL | 位置 | 执行方式 |
|---|---|---|
| 监控建库 | [余额监控](monitoring.md#独立数据库) | PostgreSQL 管理员首次创建独立库和账号 |
| 应用表结构 | [migrations](../src/new_api_statistics/migrations/) 的 `001`～`010` | 容器启动自动按编号执行尚未登记的文件 |
| 缺失 PAT 补建函数 | [source_pat_function.sql](../sql/source_pat_function.sql) | New API 原库表所有者单独安装，再向查询角色授权 EXECUTE |

`source_pat_function.sql` 不是建库脚本，也不修改用户余额、密码或 KEY。
它在目标用户没有 PAT 时补建，已有 PAT 原样返回；不会批量生成所有用户的 PAT。
脚本位于源码仓库根目录的 `sql/`，Docker 镜像中为 `/app/sql/source_pat_function.sql`。
函数授权与安全边界见[用户管理](users.md#安装补建函数与最小权限)。

监控迁移保存在 Python 应用包中，随镜像和安装包分发。
`schema_migrations.version` 记录文件名：新库执行完整链，已有库只执行缺失项。
不能将已发布迁移合并进 `001` 或重编号；否则已登记旧文件的安装不会获得必要改动。
新增表结构请追加迁移，升级前备份并停止应用，见[升级](upgrading.md#数据库迁移)。
