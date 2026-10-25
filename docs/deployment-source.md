# 源码构建部署

本页用于部署开发提交或自行构建镜像。使用正式版本的常规安装请走[发布镜像部署](deployment.md)，不需要本页的 Git、构建工具或应用源码。

两种方式不要混用：发布镜像配套同一 Release 的安装文件；源码镜像配套同一提交的配置与迁移。

## 新服务器全量安装

### 1. 检查依赖

先完成[准备参数](deployment.md#1-准备部署参数)、[组件与端口](deployment.md#2-了解组件与端口)和[服务器检查](deployment.md#3-检查目标服务器与依赖)。额外需要 Git、源码仓库访问权限以及镜像构建所需的网络与磁盘。

仍使用 `/root/new-api`、IP / HTTP 80 和现有 Nginx。以下步骤替代发布镜像手册的第 4～5 节，不能在已经运行的实例上作为首次安装执行。

### 2. 获取并固定源码

仅在空目录首次执行，默认检出 `develop`；部署指定提交时，在记录版本之前检出该提交：

```bash
(
set -euo pipefail
umask 077
if [ -L /root/new-api ]; then
  echo '安装目录不能是软链接。' >&2
  exit 1
fi
if [ -d /root/new-api ] && [ -n "$(find /root/new-api -mindepth 1 -maxdepth 1 -print -quit)" ]; then
  echo '/root/new-api 非空，请先确认继续位置或升级方案。' >&2
  exit 1
fi
install -d -m 700 /root/new-api
git clone --branch develop https://github.com/ilove323/new-api-cockpit.git /root/new-api/source
cd /root/new-api
# 指定提交时，在这里 git -C source checkout <提交或标签>。
git -C source rev-parse HEAD > source-commit.txt
install -d -m 700 install
for name in compose.yml init-config.py bootstrap-admin.py postgres-init.sql source-grants.sql nginx-http.conf; do
  cp "source/deploy/full-stack/$name" "install/$name"
done
cp source/nginx.conf.example source/image-studio-capabilities.example.json install/
cp source/sql/source_pat_function.sql install/
find source/src/new_api_cockpit/migrations -maxdepth 1 -type f -name '*.sql' -printf '%f\n' | sort > install/migrations.txt
cp install/compose.yml docker-compose.yml
cp source/deploy/full-stack/compose.source.yml compose.source.yml
chmod 600 source-commit.txt docker-compose.yml compose.source.yml
)
```

**检查点：** `source-commit.txt` 记录实际提交，`install/` 是该提交的安装文件；`compose.source.yml` 仅增加三个 Cockpit 池的构建配置。基础运行 Compose 不含构建步骤。

### 3. 生成本实例配置并固定本地镜像名

HTTP 使用下面命令；HTTPS 将唯一一条初始化命令改为 `--scheme https`。子网冲突时在该命令增加 `--subnet <空闲私网/24>`。

```bash
(
set -euo pipefail
cd /root/new-api
PYTHON=$(command -v python3 || true)
if [ -z "$PYTHON" ]; then PYTHON=/usr/libexec/platform-python; fi
"$PYTHON" -c 'import sys; assert sys.version_info >= (3,6)'
"$PYTHON" install/init-config.py --root /root/new-api --scheme http
IMAGE="new-api-cockpit:source-$(git -C source rev-parse --short=12 HEAD)"
printf 'COCKPIT_IMAGE=%s\n' "$IMAGE" >> .env
chmod 600 .env
)
```

**检查点：** 配置生成成功，`.env` 的 `COCKPIT_IMAGE` 是包含提交 ID 的本地镜像名，不是 GHCR 的正式版本。不要通过重复初始化来替换密钥。

### 4. 构建并启动基础组件

```bash
(
set -euo pipefail
cd /root/new-api
docker compose config -q
docker compose pull postgres redis image-studio-receipts new-api
docker compose -f docker-compose.yml -f compose.source.yml build statistics
docker compose up -d --no-build --pull never --wait --wait-timeout 180 postgres redis image-studio-receipts new-api
docker compose ps
)
```

三个请求池使用同一个本地镜像，因此只构建 `statistics` 一次。覆盖文件的构建上下文是实例根目录的 `./source`；所有运行命令仍使用根目录的 `docker-compose.yml`。
需要调整构建来源时，在 `.env` 设置 `PYTHON_IMAGE`、`NODE_IMAGE` 或 `PIP_INDEX_URL`，不要替换全机镜像源；镜像架构必须匹配目标服务器。

**检查点：** 本地镜像已构建，四个基础服务健康。

### 5. 完成数据库、Nginx 与登录

从发布镜像手册的[第 6 节：授权与管理员](deployment.md#6-授权源库并创建管理员)继续，依次完成全部 Cockpit 池、Nginx 和登录；这些步骤共用 `install/` 文件，无需再次下载 Release 或构建镜像。

迁移按 `install/migrations.txt` 核对。交付时记录 `source-commit.txt` 和本地镜像标识，而不是把开发提交当作正式 Release。模型、图片、搜索和通知仍按需配置。

## 接入已有 New API 的源码构建

此方式只安装 Cockpit，不接管已有 New API 或数据库。选择独立部署目录，例如 `/root/new-api/cockpit-source`，不要覆盖现有项目：

```bash
git clone https://github.com/ilove323/new-api-cockpit.git /root/new-api/cockpit-source
cd /root/new-api/cockpit-source
# 先检出需要部署的提交，并记录 git rev-parse HEAD。
cp .env.example .env
chmod 600 .env
```

按[已有系统接入](existing-installation.md#配置与启动)中的参数表填写 `.env`，准备数据库权限、网络、图片能力文件及回执服务后执行：

```bash
docker compose up -d --build
docker compose ps
```

需要聊天时，先按[聊天配置](chat.md#配置与可选请求池)准备本地数据目录、稳定 instance ID 和能力文件，再启用对应 profile。源码镜像与这些配置必须来自同一提交。

## 源码实例的更新

- 先记录旧提交、Compose 与三个池的镜像，并保留独立回退标签；备份数据库、配置与完整聊天数据。
- 全量实例在 `source/` 检出经过核对的新提交，比较并合并安装文件及 `compose.source.yml`，不覆盖私有配置或重新生成密钥。
- 在 `.env` 选择包含新提交 ID 的独立 `COCKPIT_IMAGE`，用 `docker compose -f docker-compose.yml -f compose.source.yml build statistics` 预先构建；构建成功后才进入维护窗口。
- 按[全量实例更新](upgrading.md#全量安装实例的更新)排空、备份并切换三个请求池，使用 `--no-build --pull never`，不从仓库拉取本地源码镜像。
- 仅接入已有 New API 的源码项目使用其根目录 Compose，先构建 `statistics image-studio`（启用聊天时也构建 `chat`），再按各自停止窗口切换应用服务。

更新不清库、不重放付费请求、不自动降级聊天事件格式。回退须先检查数据库与聊天数据的兼容性，不能只换回镜像就继续写新数据。

## 镜像内的 VS Code 安装包

镜像构建会自动从同一检出目录的 `vsix/` 源码打包插件；构建机器需要能拉取 Node 构建镜像及 npm 锁文件中的依赖，不要求宿主机安装 Node。运行镜像不包含 Node 或 node_modules。升级镜像后，在“文档 → 辅助工具”下载当前镜像内的版本；无需另放静态目录或改 Nginx。插件或依赖构建失败会阻止镜像生成。完整边界见[辅助工具](tools.md)。
