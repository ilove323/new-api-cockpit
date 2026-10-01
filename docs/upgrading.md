# 升级、备份与回退

本文描述当前单容器应用的升级操作，适用于源码或匹配的发布镜像。
当前运行结构见[架构说明](architecture.md)，参数与探活见[部署](deployment.md)。

## 升级前检查

- 记录当前 Git 提交、镜像 tag/digest、Compose 和私有 override；确保能恢复原应用。
- 备份独立监控库、`.env` 及 `NOTIFICATION_ENCRYPTION_KEY`，保存在仓库外的受限目录。
- 核对 New API 只读账号能读取[兼容范围](compatibility.md)中的字段；配额操作需可访问管理 API。
- 更新应用镜像时同时使用配套 Compose，不把当前源码模板与不包含该实现的发布镜像混用。
- 本文只操作统计项目；不对 New API、PostgreSQL、Redis 项目执行 `down` 或清空数据库。

备份监控库（示例库名为 `new_api_statistics`，应按自己的配置核对）：

```bash
umask 077
docker exec <PostgreSQL容器名> sh -lc \
  'pg_dump -U "$POSTGRES_USER" -Fc new_api_statistics' > /安全备份目录/monitor.dump

docker exec -i <PostgreSQL容器名> pg_restore -l \
  < /安全备份目录/monitor.dump > /安全备份目录/monitor.contents.txt
```

确认备份非空并能列出内容，再继续升级。New API 原库按其自身备份策略单独备份，
不要将监控库恢复操作用于原库。通知加密密钥与数据库备份缺一不可。

## 停止与更新

先在统计项目目录停止当前应用，避免升级中仍有额度请求发起：

```bash
docker compose stop -t 90 statistics
```

**调度互斥检查**：若已有部署还存在 `balance-worker`、`quota-worker` 或独立后台进程，
必须用其实际配置停止并确认退出，再使用当前模板；私有 override 中也应去掉这些服务。
有 `quota-schedules` profile 的部署，停止时须包含该 profile，不能遗漏后台服务。
当前应用只运行一个 `statistics` 容器，不能让其他调度器与它并行执行。

保留 `.env`、监听端口、Docker 网络和监控数据，更新到目标源码或配套发布配置。
不要直接覆盖私有参数，也不应重建 New API 服务。

### 源码构建

```bash
docker compose up -d --build statistics
docker compose logs --tail=100 statistics
```

### 发布镜像

将 `IMAGE_TAG` 设为目标正式版本，并下载该版本附带的配置：

```bash
docker compose -f compose.release.yml pull statistics
docker compose -f compose.release.yml up -d statistics
docker compose -f compose.release.yml logs --tail=100 statistics
```

两种方式选一种；停止和启动必须使用相同的实际 Compose 项目/配置。
如果使用发布配置，前面的停止命令也需加 `-f compose.release.yml`。

## 数据库迁移

`MONITOR_DATABASE_URL` 必须指向独立监控库。
Docker entrypoint 在启动网页前应用 `migrations/` 中尚未执行的脚本，按编号顺序处理。
`schema_migrations` 保存文件名，已登记的脚本不会重复执行；当前结构由 `001`～`008` 构建。
迁移在事务和数据库锁内运行，失败回滚且不登记版本，也不启动网页进程。

正常页面/API 请求不执行 DDL，仅在必要时检查迁移版本。
不要为了应用迁移清空预算、归档、通知配置、规则或执行记录；不要把 SQL 执行到 New API 原库。
迁移文件不得删除、改号或修改已发布脚本；新增结构使用下一编号。
当前表及用途见[数据库职责](architecture.md#数据库职责)。

非 Docker 部署使用统一入口，自动应用迁移和 Gunicorn 生命周期配置：

```bash
python -m new_api_statistics.runtime gunicorn --bind 127.0.0.1:8000 new_api_statistics.app:app
```

`python -m new_api_statistics.runtime` 不带命令时仅执行迁移，不启动网页或定时器。
如使用自定义 Gunicorn 配置，须引入[定时器生命周期钩子](quota.md#部署与数据库)。

## 升级后验证

- `docker compose ps` 仅列出 `statistics` 一个统计应用服务；使用实际配置检查遗留容器也已停止。
- 根据[运行状态检查](deployment.md#运行状态检查)确认 `/healthz` 返回 `200`，两种定时器就绪。
- 核对迁移版本、已归档月份、预算、通知配置和配额规则仍在；验证网页及 API 认证没有被放开。
- 配额执行结果在 `/quota/` 的“执行记录”核对；探活和普通页面访问不会发额度。
- 如需验证通知，管理员明确点击“发送测试消息”；不要用增减真实用户额度测试部署。
- 页面和路由未变时不需修改 Nginx；入口配置仍应包含 `/statistics/` 和 `/quota/`。

首次渠道同步会进行一次完整渠道发现，之后只同步当前目录；显式发现命令及快照清理机制
见[性能机制](performance.md)。展示快照不是历史账单，不能用它替代归档验证。

重启会暂时停止两种定时器。余额启动安排未来的 10:00，不立即报警；
配额按已有到期窗口处理，错过周期及中断/不明确请求不补发、不自动重试。

## 回退

1. 停止当前应用，确认调度器不再领取任务。
2. 恢复已记录的原镜像与配套 Compose，保留 `.env` 和加密密钥；只启动目标统计应用。
3. 核对该应用与现有监控结构是否兼容。回退镜像不会自动降级数据库，新增表通常无需删除。
4. 确需数据库恢复时，只恢复目标监控库，并先评估备份后新增数据的损失；不得自动覆盖业务记录或动 New API 原库。
5. 重新验证认证、归档、配置和任务状态；已经成功的额度操作不会因回退撤销。

本项目没有自动降级 SQL，也不自动重放结果不明确的额度请求。
遇到迁移或外部请求不确定状态，先保留证据、备份并人工核对，不以清库或反复重发作为修复手段。
