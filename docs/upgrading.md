# 升级、备份与回退

升级同时更新 `statistics` 页面/调度池和 `image-studio` 图片请求池；两者使用同一版本镜像。
独立的 `image-studio-receipts` Redis 保留原容器和持久卷。本文适用于源码或配套发布镜像。
当前运行结构见[架构说明](architecture.md)，参数与探活见[部署](deployment.md)。

## 升级前检查

- 记录当前 Git 提交、两个应用的镜像 ID/tag/digest、Compose 和私有 override；给旧镜像保留独立的回退标签，避免源码构建覆盖同名 tag。
- 备份独立监控库、`.env` 及 `NOTIFICATION_ENCRYPTION_KEY`，保存在仓库外的受限目录。
- 核对 New API 查询账号能读取[兼容范围](compatibility.md)中的字段；配额操作需可访问管理 API。
- 确认 New API 支持当前登录协议，浏览器入口同源，后台内部地址可用，见[登录与会话](authentication.md)。
- 更新应用镜像时同时使用配套 Compose，不把当前源码模板与不包含该实现的发布镜像混用。
- 备份图片协议文件，确认回执 Redis 健康且持久卷仍在。缺少图片池时，先按[图片池部署](deployment.md#生图工作站请求池)准备配置、权限与回执服务。
- 本文只替换两个应用服务，不对 New API、PostgreSQL 或 Redis 项目执行 `down`，不清理回执或业务数据。

备份监控库（示例库名为 `new_api_cockpit`，应按自己的配置核对）：

```bash
umask 077
docker exec <PostgreSQL容器名> sh -lc \
  'pg_dump -U "$POSTGRES_USER" -Fc new_api_cockpit' > /安全备份目录/monitor.dump

docker exec -i <PostgreSQL容器名> pg_restore -l \
  < /安全备份目录/monitor.dump > /安全备份目录/monitor.contents.txt
```

确认备份非空并能列出内容，再继续升级。New API 原库按其自身备份策略单独备份，
不要将监控库恢复操作用于原库。通知加密密钥与数据库备份缺一不可。

保留现有数据库、角色、`.env`、连接串与 Compose 项目名。
需要固定项目名时使用 `COMPOSE_PROJECT_NAME` 或 `docker compose -p <现有项目名>`。
保持 `statistics`、`image-studio` 和 `image-studio-receipts` 的服务名与项目名，避免产生另一组容器或新的回执卷。

## 准备与切换

先准备新镜像，再停止应用，避免停机期间等待构建或下载。保留 `.env`、端口、网络、协议文件和私有 override，
不要直接覆盖环境参数。下面两种方式选一种，所有命令都在同一个实际 Compose 项目中执行。

### 源码构建

```bash
docker compose build statistics image-studio
docker compose stop statistics image-studio
docker compose up -d --no-build --no-deps --pull never statistics image-studio
docker compose ps
docker compose logs --tail=100 statistics image-studio
```

### 发布镜像

下载目标版本的配套配置，将 `IMAGE_TAG` 改为该正式版本后拉取镜像：

```bash
docker compose -f compose.release.yml pull statistics image-studio
docker compose -f compose.release.yml stop statistics image-studio
docker compose -f compose.release.yml up -d --no-deps --pull never statistics image-studio
docker compose -f compose.release.yml ps
docker compose -f compose.release.yml logs --tail=100 statistics image-studio
```

私有 override 必须在构建、停止和启动命令中始终使用；固定了镜像的 override 也要同步更新两个应用服务。
`--no-deps` 保留已运行的回执 Redis，不用于尚未初始化回执服务的首次部署。

停止前确认同一监控库没有其他应用实例正在发起额度操作。Compose 按服务配置排空请求：
网页池默认 90 秒，图片池 360 秒；不要给图片池套用网页池的停止窗口。
已派发的模型请求不会在升级后续跑或重发；结果不明确时先核对 New API 日志。

## 数据库迁移

`MONITOR_DATABASE_URL` 必须指向独立监控库。
网页池入口在启动前应用 `migrations/` 中尚未执行的脚本，按编号顺序处理；图片池不执行迁移。
`schema_migrations` 保存文件名，已登记的脚本不会重复执行；当前结构由 `001`～`011` 构建。
迁移在事务和数据库锁内运行，失败回滚且不登记版本，也不启动网页进程。

正常页面/API 请求不执行 DDL，仅在必要时检查迁移版本。
不要为了应用迁移清空预算、归档、通知配置、规则或执行记录；不要把 SQL 执行到 New API 原库。
迁移文件不得删除、改号或修改已发布脚本；新增结构使用下一编号。
通知公共状态以 `channel` 为主键，每个渠道有独立开关、版本和发送状态；邮件配置单独存表。
相关结构由 `011_notification_channels_email.sql` 创建。已有通知配置和凭据保留，新增的渠道默认关闭。
迁移不改 New API 原库、费用归档或用户额度。通知表涉及主键变更，回退前必须核对目标镜像与表结构的兼容性；
如需恢复监控库，先停机并评估备份之后新增记录的损失，步骤见[回退](#回退)。
当前表及用途见[数据库职责](architecture.md#数据库职责)。

非 Docker 部署使用统一入口，自动应用迁移和 Gunicorn 生命周期配置：

```bash
python -m new_api_cockpit.runtime gunicorn --bind 127.0.0.1:8000 new_api_cockpit.app:app
```

`python -m new_api_cockpit.runtime` 不带命令时仅执行迁移，不启动网页或定时器。
如使用自定义 Gunicorn 配置，须引入[定时器生命周期钩子](quota.md#部署与数据库)。

## 升级后验证

- 两个应用服务使用同一目标镜像且均健康，回执 Redis 保持健康；没有重复项目或意外启动的调度实例。
- 网页池 `http://127.0.0.1:8091/healthz` 返回 `200`，两种定时器就绪；图片池 `http://127.0.0.1:8092/healthz` 返回 `200`，`receipts_ready` 为 `true`。端口不同的安装按实际配置检查。
- 刷新页面并重新查询用量，确认日志倍率分行、Token 和消费金额。临时报表详情有效期为 15 分钟，不需要为页面更新清理数据库。
- 核对迁移版本、已归档月份、预算、通知配置和配额规则仍在；验证网页及 API 认证没有被放开。
- 配额执行结果在 `/cockpit/operations/` 核对；探活和普通页面访问不会发额度。
- 如需验证通知，管理员明确点击“发送测试消息”；不要用增减真实用户额度测试部署。
- 确认 Nginx 保留 `/cockpit/` 前缀、原域名和端口；图片生成/编辑两个精确路径走图片池，其他请求走网页池。管理页面与 API 文档要求管理员，三个模型工具页允许普通用户登录访问；业务 API 也可通过管理员 PAT 调用。
  未登录时可加载登录页和静态资源，业务请求仍受保护。外部调用的路径与确认头按 [API 参考](api.md) 核对。

首次渠道同步会进行一次完整渠道发现，之后只同步当前目录；显式发现命令及快照清理机制
见[性能机制](performance.md)。展示快照不是历史账单，不能用它替代归档验证。

重启会暂时停止两种定时器。余额启动安排未来的 10:00，不立即报警；
配额按已有到期窗口处理，错过周期及中断/不明确请求不补发、不自动重试。

## 回退

1. 按各自停止窗口排空并停止两个应用池，确认调度器不再领取任务；不停止或清理回执 Redis。
2. 恢复两个应用的原镜像、配套 Compose 和图片协议文件，保留 `.env`、加密密钥与现有回执卷。
3. 核对该应用与现有监控结构是否兼容。回退镜像不会自动降级数据库，不能仅凭新增表就认定回退兼容；需要回退结构时恢复该版本的匹配备份。
4. 确需数据库恢复时，只恢复目标监控库，并先评估备份后新增数据的损失；不得自动覆盖业务记录或动 New API 原库。
5. 使用原项目启动两个应用池，重新验证健康、认证、归档、配置和任务状态。已经成功的额度操作不会撤销，结果未知的图片请求不会重放。

本项目没有自动降级 SQL，也不自动重放结果不明确的额度请求。
遇到迁移或外部请求不确定状态，先保留证据、备份并人工核对，不以清库或反复重发作为修复手段。

源库 PAT 补建函数单独安装与授权，见[用户管理](users.md#安装补建函数与最小权限)。
升级不批量补建 PAT，不修改已有用户和 KEY。
