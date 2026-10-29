# 接入已有 New API（发布镜像）

本页仅用于已有 New API / PostgreSQL 的接入。全新服务器应使用[全量部署手册](deployment.md)。

以下命令在独立 Cockpit 部署目录执行，使用同一 Release 的预构建镜像和公开附件。源码构建另见[源码部署](deployment-source.md#接入已有-new-api-的源码构建)。

## 部署条件

- 已运行的 New API、PostgreSQL 和 Docker Compose。
- 统计容器加入 New API 的现有 Docker 网络，不新建或替换其数据库。
- 数据库账号具有 SELECT `logs`、`options`、`users`、`channels`、`tokens` 的权限，建议使用专用查询账号，不授予业务表写权限。
- 生图工作站另需 SELECT `abilities` 的 `"group"`、`model`、`enabled`、`channel_id`、`priority`、`weight` 六列，以及可用的独立监控库控制并发。
- 网页通过同源 HTTPS 反向代理访问。下文以宿主机 Nginx 为例，也可使用容器化 Nginx 或同等代理。

## 配置与启动

从目标 [Release](https://github.com/ilove323/new-api-cockpit/releases) 下载配套附件，在新建的空部署目录执行下面的命令；替换 `vX.Y.Z`，不要使用 `latest`：

```bash
(
set -euo pipefail
umask 077
RELEASE_TAG='vX.Y.Z'
[[ "$RELEASE_TAG" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]]
install -d -m 700 /root/new-api/cockpit-release
cd /root/new-api/cockpit-release
test -z "$(find . -mindepth 1 -maxdepth 1 -print -quit)"
BASE="https://github.com/ilove323/new-api-cockpit/releases/download/$RELEASE_TAG"
for asset in compose.release.yml default.env.example nginx.conf.example image-studio-capabilities.example.json source_pat_function.sql SHA256SUMS; do
  curl --fail --location --silent --show-error --proto '=https' --tlsv1.2 -o "$asset" "$BASE/$asset"
done
sha256sum -c SHA256SUMS
cp compose.release.yml docker-compose.yml
cp default.env.example .env
printf '\nIMAGE_TAG=%s\n' "${RELEASE_TAG#v}" >> .env
printf '%s\n' "$RELEASE_TAG" > release-tag.txt
chmod 600 .env docker-compose.yml release-tag.txt
)
```

后续所有命令在 `/root/new-api/cockpit-release` 执行。已有安装应保留原 `.env`、密钥与项目名，按[升级步骤](upgrading.md)处理，不重新执行上面的首次下载流程。
编辑 `.env` 填写实际连接参数；`IMAGE_TAG` 必须为匹配该 Release 的版本号（去掉 `v`）：

| 参数 | 说明 |
| --- | --- |
| `IMAGE_TAG` | 所选 Release 的精确镜像版本，如标签 `vX.Y.Z` 对应 `X.Y.Z` |
| `PGHOST` | PostgreSQL 在共享 Docker 网络中的服务名或网络别名 |
| `PGPORT` | PostgreSQL 容器内部端口，通常为 `5432` |
| `PGDATABASE` | New API 使用的数据库名 |
| `PGUSER` / `PGPASSWORD` | 源库查询账号及密码；用户管理还可授予受限 PAT 补建函数 EXECUTE，不能授予业务表 UPDATE |
| `BIND_HOST` | 宿主机监听地址，默认 `127.0.0.1`；仅跨主机代理或可信内网调用需要调整 |
| `PORT` | 暴露到宿主机的端口，默认 `8091` |
| `IMAGE_STUDIO_PORT` | 图片池宿主机端口，默认 `8092`；调整时同步反向代理 |
| `IMAGE_STUDIO_MEMORY` | 图片池内存上限，默认 `1g`；结合图片大小和两笔并发核算 |
| `COCKPIT_PROCESS_ROLE` | 默认 `web`；图片池固定为 `image`，仅承担生成/编辑，不启动定时器或迁移 |
| `IMAGE_STUDIO_CAPABILITIES_FILE` | 可选能力 JSON 文件在容器内的路径；只读挂载，网页池与图片池使用同一份配置 |
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
docker compose config -q
docker compose pull
docker compose up -d --no-build --pull never
docker compose ps
docker compose logs --tail 100 statistics
```

配置独立监控库后，Docker entrypoint 会先执行增量迁移，失败则不启动该容器进程；
正常请求仅检查版本，不尝试建表。无需清空数据库。备份、更新与非 Docker 启动入口见[升级文档](upgrading.md)。

应用默认仅绑定宿主机 `127.0.0.1:8091`，供反向代理和探活使用。网页入口按“配合 Nginx”配置；
可信内网中的 API 直连见“无 Nginx 直接访问”。

## 运行状态检查

`statistics` 负责网页、每天北京时间 10:00 的余额检查和按规则执行的零点配额任务；`image-studio` 只处理图片请求。
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
生图/改图的两个精确 location 转发到独立图片池 `127.0.0.1:8092`，配置见[生图工作站请求池](#生图工作站请求池)。
流式智力测试还需加入示例中 `/cockpit/api/intelligence/test` 的精确 location，读取超时为 360 秒，并关闭缓冲、缓存与 gzip；模型生成本身最多 300 秒。
`/cockpit/api/intelligence/preview` 的精确 location 允许最多 2 MiB 表单，用于承载编码后的预览 HTML。
其他接口继续使用普通 `/cockpit/` 的超时配置。已有 Nginx 不会随应用代码自动更新。
业务页面、`/cockpit/docs/`、统一的 `/cockpit/api/` 接口和静态资源均由 Cockpit 处理。
`proxy_pass` 不加末尾斜杠，必须把完整路径前缀转发到应用。
使用 `Host $http_host` 保留原域名和端口，同源管理请求校验依赖此信息。
`/cockpit` 不应在 Nginx 中固定跳转到统计页，而是原路径转发给应用：管理员进入统计，普通用户进入模型使用。
侧边栏使用相对路径，始终保留浏览器当前协议、域名与端口。

本项目的页面、API 和静态资源统一使用 `/cockpit/` 前缀。
对外余额与报警接口分别为 `/cockpit/api/statistics/balance` 和 `/cockpit/api/statistics/alert`。
API 调用方使用完整 `/cockpit/api/statistics/` 路径。

```bash
nginx -t
nginx -s reload
```

随后访问 `https://<你的域名>/cockpit/`，通过侧边栏进入各页面。如果修改了 `PORT`，应同步修改
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
docker compose up -d --no-build --pull never --force-recreate statistics
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
密码和二次验证码直接交给官方接口，服务端每次经 `/api/user/self` 校验会话和启用状态。管理功能要求 `role >= 10`；所有启用用户也能访问模型使用、智力测试和生图工作站及其本人接口；其他管理路由仍默认禁止普通用户访问。
New API 撤销会话或禁用账号后，Cockpit 也拒绝访问；降权后不再允许管理功能。上游暂不可用返回 503，恢复后可重试。
浏览器必须能访问同源 New API `/api/` 和官方 `/sign-in` 页面；内部地址仅用于后台校验，不发送给浏览器。
登录本身无需新增表、迁移或数据库权限；不需要 New API 的 SESSION_SECRET，也不读取它的会话 Redis。生图工作站的 abilities 读权限见下节。详见[登录与会话](authentication.md)。
统计查询不会修改 New API 数据；管理员在 `/cockpit/users/`
确认配额增减时，服务才会通过 New API 官方管理接口修改所选用户额度，详情见
[用户配额](quota.md)。启用定时配额规则后，主程序内置定时器会在对应周期按执行管理员权限调用同一接口。
网页访问不启动任务，应用启动也不会无条件发额度；仅执行符合既有到期窗口的启用规则。

## 用户与令牌管理授权

用户、KEY 和配额写操作均需要监控库用于审计。New API 源库须能 SELECT `tokens`；
缺失用户 PAT 的补建仅授权受限函数，不授予业务表 UPDATE，安装 SQL 见[用户管理](users.md#安装补建函数与最小权限)。
管理页面经过管理员会话认证；模型使用、智力测试和生图工作站页面允许普通用户登录后访问。`/cockpit/api/` 下的业务接口也接受管理员 PAT，权限与审计不变，这三个模块始终限定认证账号本人。普通用户智力测试使用自身会话操作 KEY；生图工作站使用本人既有 KEY，无须扩大源库 PAT 补建函数的权限或升级数据库。
登录页和无敏感信息的静态资源公开。接口地址、确认头及示例见 [API 参考](api.md)。
Nginx 统一转发 `/cockpit/`。数据库仅在内部网络开放。

## 生图工作站请求池

网页与图片池使用同镜像、同代码和同源入口，分开处理同步 HTTP：

| 请求 | 宿主机目标 | 进程角色 |
|---|---|---|
| 页面、静态资源、令牌/模型目录、其他业务 API | 现有 `127.0.0.1:8091` | `COCKPIT_PROCESS_ROLE=web`（默认） |
| `/cockpit/api/image-studio/generations`、`/cockpit/api/image-studio/edits` | `127.0.0.1:8092` | `COCKPIT_PROCESS_ROLE=image` |

图片池为 2 个 worker、每个 1 个线程，不执行迁移或余额/配额定时器，不承接其他业务接口。
Compose 服务为 `image-studio`；`statistics` 保持原网页与调度职责。两个付费端点必须走图片池，误转网页池返回 `503`。
文生图 JSON 上限 192 KiB，编辑 multipart 总上传上限 24 MiB；两条精确路径代理响应等待 330 秒，普通路径不放宽。
应用连接 10 秒、上传 30 秒、总截止 300 秒，工作站浏览器等待 360 秒；反向代理不得自动重发付费请求。
保留完整路径和 `Host $http_host`。容器化代理应使用图片服务的内部网络地址，不能使用指向代理自身的 `127.0.0.1`。

图片池需要 `MONITOR_DATABASE_URL`，用独立 autocommit 连接的会话级 advisory lock 控制全站 2 个槽位、同用户 1 笔。
JWT/PAT 按同一个真实用户 ID 共用限额，在解析 multipart/解码前非阻塞取锁，容量满返回 `429`，不排队。
未配置监控库、锁连接失效或监控库重启后返回 `503`，停止新提交并排空/重启图片池再恢复，不降级成单机锁。
浏览器结束等待不提前释放容量；锁连接只在服务端请求结束后解锁并关闭。

### 源库最小读权限

由源库表所有者执行，替换实际 schema 和查询角色：

使用新式管理员 `nap_` PAT 调用 Cockpit 时，还需以下权限；只授予四个验证字段，不授予令牌表写权限。旧版 New API 尚无此表时不要执行；升级创建表后再授权。网页登录及普通用户 MCP 的余额查询不依赖这项权限。

```sql
GRANT SELECT (token_hash, user_id, scopes, expires_at)
ON TABLE public.user_access_tokens TO cockpit_reader;
```

图片路由另需：

```sql
GRANT SELECT ("group", model, enabled, channel_id, priority, weight)
ON TABLE public.abilities TO cockpit_reader;
```

模型清单关联当前启用渠道，严格读取该令牌实际路由分组的启用 abilities（含 auto 的当前有效分组），不以 `channels.models` 猜授权。
新增查询只要求列级 SELECT，不授予源表写权限、不新增数据库函数或工作站迁移；保留其他管理功能已使用的 PAT 函数。
`priority`、`weight` 用于把路由优先级和权重变化纳入契约修订，需与其他路由列一起授权。
源角色还需 `channels` 的 `id/status/type/model_mapping/base_url/setting/settings/param_override/header_override` 读取权限，以及 `options` 的 `key/value` 读取权限。程序先检查列是否存在和可读，只投影透传开关、覆盖是否为空与配置摘要，不读取渠道 KEY 或返回原始设置。可选列不存在时按已核对的默认处理；权限缺失不视为列不存在。
本人 KEY 由官方已审计的 `/api/token/{id}/key` 读取，无 PAT 补建，管理员也不能代用他人的 KEY。

### 图片协议契约

`IMAGE_STUDIO_CAPABILITIES_FILE` 必须指向容器内的 schema 2 JSON 文件，网页池和图片池读取同一文件，并以只读方式挂载。未配置的渠道没有默认图片能力。

从根目录的 [`image-studio-capabilities.example.json`](../image-studio-capabilities.example.json) 开始填写，文件中不放 KEY、密码或原始请求。示例渠道 ID 仅作格式演示，不能直接代表你的上游。

- `profiles`：独立的生成/编辑参数、输入限制、响应格式及证据修订。编辑声明 `native-mask`、`reference-only` 或 `unavailable`，以及单图/多图字段名。
- `channels`：实际渠道 ID、类型和 New API 转发方式，明确全局/渠道级透传前置条件。
- `models`：精确的公开模型 ID，逐渠道绑定协议档与预期映射后的模型名。

`sizes` 填该渠道实际支持的尺寸档位；没有确认支持任意像素尺寸时，`custom_size` 设为 `false`。模型官方支持自定义尺寸，不代表经由当前上游和 New API 转发后也支持。编辑的「自动」只选择已声明档位；只有 `auto` 的协议保持发送 `auto`，不会按上传图像另造尺寸。

只有被允许的参数才发给上游；生成与编辑不共享一份模糊的“支持”开关。所选令牌可能使用的同模型启用路由都必须有兼容契约，否则对应操作不可用。配置不能授予模型权限、代替 KEY 鉴权或替普通用户锁定渠道。

当前转发核对针对 New API `v1.0.0-rc.40` 的 OpenAI adaptor：重构模式下单图为 `image`、多图为 `image[]`；mask 会被转发。若供应商要求单图也必须为 `image[]`，必须有已核对的透传配置，不能补一张虚假图片绕过。透传时也不能依赖网关改写模型名。
源渠道的透传/参数覆盖设置变化后，原契约不会继续盲发；修订不一致在生成前返回 `409`。不要为了工作站擅自修改 New API 全局透传或重试设置，先检查对应渠道和其他使用方。

默认接收 Base64；需要 `response_format` 的兼容渠道应在各操作中显式声明。URL-only 响应须同时配置安全结果规则：仅 HTTPS、精确主机或公开网络白名单，逐跳验证与地址绑定，拒绝私网/环回/链路本地地址；不转发模型 KEY、Cookie 或 Referer。验证后仍向浏览器返回 Base64，不返回外链。

文件最大 64 KiB，无效配置返回 `503`，不静默回退。更新前在本地运行契约测试与配置校验，不通过收费试错探测。

### 短期请求回执

Compose 的 `image-studio-receipts` 是独立 Redis，只与图片池共享内部网络，没有宿主机端口，不连接 New API 会话缓存。数据放在独立命名卷，配置 AOF、`appendfsync always` 和 `noeviction`。不要在更新/回退时删除该卷。

图片池默认连接 `redis://image-studio-receipts:6379/0`。已有符合要求的 Redis 也可通过 `IMAGE_STUDIO_REDIS_URL` 指定；允许 `redis://` 或启用证书验证的 `rediss://`，URL 不带查询参数。跨主机使用受限 ACL 与 TLS。

| 环境变量 | 默认值 / 要求 |
|---|---|
| `IMAGE_STUDIO_REDIS_URL` | Compose 提供独立回执服务；非 Compose 部署必须填写 |
| `IMAGE_STUDIO_RECEIPT_NAMESPACE` | `cockpit:image-receipts:v1`；同一站点所有实例一致，不与其他站点混用 |
| `IMAGE_STUDIO_RECEIPT_TTL` | 86400 秒；允许 3600～604800 |

回执仅含账号 ID、输入 SHA-256 摘要、派发/结束状态和时间；Redis 键使用请求 ID 的摘要，不存图片、提示词或 KEY。不创建数据库任务表。

受限 ACL 需允许所选命名空间的 `GET`、`SET`、`EVAL`，以及探活所需的 `INFO` 和 `CONFIG GET`；连接认证和客户端初始化依 Redis 版本授予必要权限。不得开放 `FLUSHDB`、`FLUSHALL` 或 `CONFIG SET`。服务不执行这些管理操作。

Redis 不可用、AOF 写入失败、策略不符时，付费请求在派发前返回 `503`。检测到 Redis 重启时需核对持久化状态并重启图片池；不自动放弃去重。已派发但进程退出的回执保持“结果未知”，不会重新派发。
终态回执保留至原 TTL；不提供取回图片的接口。持久卷丢失、旧备份恢复或 TTL 到期不保证去重，必须人工核对在途请求，不能清空 Redis 当成重试办法。

### 探活、更新与回退

图片池健康检查同时核对监控库并发锁和 Redis 回执存储。宿主机探活图片池：`curl --fail-with-body http://127.0.0.1:8092/healthz`，不发模型请求。
图片池默认内存上限 1 GiB、`/tmp` 128 MiB、Gunicorn 排空 330 秒、容器停止窗口 360 秒。
核算临时上传、解码像素、base64/JSON 和两个请求的内存峰值，不沿用网页池的 64 MiB `/tmp` 与 90 秒停止窗口。
启动、健康检查和自动测试不得调用生成/编辑。

更新前保留上一镜像、Compose、Nginx 与协议文件，保留回执卷；先停止新图片提交，等待服务端在途请求结束后替换图片池。
回退时恢复这些配置与镜像，不改变 New API 的 `/api/`、`/v1/` 路由或业务数据；工作站无新增任务表或图库需要回迁。
付费请求断连/超时可能已生效，不在回退后重放。资源规格和使用规则见[生图工作站](image-studio.md)。

## 数据库初始化与迁移

建库、应用迁移和源库函数的执行位置如下：

| SQL | 位置 | 执行方式 |
|---|---|---|
| 监控建库 | [余额监控](monitoring.md#独立数据库) | PostgreSQL 管理员首次创建独立库和账号 |
| 应用表结构 | [migrations](../src/new_api_cockpit/migrations/) 的 `001`～`012`（当前开发代码） | 容器启动自动按编号执行尚未登记的文件 |
| 缺失 PAT 补建函数 | [source_pat_function.sql](../sql/source_pat_function.sql) | New API 原库表所有者单独安装，再向查询角色授权 EXECUTE |

`source_pat_function.sql` 安装源库 PAT 补建函数：目标用户没有 PAT 时补建，已有 PAT 原样返回。
函数仅允许这一项写入，用户余额、密码和 KEY 由官方 API 修改。
发布附件下载后脚本位于部署目录的 `source_pat_function.sql`，Docker 镜像中为 `/app/sql/source_pat_function.sql`；使用与镜像同版本的文件。
函数授权与安全边界见[用户管理](users.md#安装补建函数与最小权限)。

监控迁移保存在 Python 应用包中，随镜像和安装包分发。
`schema_migrations.version` 记录文件名：新库执行完整链，已有库只执行缺失项。
不能将已发布迁移合并进 `001` 或重编号；否则已登记旧文件的安装不会获得必要改动。
新增表结构请追加迁移，升级前备份并停止应用，见[升级](upgrading.md#数据库迁移)。


## 可选 AI 助手请求池

先确认所选 Release 已包含聊天组件和 profile；`v0.3.0` 不包含聊天，不能把开发分支的 chat 模板套在旧镜像上。
聊天默认关闭，不影响现有web/image服务。启用、私有data目录权限、稳定instance ID、只读模型能力文件及可选Compose profile步骤见[AI助手配置](chat.md#配置与可选请求池)。
chat角色使用同镜像独立8093池，只有它挂载`./data/chat`；不得把数据目录放进镜像或公开为静态资源。
该角色不执行监控迁移/定时器，readiness不依赖监控库或图片回执Redis；没有新增数据库迁移。
Nginx仅把`/cockpit/api/chat/`路由给chat池，并关闭SSE缓冲。未启用chat profile时原有默认启动方式保持不变。

## IP 与 SSH 隧道访问

公网部署应使用 HTTPS；可信隔离内网可明确选择 HTTP，并设置 `COCKPIT_COOKIE_SECURE=false`。New API 也需按对应版本配置允许 HTTP 会话 Cookie，否则两套系统不能共用登录。
HTTP 不加密密码、会话和请求内容，不应直接暴露到公网。

不绑定域名、希望任何 Host 都进入这套服务时，应将其设置为端口的默认站点，而不是限定一个 `server_name`：

```nginx
server {
    listen 80 default_server;
    listen [::]:80 default_server;

    # 在此放入 nginx.conf.example 中全部 Cockpit location。
    # 保留精确的图片路由、聊天上传限制和 SSE 设置。

    location / {
        proxy_pass http://127.0.0.1:3000;
        proxy_set_header Host $http_host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 360s;
        proxy_buffering off;
    }
}
```

同一监听地址只能有一个 `default_server`。先备份并屏蔽发行版 `/etc/nginx/nginx.conf` 或 `conf.d` 中原有的默认欢迎站点；只删新站点的 `server_name`，不会取代旧默认站点。
用 `nginx -t` 检查后重载现有 Nginx，不需要安装第二套 Nginx。

SSH 转发本机端口到服务器 80 后，浏览器 Host 会包含本机名称和端口，代理必须保留 `$http_host`，不能写死为服务器 IP。
根路径 `/` 是 New API 首页，登录在 `/sign-in`，Cockpit 在 `/cockpit/`，不是把根路径全部改到 Cockpit。
若怀疑看到旧欢迎页，先核对响应正文/版本头与生效配置，再检查浏览器缓存；不要把响应头的 `Server: nginx` 当成返回欢迎页的证据。
