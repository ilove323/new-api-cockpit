# 从零全量部署 New API 与 Cockpit（发布镜像）

从 GitHub 正式 Release 下载配套安装包，直接拉取该版本的预构建镜像，完成全部组件、数据库、Nginx 和网页登录；目标服务器不下载应用源码、不编译镜像。

需要自行构建或部署开发提交时，使用独立的[源码构建部署](deployment-source.md)。

**默认方案：** `/root/new-api` 安装目录、IP + HTTP/80 访问、复用已安装的 Docker 和 Nginx。所有命令在目标服务器的 root Bash 终端执行；只有 SSH 隧道命令在访问电脑执行。

**版本要求：** 所选 [Release](https://github.com/ilove323/new-api-cockpit/releases) 必须同时提供 `new-api-cockpit-vX.Y.Z-deploy.tar.gz`、对应 `.sha256` 和该版本 GHCR 镜像。`v0.3.0` 没有完整安装包及聊天组件，不能套用本手册；没有满足条件的 Release 时须先[完成发版](releasing.md#发布流程)，不自动切换到源码构建。

本页用于**全新安装**。已有 New API 使用[接入手册](existing-installation.md)；已有本套实例使用[升级手册](upgrading.md#全量安装实例的更新)。发现已有配置或业务数据时，不执行首次初始化。

## 部署流程

按第 1～9 节顺序执行，每一步通过“检查点”再继续。第 10 节为可选业务配置，第 11～12 节用于备份和排错。

| 阶段 | 操作 | 完成标志 |
|---|---|---|
| [准备](#1-准备部署参数) | 确认连接、资源、端口与安装目录 | 目标正确，依赖可用，无冲突 |
| [首次配置](#4-下载安装包并生成首次配置) | 下载同版本安装包，生成本实例配置 | 配置生成成功，密钥未外泄 |
| [基础组件](#5-启动基础组件) | 启动 PostgreSQL、两套 Redis、New API | 四个服务健康 |
| [数据库与账号](#6-授权源库并创建管理员) | 授权源库，初始化管理员 | 授权成功，管理员已创建 |
| [Cockpit](#7-启动全部-cockpit-组件并完成迁移) | 启动网页、图片、聊天三个请求池 | 七个服务健康，迁移齐全 |
| [网页入口](#8-配置-nginx-统一入口) | 屏蔽欢迎站，配置 Nginx | 语法检查通过，入口指向应用 |
| [登录](#9-登录与验收) | 从实际使用地址进入两套系统 | New API 与 Cockpit 均可登录 |

代码块中的括号用于隔离 shell 设置；`set -euo pipefail` 使当前步骤遇错即停，不会接着执行后面的命令。失败时按错误恢复当前步骤，不从头重建实例。

## 1. 准备部署参数

开始前确定以下信息。`<尖括号>` 表示需要替换的参数，不应原样执行。

| 参数 | 默认值或填写要求 |
|---|---|
| Release 标签 | 选择包含完整安装包的正式版本，填写第 4 节的 `RELEASE_TAG`；不用 `latest` |
| SSH 连接 | 目标地址、用户、端口；使用跳板机时还需跳板地址、用户及两跳主机指纹 |
| 安装目录 | `/root/new-api`，首次安装使用空目录；安装文件、配置、应用数据和备份均保存在这里 |
| 访问入口 | 默认 `http://<服务器IP>`；也可通过 SSH 隧道访问 `http://localhost:24444` |
| HTTPS | 仅在采用 HTTPS 时准备访问名及匹配的有效证书、私钥，见第 8 节 |
| 管理员 | 准备用户名和符合 New API 策略的密码，第 6 节交互输入；不预设默认密码 |
| Docker 子网 | 默认 `172.28.241.0/24`，必须与主机路由、VPN 和现有 Docker 网络不重叠 |
| 资源 | 建议至少 8 GiB 内存、30 GiB 可用磁盘；聊天总存储额度默认 10 GiB |
| 上游与可选服务 | 供应商地址、KEY、模型 ID/协议/分组；搜索、通知参数可在安装完成后配置 |

HTTP 会明文传输密码和会话，仅用于可信隔离内网；非可信网络使用 HTTPS，或通过 SSH 隧道访问。系统 Nginx 的生效配置及 Docker 引擎存储仍保留系统标准位置。

## 2. 了解组件与端口

[完整 Compose 模板](../deploy/full-stack/compose.yml)包含七个服务，默认全部启用，无需 chat profile。

| 服务 | 用途 | 宿主机端口 |
|---|---|---|
| `postgres` | `new_api` 源库、`new_api_cockpit` 监控库 | 不发布 |
| `redis` | New API 缓存，独立认证 | 不发布 |
| `image-studio-receipts` | 生图去重回执，独立认证、AOF always、noeviction | 不发布 |
| `new-api` | 网关、账号、渠道、KEY、原始日志 | `127.0.0.1:3000` |
| `statistics` | Cockpit 网页/API、统计、管理、迁移和调度 | `127.0.0.1:8091` |
| `image-studio` | 生图/改图请求池 | `127.0.0.1:8092` |
| `chat` | 私有聊天、文件、Skills / Memory、受控联网 | `127.0.0.1:8093` |

Nginx 是统一入口：New API 使用 `/`、`/api/`、`/v1/`、`/sign-in`；Cockpit 使用 `/cockpit/`。图片的两个付费端点及聊天 API 单独转发到对应请求池，其余页面和接口进入网页池。

数据库角色分工如下，不能混用：

- `newapi_owner`：拥有源库，New API 使用它创建和维护业务表。
- `cockpit_reader`：仅查询源库和执行受限 PAT 补建函数，不授予业务表 `UPDATE`。
- `cockpit_monitor`：拥有独立监控库，Cockpit 使用它执行迁移、保存审计与配置。
- 聊天池不接收数据库、通知或 New API 会话签名密钥，仅挂载本实例的聊天数据。

## 3. 检查目标服务器与依赖

先确认当前终端已到目标服务器，而不只是跳板机：

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
docker network ls -q | xargs -r docker network inspect \
  --format '{{.Name}} {{range .IPAM.Config}}{{.Subnet}} {{end}}'
nginx -v
nginx -t
```

**检查点：**

- 当前为正确服务器的 root，安装目录为空，内存与磁盘足够。
- `3000/8091/8092/8093` 可用；已有 Nginx 占用 80 属正常情况，保留并在第 8 节配置。
- Docker 可用，Compose plugin 为 2.27 或更新版本，Nginx 原配置通过语法检查。
- 后端子网不冲突。不要通过停止其他业务、删除容器或改动现有数据库来腾位置。

缺少依赖时先补齐，再继续：

- Docker：按目标发行版使用 [Engine 安装步骤](https://docs.docker.com/engine/install/)和 [Compose plugin 安装步骤](https://docs.docker.com/compose/install/linux/)。已安装则复用，不替换全机镜像源。
- Nginx：仅在未安装时通过系统仓库安装；RPM 系统使用 `dnf install nginx` / `yum install nginx`，Debian/Ubuntu 使用 `apt-get update && apt-get install nginx`。
- Bash、tar、gzip、curl、sha256sum 和 Python 3.6+：使用系统仓库补齐。RHEL 8 可使用已有 `/usr/libexec/platform-python`，无需替换系统 Python。Cockpit 容器内部使用 Python 3.12。
- Docker 服务未运行时，在确认不影响现有业务后启动。

本模板固定 New API 为 [`v1.0.0-rc.40`](https://github.com/QuantumNous/new-api/releases/tag/v1.0.0-rc.40)，PostgreSQL 为 16，Redis 为 7。不要将 New API 改为 `latest`；其他版本先核对[兼容范围](compatibility.md)。

## 4. 下载安装包并生成首次配置

### 4.1 选择 Release、下载并校验

在所选 Release 的 Assets 中确认安装包、校验文件和对应镜像均已发布，再将下面的 `vX.Y.Z` 替换为实际标签。以下代码块**仅用于空目录的首次安装**：

```bash
(
set -euo pipefail
umask 077
RELEASE_TAG='vX.Y.Z'
if ! [[ "$RELEASE_TAG" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  echo '请将 RELEASE_TAG 改为已发布的正式标签。' >&2
  exit 1
fi
if [ -L /root/new-api ]; then
  echo '安装目录不能是软链接。' >&2
  exit 1
fi
if [ -d /root/new-api ] && [ -n "$(find /root/new-api -mindepth 1 -maxdepth 1 -print -quit)" ]; then
  echo '/root/new-api 非空，请先确认是继续安装、接入还是升级。' >&2
  exit 1
fi
install -d -m 700 /root/new-api /root/new-api/releases
cd /root/new-api/releases
ARCHIVE="new-api-cockpit-${RELEASE_TAG}-deploy.tar.gz"
BASE="https://github.com/ilove323/new-api-cockpit/releases/download/${RELEASE_TAG}"
curl --fail --location --silent --show-error --proto '=https' --tlsv1.2 -o "$ARCHIVE" "$BASE/$ARCHIVE"
curl --fail --location --silent --show-error --proto '=https' --tlsv1.2 -o "$ARCHIVE.sha256" "$BASE/$ARCHIVE.sha256"
sha256sum -c "$ARCHIVE.sha256"
tar --extract --gzip --file "$ARCHIVE" --directory /root/new-api --no-same-owner
cd /root/new-api
cp install/compose.yml docker-compose.yml
printf '%s\n' "$RELEASE_TAG" > release-tag.txt
chmod 600 docker-compose.yml release-tag.txt
)
```

**检查点：** 校验输出 `OK`，根目录有 `docker-compose.yml`、`release-tag.txt`，`install/` 中有初始化脚本、SQL、Nginx 片段、`release.env`、`release.json` 和 `migrations.txt`。下载或校验失败时不会解包或执行脚本，不改成 `git clone` 重试。

`release.env` 固定该 Release 对应的 `COCKPIT_IMAGE=ghcr.io/ilove323/new-api-cockpit:X.Y.Z`。后续使用根目录 Compose，不在 `install/` 中另起一套项目。

### 4.2 生成私有配置

默认使用 HTTP；采用 HTTPS 时，把下面唯一一条初始化命令的 `--scheme http` 改为 `--scheme https`。子网冲突时在同一条命令增加 `--subnet <空闲私网/24>`。

```bash
(
set -euo pipefail
cd /root/new-api
PYTHON=$(command -v python3 || true)
if [ -z "$PYTHON" ]; then PYTHON=/usr/libexec/platform-python; fi
"$PYTHON" -c 'import sys; assert sys.version_info >= (3,6)'
"$PYTHON" install/init-config.py --root /root/new-api --scheme http
cat install/release.env >> .env
chmod 600 .env
)
```

**检查点：** 输出包含 `"configuration_created": true`。程序已生成稳定实例 ID、不同的数据库/Redis 密码、New API 持久会话与加密密钥、通知加密密钥；无需复制或打印这些内容。

程序拒绝覆盖现有配置或初始化已有数据。若中断，先核对已经生成的文件，不删除 `data` 或 `config` 后重跑。

```text
/root/new-api/
  install/                 同一 Release 的公开安装文件，无应用源码
  release-tag.txt           已选择的正式标签
  docker-compose.yml       七服务配置
  .env                     Compose 网络/镜像参数，无管理员密码
  config/
    postgres.env           首次建库配置
    new-api.env            源库/缓存/持久会话与加密密钥
    web.env image.env      查询、监控与图片回执配置
    chat.env               仅聊天配置和内部 New API 地址
    *-health.env *.conf     Redis 认证与持久化配置
    *-capabilities.json    能力白名单，初始为空
    instance.json nginx/
  data/                    postgres / redis / image-studio-receipts / new-api / chat
  logs/new-api/
  backups/ releases/
```

配置目录权限为 `0700`，私有环境文件为 `0600`。聊天目录属主为 `10001:10001`；本模板固定 Redis 7 Alpine 的 `999:1000`，Redis 配置权限为 `0400`。
更换 Redis 镜像时需同步核对 UID/GID，不用 `chmod 777`。聊天目录须使用本机 POSIX 文件系统，不使用共享 NAS/NFS。

## 5. 启动基础组件

```bash
(
set -euo pipefail
cd /root/new-api
docker compose config -q
docker compose pull
docker compose up -d --no-build --pull never --wait --wait-timeout 180 postgres redis image-studio-receipts new-api
docker compose ps
)
```

**检查点：** `postgres`、`redis`、`image-studio-receipts`、`new-api` 四个服务均为运行且健康。三个 Cockpit 请求池使用 `install/release.env` 中固定的同一个 GHCR 镜像；Compose 没有 `build` 配置，不会本地编译。

首次启动时，PostgreSQL 自动运行[建库脚本](../deploy/full-stack/postgres-init.sql)，创建三个业务角色和两个数据库；随后 New API 创建源库业务表。这不需要手工清库或导入整库 SQL。
已有 PostgreSQL 数据目录不会重复执行建库脚本；连接异常应排查角色、库和配置，不通过删除数据让脚本重跑。

镜像拉取失败时，检查网络、镜像架构和仓库权限；GHCR 非公开包需使用有读取权限的账号安全登录。不要改用 `latest` 或另一版本。基础镜像源需要调整时，只改本实例的 `.env` 参数，保持版本和架构一致。
离线安装可在可信联网机器拉取相同版本、相同架构的全部预构建镜像，经校验后 `docker save` / `docker load` 导入，随后执行上面的 `up --no-build --pull never`；无需准备编译依赖。

## 6. 授权源库并创建管理员

### 6.1 授予 Cockpit 最小权限

确认第 5 节健康检查通过后执行：

```bash
(
set -euo pipefail
cd /root/new-api
{
  cat install/source_pat_function.sql
  cat install/source-grants.sql
} | docker compose exec -T postgres psql -X -v ON_ERROR_STOP=1 -U newapi_owner -d new_api
)
```

**检查点：** 输出包含 `CREATE FUNCTION`、`REVOKE` 和 `GRANT`，没有 `ERROR`。该步骤安装受限 PAT 函数并授权查询，不修改账号密码、额度或 KEY，也不授予业务表 `UPDATE`。
容器内本地 socket 不需要在命令里传密码；数据库 TCP 端口不对宿主机或外网发布。

### 6.2 交互创建管理员

在带 TTY 的服务器交互终端执行，按提示输入准备好的用户名和两次密码：

```bash
(
set -euo pipefail
cd /root/new-api
PYTHON=$(command -v python3 || true)
if [ -z "$PYTHON" ]; then PYTHON=/usr/libexec/platform-python; fi
"$PYTHON" install/bootstrap-admin.py
)
```

**检查点：** 输出包含 `"official_administrator_initialized": true`。密码输入无回显，不会作为明文保存在脚本、命令参数或配置中。
程序通过本机官方 `/api/setup` 接口初始化；已有 root 账号时会停止，应使用原账号登录，而不是重新创建。

在开放 Nginx 入口前完成此步骤。Cockpit 与 New API 共用账号，不需要再创建第二套管理员。

## 7. 启动全部 Cockpit 组件并完成迁移

```bash
(
set -euo pipefail
cd /root/new-api
docker compose up -d --no-build --pull never --wait --wait-timeout 180 statistics image-studio chat
docker compose ps
docker compose exec -T postgres psql -X -v ON_ERROR_STOP=1 -U cockpit_monitor -d new_api_cockpit \
  -c 'SELECT version FROM schema_migrations ORDER BY version;'
)
```

**检查点：** 七个服务均运行且健康；迁移查询返回的文件名与同一安装包的 `install/migrations.txt` 逐项一致，当前版本为 `001`～`011`。

网页池启动入口自动执行缺失的监控库迁移，失败则不会启动网页进程。图片池在网页池健康后启动；图片和聊天角色不执行迁移或调度。
源库与监控库分开，不能把监控迁移运行到 `new_api` 源库，也不能删除或重新编号已有迁移。

## 8. 配置 Nginx 统一入口

默认 HTTP 模板使用 80 的 `default_server`，**不固定 `server_name`**，可通过服务器 IP、localhost 或 SSH 转发访问。已有其他业务域名站点保留，按各自的匹配规则运行。

### 8.1 备份并定位欢迎站

```bash
(
set -euo pipefail
umask 077
cd /root/new-api
BACKUP="backups/nginx-$(date -u +%Y%m%dT%H%M%SZ)"
install -d -m 700 "$BACKUP"
tar -czf "$BACKUP/before.tar.gz" -C /etc nginx
systemctl is-active nginx > "$BACKUP/was-active.txt" || true
systemctl is-enabled nginx > "$BACKUP/was-enabled.txt" || true
if command -v getenforce >/dev/null 2>&1 && [ "$(getenforce)" != Disabled ]; then
  getsebool httpd_can_network_connect > "$BACKUP/selinux-before.txt"
fi
nginx -T > "$BACKUP/effective.conf" 2> "$BACKUP/check.log"
printf 'Nginx 备份目录：/root/new-api/%s\n' "$BACKUP"
grep -nE '^# configuration file |listen .*80|server_name|root .*html' "$BACKUP/effective.conf" || true
)
```

根据输出定位发行版欢迎站，只处理确认属于默认欢迎页的站点：

- 在 `/etc/nginx/nginx.conf` 中：删除或逐行注释其**完整 `server { ... }` 块**，包括嵌套 location；保留外围 `http`、`events` 和其他业务配置。
- 在 `/etc/nginx/conf.d/default.conf` 等独立文件中：将该文件移出 `conf.d`，保存到刚输出的备份目录。不要只改成另一个仍以 `.conf` 结尾的文件名。
- 检查 IPv4、IPv6 两种监听；同一监听地址只能有一个 `default_server`。有明确业务用途的其他默认站不能当欢迎页删除，需先确定站点分配。

**检查点：** 欢迎站已停用，其他业务站点未被改动。仅删除新站的 `server_name` 不能解决旧默认站抢占请求的问题。

### 8.2 准备配置副本

```bash
(
set -euo pipefail
cd /root/new-api
install -m 644 install/nginx.conf.example config/nginx/new-api-cockpit-locations.inc
install -m 644 install/nginx-http.conf config/nginx/new-api.conf
)
```

默认 HTTP 方案无需填写域名。主机禁用 IPv6 时，删除 `config/nginx/new-api.conf` 中的 `listen [::]:80 default_server;` 一行。

**仅 HTTPS 方案需要调整：** 第 4 节选择 `--scheme https`，在上述副本中将监听改为 443 SSL，配置与访问名匹配的证书和私钥，再确定是否另加 80 跳转；域名站按实际域名配置。IP 证书也必须覆盖访问 IP，不使用自签证书替代有效证书。

两种方案都保留位置规则和 `Host $http_host`，以便保留浏览器使用的端口；`X-Forwarded-Proto` 使用实际入口协议。不要将 `/root` 下的文件直接软链接给宿主 Nginx。

### 8.3 发布、检查并启用

以下步骤要求 `/etc/nginx/conf.d/new-api.conf` 未被其他站点使用；已存在时先核对归属和差异，不直接覆盖。

```bash
(
set -euo pipefail
cd /root/new-api
test ! -e /etc/nginx/conf.d/new-api.conf
install -m 644 config/nginx/new-api-cockpit-locations.inc /etc/nginx/new-api-cockpit-locations.inc
install -m 644 config/nginx/new-api.conf /etc/nginx/conf.d/new-api.conf
if command -v getenforce >/dev/null 2>&1 && [ "$(getenforce)" != Disabled ]; then
  restorecon -F /etc/nginx/nginx.conf /etc/nginx/conf.d/new-api.conf /etc/nginx/new-api-cockpit-locations.inc
  if getsebool httpd_can_network_connect | grep -q -- '--> off'; then
    setsebool -P httpd_can_network_connect on
  fi
fi
nginx -t
if systemctl is-active --quiet nginx; then
  systemctl reload nginx
else
  systemctl start nginx
fi
systemctl enable nginx docker
)
```

**检查点：** `nginx -t` 成功，Nginx 正常运行，Docker 与 Nginx 已设为开机启动。修改防火墙时只开放计划使用的 80/443 和来源，先核对实际接口及 zone；不开放数据库、Redis 或应用回环端口，不关闭防火墙或 SELinux。

如果语法检查失败，本步骤不会重载；修正实例副本并重新部署对应文件，再检查并重载。需要回退时，恢复本次修改的配置及欢迎站片段，并按记录恢复服务、自启、SELinux 和防火墙状态；不要覆盖其他站点的新变更。

以后切换 HTTP/HTTPS 时，备份并同步调整 `new-api.env` 的 `SESSION_COOKIE_SECURE` 和各 Cockpit 角色环境文件的 `COCKPIT_COOKIE_SECURE`，然后重建相应容器；不重新运行首次初始化。HTTP 为 `false`，HTTPS 为 `true`。

## 9. 登录与验收

### 直接访问

默认 HTTP 入口在访问电脑打开：

- New API：`http://<服务器IP>/sign-in`
- Cockpit：`http://<服务器IP>/cockpit/`

使用第 6 节创建的同一个账号登录两套系统；也可在 New API 登录后进入 Cockpit 复用会话。HTTPS 方案替换为实际 HTTPS 地址，两套系统保持同协议、同主机、同端口。

HTTP 方案可在目标服务器做一次入口检查：

```bash
curl --fail --silent --show-error http://127.0.0.1/api/status
```

应返回 New API JSON 且 `success` 为 `true`，不是 Nginx 欢迎页。服务器本机检查不能代替访问电脑的实际登录；`/` 是 New API 官方首页，看到产品首页并不代表仍是欢迎站。

### 通过 SSH 隧道访问

无法直连内网时，在**访问电脑**替换参数后执行，并保持该进程运行：

```bash
ssh -N -o BatchMode=yes -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 \
  -L 127.0.0.1:24444:127.0.0.1:80 <目标用户>@<目标地址>
```

有跳板机时增加 `-J <跳板用户>@<跳板地址>`，目标 SSH 非 22 端口时增加 `-p <目标SSH端口>`。目标若是跳板机回环上的 SSH 转发，则使用对应 `<目标用户>@127.0.0.1` 和目标端口；最后转发的是**目标服务器的 80**，不是跳板机的 80。

首次连接先用普通交互式 SSH，通过可信渠道核对并保存两跳指纹，再运行上面的 BatchMode 命令。遇到指纹冲突先核实，不关闭主机校验或盲目删除旧记录。

本机访问 `http://localhost:24444/sign-in` 与 `http://localhost:24444/cockpit/`。此示例适用于 HTTP/80 方案；HTTPS 隧道应转发 HTTPS 端口并保留与证书匹配的访问名。

### 完成标准

- 七个服务健康，监控库迁移与该 Release 的清单一致。
- 从实际使用地址能登录 New API 和 Cockpit，统计、管理、生图、聊天页面可打开。
- 应用只监听回环地址，数据库和 Redis 不发布端口，私有文件权限正确。

**满足以上条件即完成安装。** 验收不要求调用付费模型、发送通知或增减真实额度。文件功能如需检查，使用小型允许类型文本即可，不需要上传大文件；上限为 50 MiB，聊天 Nginx 规则为 `51264k`。

交付时记录访问地址、账号名称、Release 标签、镜像标识、数据库迁移、数据/配置/备份路径，以及未配置的可选项。不记录密码、真实 KEY 或完整环境变量；`docker compose config -q` 可用于校验，不输出完整私有配置。

镜像记录命令：

```bash
(
set -euo pipefail
umask 077
cd /root/new-api
docker compose images > releases/images.txt
docker compose images -q | sort -u | xargs -r docker image inspect \
  --format '{{.Id}} {{json .RepoTags}} {{json .RepoDigests}}' > releases/image-identities.txt
)
```

## 10. 按需接入模型、图片、搜索与通知

本节在安装与登录完成后执行。没有真实渠道和 KEY 时，系统可登录使用管理功能，但聊天/图片生成尚未就绪。

1. **渠道和模型：** 在 New API 添加实际供应商地址、KEY、精确模型 ID/映射、协议和分组；核对账号使用权限与额度，按站点要求设置注册策略。
2. **个人 KEY：** 在本人账号准备所需 KEY。生图工作站只使用本人已有、分组精确为 `GPT` 的 KEY；聊天也使用本人已有 KEY，不会自动创建或更改分组、额度。
3. **图片能力：** 按[协议说明](existing-installation.md#图片协议契约)和[能力示例](../image-studio-capabilities.example.json)填写 `config/image-studio-capabilities.json`。示例渠道 ID 和模型不是可直接工作的真实配置。
4. **聊天能力：** 按[模型能力配置](chat.md#模型能力)填写 `config/chat-capabilities.json`。协议、工具、reasoning 与压缩能力以渠道实际支持情况为准；模型还须在本人 KEY 的实际目录中。
5. **搜索：** 需要搜索时，在 `config/chat.env` 配置 SearXNG JSON 后端，参数见[搜索后端](chat.md#搜索后端)。未配置搜索不影响独立网页抓取/文本下载。
6. **通知与调度：** 在 Cockpit 配置通知、预算、监控和定时配额。只创建计划实际启用的规则；测试通知和真实模型调用会产生外部操作或费用，应单独安排。

初始能力文件是**空白名单**，不是通用能力模板。配置文件原子替换后，需要重建使用它们的容器，避免 bind mount 仍读取旧文件。
在没有在途生成请求的维护窗口执行：

```bash
(
set -euo pipefail
cd /root/new-api
docker compose up -d --no-build --pull never --no-deps --force-recreate --wait --wait-timeout 180 statistics image-studio chat
)
```

网页与图片池使用同一份图片配置，聊天池只接收聊天配置。实际生成、编辑、搜索或通知是否可用，以各项单独验收结果为准，不能仅凭容器健康判断。

## 11. 备份与后续维护

安装完成后安排异机备份；实例目录里的副本不能防止整盘丢失。至少保留：

- 两个 PostgreSQL 数据库的一致性备份，以及角色和权限。
- 完整 `config`、`.env`、Compose、Release 标签/元信息和镜像标识，尤其是 New API 的持久密钥与通知加密密钥。
- 停止聊天写入后的一致 `data/chat`，包括事件、manifest、快照和文件，保留原 instance ID。
- 两套 Redis 的持久数据，尤其是防重复派发的图片回执。
- Nginx 修改前后的配置、服务状态和 SELinux/防火墙变更记录。

运行中的 PostgreSQL 使用 `pg_dump` / `pg_dumpall`，不能直接打包数据目录作为一致备份。恢复前停止写入并保留当前状态，先在空目录或独立实例验证；恢复旧备份会丢失备份后的新记录。
升级使用增量迁移，不重新生成密钥、不重复建库、不清空聊天。完整实例的更新和数据库备份命令见[升级与回退](upgrading.md#全量安装实例的更新)。

## 12. 常见问题与继续位置

先在 `/root/new-api` 查看 `docker compose ps`，再查看故障服务的 `docker compose logs --tail=60 <服务名>`。日志可能含业务信息，分享前脱敏；不要为了排错打印全部环境变量。

| 现象 | 检查与处理 |
|---|---|
| Release 附件不存在或校验失败 | 确认标签、附件名、发布状态与下载完整性；保留现场，只重新下载失败文件，不解包错误文件、不运行初始化 |
| 镜像拉取失败 | 核对版本、网络、架构、仓库权限和受信任镜像来源；修复后继续第 5 节，不重做第 4 节 |
| PostgreSQL 或 New API 不健康 | 查看对应服务日志，核对角色、库、挂载和连接；首次建库只对空数据目录生效，不删除数据卷 |
| 管理员初始化停止 | 检查是否交互终端、是否已有 root、密码是否满足版本策略；已有账号用原账号登录，不重置数据库 |
| 网页池迁移失败 | 查看 `statistics` 日志，核对监控库连接与权限；修复后继续第 7 节，由网页池补做缺失迁移并启动其余请求池 |
| `duplicate default server` | 第 8 节还有未屏蔽的默认监听，分别检查 IPv4/IPv6 和 `nginx.conf`、`conf.d`，修正后重新 `nginx -t` |
| IPv6 监听报错 | 主机确实未启用 IPv6 时，移除实例副本中的 `[::]:80` 监听，重新发布该文件；保留 IPv4 |
| 仍是 Nginx 欢迎页 | 核对当前生效配置和重载结果、80 监听者、SSH 最终目标；用 `/api/status` 区分应用首页与欢迎页 |
| `502 Bad Gateway` | 核对对应请求池健康状态、回环端口、Nginx upstream 与 SELinux 代理连接权限 |
| Cockpit 登录循环或来源校验失败 | 两套系统须同源；保留 `Host $http_host` 中的端口和正确协议，HTTP 的 Cookie Secure 为 `false`，HTTPS 为 `true` |
| 聊天池不健康 | 核对数据目录属主 `10001:10001`、权限 `0700`、磁盘空间、稳定 instance ID 与能力文件格式，不改成 `777` |
| 模型/生图目录为空 | 继续第 10 节；核对本人 KEY、分组、上游目录与能力白名单，初始空配置不是安装失败 |
| 上传返回 `413` | 应用和 Nginx 都需使用对应版本；检查聊天 location 的 `51264k` 已生效，不放大所有 API |
| SSH 主机指纹校验失败 | 核对跳板和目标两跳指纹，先交互建立可信记录，再运行隧道；不使用 `StrictHostKeyChecking=no` 绕过 |
