# 部署

以下命令均在项目根目录执行。

## 部署条件

- 已运行的 New API、PostgreSQL 和 Docker Compose；Nginx 为可选。
- 统计容器加入 New API 的现有 Docker 网络，不新建或替换其数据库。
- 数据库账号具有 SELECT `logs`、`options`、`users`、`channels` 的权限，建议使用专用只读账号。
- 使用 Nginx 时默认其运行在同一宿主机上；容器化 Nginx 和无 Nginx 的访问方式见下文。

## 配置与启动

根据 [.env.example](../.env.example) 创建 `.env`，将权限设为 `600`，填写实际数据库连接参数：

| 参数 | 说明 |
| --- | --- |
| `PGHOST` | PostgreSQL 在共享 Docker 网络中的服务名或网络别名 |
| `PGPORT` | PostgreSQL 容器内部端口，通常为 `5432` |
| `PGDATABASE` | New API 使用的数据库名 |
| `PGUSER` / `PGPASSWORD` | 只读数据库账号及密码 |
| `BIND_HOST` | 宿主机监听地址；配合 Nginx 使用 `127.0.0.1`，无 Nginx 直接访问使用 `0.0.0.0` |
| `PORT` | 暴露到宿主机的端口，默认 `8091` |
| `PYTHON_IMAGE` | 构建使用的 Python 镜像，默认 `python:3.12-slim` |
| `PIP_INDEX_URL` | 可选 Python 包索引地址 |
| `NEW_API_NETWORK` | 已有 New API Docker 网络名，默认 `new-api-network` |
| `NEW_API_INTERNAL_URL` | New API 在共享 Docker 网络中的内部地址，默认 `http://new-api:3000`；仅用户配额页面执行增减时使用 |
| `MONITOR_DATABASE_URL` | 独立监控库连接串；用于保存额度、月度归档、报警及通知渠道配置 |
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
docker compose up -d --build statistics balance-worker
docker compose ps
docker compose logs --tail 100 statistics
docker compose logs --tail 100 balance-worker
```

应用默认仅绑定宿主机 `127.0.0.1:8091`。使用 Nginx 时继续阅读下一节；不使用 Nginx 时按“无 Nginx 直接访问”配置外部监听。

## 配合 Nginx

把 `../nginx.conf.example` 中 `/statistics`、`/statistics/`、`/quota`、`/quota/` 四个 location 加入现有站点的 HTTPS `server` 块，
保留 New API 原来的 `/` 转发配置。前两者是用量统计，后两者是用户配额；其他路径继续访问 New API。
`proxy_pass` 不要额外添加末尾斜杠，以保留应用需要的 `/statistics/` 路径前缀。
页面互跳使用 `/statistics/` 和 `/quota/`，不写死域名。入口缺少末尾斜杠时，
示例配置使用 `absolute_redirect off` 返回相对路径，并保留查询参数；浏览器会
沿用当前协议、域名和端口，避免在代理或非标准端口访问时跳到别的地址。

```bash
nginx -t
nginx -s reload
```

随后访问 `https://<你的域名>/statistics/` 或 `https://<你的域名>/quota/`。如果修改了 `PORT`，应同步修改
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
http://<服务器IP>:8091/statistics/
http://<服务器IP>:8091/quota/
```

报警 API 地址相应为：

```text
http://<服务器IP>:8091/statistics/api/alert
```

示例：

```bash
curl -H 'Authorization: Bearer <管理员PAT>' \
  http://<服务器IP>:8091/statistics/api/alert
```

该方式确实对外提供宿主机端口，不需要 Nginx。由于登录使用 HTTP Basic Auth，普通 HTTP 会以可还原形式传输凭据，
只适合受信任内网；如果跨公网访问，必须在入口增加 HTTPS，不应把 `8091` 裸露给整个互联网。

## 登录与权限

网页使用 New API 原有管理员用户名和密码，
读取 `users.password` 的 bcrypt 哈希进行校验。仅允许 `role >= 10`、`status = 1`
且未软删除的账号；每次请求重新检查，禁用、降权和修改密码立即生效。
不配置独立网页登录账号。统计查询不会修改 New API 数据；管理员在 `/quota/`
确认配额增减时，服务才会通过 New API 官方管理接口修改所选用户额度，详情见
[用户配额](quota.md)。
