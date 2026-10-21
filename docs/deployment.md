# 从零全量部署 New API 与 Cockpit

本页是**无需聊天上下文的执行手册**。目标是在一台 Linux 服务器上安装 New API、两个 PostgreSQL 数据库、两套 Redis，以及 Cockpit 网页/管理、生图、AI 助手三个请求池，并配置统一 Nginx 入口后登录使用。

**执行本手册必须已经获得目标服务器的部署授权。文档本身不是操作其他服务器的授权。**
只需要接入已运行的 New API 时，改用[已有 New API 接入](existing-installation.md)，不要用下面的首次建库流程覆盖现有数据库。

## 1. 先收集这些输入

没有历史上下文的 AI 应先填写下表，再操作；不要沿用其他聊天里的 IP、跳板机、账号、密码或证书。

| 输入 | 要求与默认行为 |
|---|---|
| 目标 SSH | 明确目标地址、用户、端口；有跳板机时分别确认两跳身份及指纹。能登录跳板机不等于已到目标服务器 |
| 安装性质 | 明确是**全新安装**还是已有系统升级/恢复。发现业务数据、现有 Compose 或 PostgreSQL 数据目录时停止首次初始化，转[升级](upgrading.md) |
| 安装目录 | 默认 `/root/new-api`，源码、配置、应用数据、备份均放这里。系统 Nginx 生效副本及 Docker 引擎自身存储保留系统标准位置 |
| 访问方式 | 按用户要求选择：可信内网 IP + HTTP/80，或域名/证书 + HTTPS。不要擅自要求域名、签自签证书或改用 443 |
| Nginx / Docker | 已安装就复用，不能因部署应用而重装、覆盖全局配置或更换镜像源 |
| 管理员 | 用户提供用户名与密码；密码仅交互输入，不写进文档、命令参数、shell 历史、`.env` 或日志 |
| 上游渠道 | 真实供应商地址、KEY、精确模型 ID、分组和所支持协议。没有时可以完成安装与登录，但不得宣称模型已能调用 |
| 生图/聊天能力 | 以实际渠道证据配置；不能从模型名称猜工具、图片编辑或原生压缩能力 |
| 搜索与通知 | SearXNG、飞书/钉钉/SMTP 为可选外部服务；未提供时保持未配置，不伪造可用状态 |
| 网络与资源 | 建议至少 8 GiB 内存及 30 GiB 可用磁盘；聊天总额默认 10 GiB。Docker 后端子网必须不与现有路由、VPN 或 Docker 网络冲突 |

HTTP 不加密密码和会话，只能用于用户明确选择的可信隔离内网。应用内部的明文通信仅限未发布端口的单机 Docker 网络。

## 2. 组件、端口与数据库职责

本方案使用[完整 Compose 模板](../deploy/full-stack/compose.yml)，七个服务**全部启动**；不需要另开 chat profile。

| 服务 | 职责 | 对宿主机发布 |
|---|---|---|
| `postgres` | `new_api` 源库、`new_api_cockpit` 监控库 | 不发布 |
| `redis` | New API 自身会话/缓存，独立认证 | 不发布 |
| `image-studio-receipts` | 仅生图短期去重回执，独立认证、AOF always、noeviction | 不发布 |
| `new-api` | 官方网关、账号、渠道、KEY、原始日志 | `127.0.0.1:3000` |
| `statistics` | Cockpit 网页/API、统计、用户/KEY/额度、迁移与调度 | `127.0.0.1:8091` |
| `image-studio` | 同步生图/改图请求池 | `127.0.0.1:8092` |
| `chat` | 私有聊天、文件、Skills / Memory、受控联网 | `127.0.0.1:8093` |

宿主机 Nginx 是唯一网页入口。`/`、`/api/`、`/v1/`、`/sign-in` 发往 New API；`/cockpit/` 发往网页池，图片两个付费端点及聊天 API 按精确规则发往独立请求池。

- `newapi_owner`：只拥有 New API 源库，供 New API 初始化自己的业务表。
- `cockpit_reader`：源库查询和受限 PAT 补建函数执行，无业务表 `UPDATE`。
- `cockpit_monitor`：只拥有独立监控库，供 Cockpit 迁移、审计、预算和调度使用。
- 聊天池没有 PostgreSQL、通知或 New API 会话签名密钥，只挂载本实例的可写聊天目录。
- 全部功能装好不等于默认执行配额、通知或模型生成；这些需要实际配置和明确操作。

## 3. 核对目标和前置条件

以下操作均在**目标服务器**执行，使用 root 或等价权限：

```bash
hostname
id
cat /etc/os-release
uname -m
free -h
df -h /root
ip route
ss -lntp
docker version
docker compose version
docker ps -a
docker network ls
nginx -v
nginx -t
git --version
```

再检查已有 Docker 网络的子网与 `/root/new-api` 内容。默认后端子网为 `172.28.241.0/24`；若冲突，后面用 `--subnet` 改为另一空闲的私网 `/24`。
**不要为腾端口直接停止、删除其他容器、站点或数据库。**

本模板使用 Compose 的健康依赖、`--wait` 等特性，要求 Compose plugin 2.27 或更新版本。
Docker 未安装时按目标发行版使用 [Docker Engine 官方步骤](https://docs.docker.com/engine/install/)及 [Compose plugin 官方步骤](https://docs.docker.com/compose/install/linux/)；不要拿另一发行版的安装命令硬套，也不要执行未经核对的远程安装脚本。
Docker 已安装但服务未运行时，在确认不影响其他业务后启动并设置开机启动。

Nginx 仅在确实缺失时使用目标系统的软件仓库安装：RPM 系统使用 `dnf install nginx` / `yum install nginx`，Debian/Ubuntu 使用 `apt-get update && apt-get install nginx`。已有 Nginx 不再安装。
同样先核对 Git、Bash、tar 和可用的 Python 3.6+，缺失时用目标系统的软件仓库补齐；不要替换系统 Python 或运行本机的部署命令来代替目标服务器操作。下面的 shell 片段按 Bash 执行。

本方案默认 New API 固定为 [`v1.0.0-rc.40`](https://github.com/QuantumNous/new-api/releases/tag/v1.0.0-rc.40)，因为当前 Cockpit 的 JWT 登录、`users.access_token` PAT 和生图协议按这一版兼容；不要替换为 `latest`。PostgreSQL 使用 16，Redis 使用 7。其他版本先按[兼容范围](compatibility.md)核对。

## 4. 获取源码并生成首次配置

确认这是全新实例后执行：

```bash
umask 077
install -d -m 700 /root/new-api
git clone --branch develop https://github.com/ilove323/new-api-cockpit.git /root/new-api/source
cd /root/new-api
# 若用户指定了发布标签或提交，先在 source 中检出；所选版本必须包含 deploy/full-stack。
git -C source rev-parse HEAD > source-commit.txt
test ! -e docker-compose.yml
cp source/deploy/full-stack/compose.yml docker-compose.yml
chmod 600 docker-compose.yml source-commit.txt
```

新功能未必已包含在旧发布镜像中，因此本方案从所记录的源码构建。后续不要不加区分地 `git pull`、更换分支或覆盖源码。

首次配置程序仅依赖 Python 标准库 3.6+；Cockpit 容器内部仍使用 Python 3.12。
RHEL 8 没有 `python3` 命令但有平台 Python 时可使用 `/usr/libexec/platform-python`：

```bash
cd /root/new-api
PYTHON=$(command -v python3 || true)
if [ -z "$PYTHON" ]; then PYTHON=/usr/libexec/platform-python; fi
"$PYTHON" -c 'import sys; assert sys.version_info >= (3,6)'
# 用户明确选择 IP / HTTP 80 时：
"$PYTHON" source/deploy/full-stack/init-config.py --root /root/new-api --scheme http
# HTTPS 方案只将上面参数改为 --scheme https，勿两条都执行。
```

如子网冲突，在同一条首次命令增加 `--subnet <已核对空闲的私网/24>`。
程序生成稳定唯一实例 ID、不同数据库/Redis 密码、New API `SESSION_SECRET` / `CRYPTO_SECRET` 和通知加密密钥，只输出非敏感摘要。
它会拒绝覆盖已有配置、替换密钥或对已有数据重新初始化。中断后先检查实际状态，**不能靠删除 data/config 重来**。

生成目录如下：

```text
/root/new-api/
  source/                 冻结版本源码（含完整安装模板）
  source-commit.txt        实际构建提交
  docker-compose.yml      完整七服务配置
  .env                    镜像/网络等 Compose 参数，无管理员密码
  config/
    postgres.env          首次建库专用配置
    new-api.env           New API 源库/Redis/持久会话与加密密钥
    web.env image.env     源库查询、监控库与生图回执配置
    chat.env              仅聊天设置和内部 New API 地址
    *-health.env *.conf   Redis 认证与 AOF 配置
    *-capabilities.json   能力白名单（初始为空）
    instance.json nginx/
  data/                   postgres / redis / image-studio-receipts / new-api / chat
  logs/new-api/
  backups/ releases/
```

配置目录权限 `0700`、私有环境文件 `0600`。聊天数据属主为 `10001:10001`；本模板固定官方 Redis 7 Alpine 的 `999:1000`，Redis 配置为 `0400`。
使用其他 Redis 镜像前必须检查其实际 UID/GID 并同步配置，不能用 `chmod 777` 绕过。聊天目录只支持本机 POSIX 文件系统，不使用共享 NAS/NFS 或多主机副本。

## 5. 拉取镜像并启动基础组件

```bash
cd /root/new-api
docker compose config -q
docker compose pull postgres redis image-studio-receipts new-api
docker compose build statistics
docker compose up -d --wait --wait-timeout 180 postgres redis image-studio-receipts new-api
```

三个 Cockpit 请求池使用同一个本地镜像，因此只需构建 `statistics` 一次。
确认镜像架构与目标主机匹配；不要把 amd64 镜像强行用于 arm64，也不要在用户不知情时开启模拟运行。

镜像站不可达时不要反复盲目重试或覆盖全机 Docker 镜像源。在 `.env` 可分别设置 `POSTGRES_IMAGE`、`REDIS_IMAGE`、`PYTHON_IMAGE`、`NEW_API_IMAGE`，但必须保持对应版本与受信任来源。
也可在有网络的受信任机器获取**同版本、同架构**镜像，经校验和验证后 `docker save` / `docker load` 导入。不要用另一个版本的 New API 顶替 rc.40；离线安装还需预置 Cockpit 构建依赖或经过核对的 Cockpit 运行镜像。

首次空 PostgreSQL 数据目录会执行[建库脚本](../deploy/full-stack/postgres-init.sql)，创建角色和两个库。New API 随后自动创建自己的业务表；不要把 Cockpit 的迁移刷进源库。
该脚本不会在已有 PostgreSQL 数据目录上重新执行；若连接失败，应查明角色/库和日志状态，不能删除数据卷让初始化重跑。

## 6. 给源库最小授权，初始化管理员

必须先确认 New API 已健康并创建 `users/logs/options/channels/tokens/abilities`，再执行：

```bash
cd /root/new-api
set -o pipefail
{
  cat source/sql/source_pat_function.sql
  cat source/deploy/full-stack/source-grants.sql
} | docker compose exec -T postgres psql -X -v ON_ERROR_STOP=1 -U newapi_owner -d new_api
```

这会安装仓库唯一的受限 PAT 函数并授权查询角色，不给业务表 UPDATE，也不改用户密码、额度或 KEY。
`psql` 命令不包含数据库密码；本地容器 socket 管理与对外 TCP 认证是不同边界，数据库端口始终不发布。

在**服务器交互终端**初始化用户指定的官方管理员：

```bash
cd /root/new-api
PYTHON=$(command -v python3 || true)
if [ -z "$PYTHON" ]; then PYTHON=/usr/libexec/platform-python; fi
"$PYTHON" source/deploy/full-stack/bootstrap-admin.py
```

该程序通过 `http://127.0.0.1:3000/api/setup` 检查并初始化，只使用官方接口；密码用 `getpass` 无回显读取，不写文件或参数，不打印请求/响应。它拒绝替换已有 root。
用户名应符合 New API 限制，密码必须满足目标版本策略。AI 不应凭空设置默认密码或把用户密码拼入命令；无法安全交互时，把这一步交给用户完成。

管理员应在**开放 Nginx 入口之前**完成初始化，避免未初始化页面暴露给其他访问者。
Cockpit 不创建第二套账号，两套系统共用此 New API 用户。

## 7. 启动全部 Cockpit 请求池并刷好监控表

```bash
cd /root/new-api
docker compose up -d --wait --wait-timeout 180 statistics image-studio chat
docker compose ps
docker compose exec -T postgres psql -X -U cockpit_monitor -d new_api_cockpit \
  -c 'SELECT version FROM schema_migrations ORDER BY version;'
```

网页池启动入口按编号自动应用缺失的 `source/src/new_api_cockpit/migrations/*.sql`，当前为 `001`～`011`。
将查询返回的文件名与源码实际迁移文件核对；已有库只补缺失项，不重编号，不合并旧迁移，不清库。
**图片和聊天角色不执行迁移或调度。** 图片池等网页池健康后再启动，避免迁移未完成时承接付费请求。

此时应有七个服务运行且健康；源库和监控库是两套独立 schema/权限，源库查询角色没有表级 UPDATE。
某项不健康只排查该组件，不用一遍遍重跑建库、生成新密钥或重复模型请求来“测试”。

## 8. 按需求配置现有 Nginx

### IP / HTTP / 任意 Host

默认使用[HTTP 默认站模板](../deploy/full-stack/nginx-http.conf)：**不写固定 `server_name`**，任何到达该端口的 Host（服务器 IP、localhost、SSH 转发端口）都进入同一套系统。

先备份系统 Nginx，再看 `nginx -T`，找出发行版默认欢迎站点。必须屏蔽其完整 `server { ... }`；它可能在 `/etc/nginx/nginx.conf`，也可能在 `conf.d/default.conf`。
只删除新站的 `server_name` 不够：旧 `default_server` 仍可能接管请求。不要误删已有的其他业务站点；同一监听地址只能有一个默认站。
模板同时监听 IPv4 和 IPv6；主机确实禁用 IPv6 时去掉 `[::]:80` 那一行，不能因此阻断 IPv4 部署。

```bash
cd /root/new-api
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
install -d -m 700 "backups/nginx-$STAMP"
tar -czf "backups/nginx-$STAMP/before.tar.gz" -C /etc nginx
systemctl is-active nginx > "backups/nginx-$STAMP/was-active.txt" || true
systemctl is-enabled nginx > "backups/nginx-$STAMP/was-enabled.txt" || true
if command -v getsebool >/dev/null 2>&1; then
  getsebool httpd_can_network_connect > "backups/nginx-$STAMP/selinux-before.txt"
fi
# 完成上面的默认站定位与屏蔽后，再部署这两个文件：
install -m 644 source/nginx.conf.example config/nginx/new-api-cockpit-locations.inc
install -m 644 source/deploy/full-stack/nginx-http.conf config/nginx/new-api.conf
install -m 644 config/nginx/new-api-cockpit-locations.inc /etc/nginx/new-api-cockpit-locations.inc
install -m 644 config/nginx/new-api.conf /etc/nginx/conf.d/new-api.conf
```

宿主 Nginx 不应直接读取 `/root` 下的配置软链接：目录权限与 SELinux 可能阻止服务读取，因此保留原件并部署系统标准位置的副本。

RHEL 等启用 SELinux 的系统保持 Enforcing，恢复新配置标签；如果 Nginx 被策略拒绝连接本机代理端口，记录原值后仅启用所需代理连接布尔值，不关闭 SELinux：

```bash
restorecon -F /etc/nginx/nginx.conf /etc/nginx/conf.d/new-api.conf /etc/nginx/new-api-cockpit-locations.inc
getsebool httpd_can_network_connect
# 只有原值为 off 且确需代理连接时：
setsebool -P httpd_can_network_connect on
```

非 SELinux 系统不执行这一段。不要通过关闭防火墙、SELinux、证书验证或把 3000/8091/8092/8093 全部发布到公网来修复反代。

```bash
nginx -t
# 已运行则重载；未运行则启动，二选一：
systemctl reload nginx
# systemctl start nginx
systemctl enable nginx
systemctl enable docker
```

如果 `nginx -t` 失败，不执行 reload；从备份恢复本次修改的文件和默认站片段，重新检查后恢复服务。回退时同时按记录恢复服务启停、自启及 SELinux/防火墙变更，不覆盖其他站点的新变更。

如果用户要求开放入站访问，只允许所需的 80/443 与明确来源；修改防火墙前先确定实际接口和 zone，保留现有规则。
不要因为服务器本机请求成功就声称其他网段、VPN 或公网已经连通。

### HTTPS 方案

只在用户明确选择 HTTPS 且提供有效证书时使用：首次配置选 `--scheme https`，将模板中监听端口改为 443 SSL，配置与访问名匹配的证书/私钥，再确定 80 是否跳转。
域名场景按用户域名设置站点；IP 场景证书也必须覆盖该 IP。不自动生成自签证书或绕过浏览器警告。

两套系统 Cookie 必须与外部协议一致；后续 HTTP/HTTPS 切换要备份并修改 `new-api.env` 的 `SESSION_COOKIE_SECURE` 以及网页池的 `COCKPIT_COOKIE_SECURE` 后重建对应容器，**不是重新运行首次配置程序生成密钥**。
浏览器中的 New API 与 Cockpit 必须同协议、同主机、同端口；Nginx 保留 `Host $http_host` 和正确的 `X-Forwarded-Proto`。

## 9. 配置真正可用的模型、图片与联网

此步的渠道参数必须来自用户或其受信任运维资料，不能从另一个站点复制真实 KEY。

1. 登录 New API，按实际供应商添加渠道、准确模型名/映射和用户分组；确认账号有使用权限与额度。若是私有站点，按用户要求设置注册与账号策略。
2. 在本人账号创建/配置所需 KEY。生图工作站仅使用**本人已有、分组精确为 `GPT`** 的 KEY；聊天使用本人已有 KEY，不自动建 KEY、改组或改额度。
3. 根据[生图协议说明](existing-installation.md#图片协议契约)和[能力示例](../image-studio-capabilities.example.json)填写 `config/image-studio-capabilities.json`，使用真实渠道 ID、公开模型 ID 与已核对的生成/编辑协议。示例不是可直接工作的真实渠道。
4. 根据[聊天能力说明](chat.md#模型能力)填写 `config/chat-capabilities.json`；明确协议、工具、reasoning 与压缩能力。模型还必须在本人 KEY 实际目录中，配置文件不能越权添加模型。
5. 原子替换 bind-mounted 能力文件后重建使用它们的容器，保证所有进程使用同一版本；网页与图片池共用图片配置，聊天池只接收聊天配置。
6. 需要搜索时配置 `chat.env` 中的 SearXNG JSON 后端；未配置搜索不影响独立网页抓取/文本下载。不把搜索后端指向内网探测代理。
7. 通知、预算、监控开关和定时配额按实际需求在 Cockpit 配置；没有授权不发通知测试、不新增自动发额度规则。

首次配置生成的能力文件为空白名单，是**安全的未配置状态**，不是通用模型能力模板。
未提供真实渠道/KEY 时，可以交付“全部组件已安装、可登录、数据库已初始化”，但必须标明“模型/图片生成尚未配置”，不能承诺能聊天或生图。

在维护窗口更新配置并确认没有在途生成请求后执行：

```bash
cd /root/new-api
docker compose up -d --force-recreate --wait --wait-timeout 180 statistics image-studio chat
```

## 10. 最小验收与交付边界

默认只做下面的最小验收，不跑大批回归、不清真实聊天、不调用付费模型、不修改真实额度：

- 七个服务健康；监控迁移版本与源码对应，两个数据库与源库查询权限符合第 2 节。
- 实际访问入口 `/api/status` 返回 New API JSON，而不是仅检查 `Server: nginx` 头；`/` 本来就是 New API 官方首页。
- 使用同一实际访问地址登录 New API，再登录或复用会话进入 `/cockpit/`，确认统计、用户/KEY、操作记录、生图和聊天页面可打开。
- 无 KEY 的空页面可以选择允许类型的文本文件；50 MiB 上限与 Nginx `51264k` 一致。除非用户明确要求，不创建大型测试文件或运行生成任务。
- `8091/8092/8093/3000` 仅绑定回环；PostgreSQL、Redis 不发布端口；实例目录与密钥权限正确。

如果用户只要求“能登录两套系统就结束”，完成两次实际登录后停止，不擅自增加渠道压测、付费测试或数据清理。
若用户另行授权真实模型验收，只发最小必要请求，事先说明会计费；分别记录聊天、图片生成、编辑、搜索等实际通过项，不用健康状态代替。

交付记录应包含：访问地址、源码提交、镜像 ID/摘要、服务状态、数据库迁移、数据/配置/备份路径，以及尚缺的真实渠道/搜索/通知配置。
记录账号名称但不回显密码；不要把具体环境秘密、真实 KEY 或用户数据提交到 Git。

记录实际运行镜像时只输出镜像元信息，不要把包含环境变量的完整 `docker inspect` 或未脱敏 `docker compose config` 写入公共日志：

```bash
cd /root/new-api
docker compose images > releases/images.txt
docker compose images -q | sort -u | xargs -r docker image inspect \
  --format '{{.Id}} {{json .RepoTags}} {{json .RepoDigests}}' > releases/image-identities.txt
chmod 600 releases/images.txt releases/image-identities.txt
```

### SSH 隧道访问

转发应在用户的本机执行，目标必须是刚安装的**目标服务器**，不是跳板机的 80：

```bash
ssh -N -o BatchMode=yes -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 \
  -L 127.0.0.1:24444:127.0.0.1:80 <目标用户>@<目标地址>
```

需要跳板机时加入 `-J <跳板用户>@<跳板地址>`，目标非 22 端口加入 `-p <目标SSH端口>`。
如果目标是跳板机回环上的转发端口，目的地址仍是对应 `root@127.0.0.1 -p <端口>`；不能套用另一台服务器的命令。
首次连接先经可信渠道核对目标指纹，再交互保存主机记录；`BatchMode=yes` 不会弹出确认。不要用 `StrictHostKeyChecking=no` 或盲目删除旧指纹绕过冲突。

用户本机访问 `http://localhost:24444/sign-in` 与 `http://localhost:24444/cockpit/`。保持隧道进程运行，先核对本机监听者和响应再判断缓存；不要只在服务器上验收。

## 11. 备份、回退与后续升级

交付前设置 Docker 与现有 Nginx 开机启动，容器使用 `unless-stopped`；备份由用户保管到安全的异机存储，本机副本不防磁盘丢失。
至少保留：

- `new_api`、`new_api_cockpit` 的 PostgreSQL 一致性备份及角色/权限；
- 完整 `config`、`.env`、Compose、源码提交与镜像标识，尤其是 New API 两个持久密钥和通知加密密钥；
- 停止聊天写入后的一致 `data/chat`，保留 instance ID；
- 两套 Redis 持久数据，尤其是防重复派发的短期生图回执；
- Nginx 原配置、修改后的原件，以及变更前服务/SELinux/防火墙状态。

不能把运行中的 PostgreSQL 目录直接打包冒充一致备份：使用 `pg_dump` / `pg_dumpall`，或停止相关服务后做冷备。也不能仅备份聊天索引而丢掉事件、manifest、快照或文件。
恢复先核对目标、停止写入并保存当前状态，在空目录/独立实例验证，不能覆盖运行数据；新事件格式可能不兼容旧聊天程序。
已存在的数据仅做增量迁移；升级过程不得重跑首次密钥生成、清空数据库或把旧备份自动覆盖新聊天。具体流程见[升级、备份与回退](upgrading.md)。
