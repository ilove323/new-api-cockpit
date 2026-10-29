# 升级、备份与回退

升级同时更新 `statistics` 页面/调度池和 `image-studio` 图片请求池；启用聊天的实例还需更新 `chat` 池。所有 Cockpit 池使用同一版本镜像。
独立的 `image-studio-receipts` Redis 保留原容器和持久卷。本文适用于源码或配套发布镜像。
当前运行结构见[架构说明](architecture.md)，参数与探活见[部署](deployment.md)。

上述两池流程用于[接入已有 New API](existing-installation.md)的默认 Compose。
如果是 `/root/new-api` 的七服务全量实例，先按下一节处理；不要把源码目录的 Compose 或发布模板覆盖到实例根目录。

## 企业微信通知升级

从 v0.4.2 升级到 v0.4.3 会追加执行监控库迁移 `012_wecom_webhook.sql`；已部署相同迁移的开发版本不会重复执行。新渠道默认关闭，已有通知开关、凭据和余额数据保留。备份监控库和通知加密密钥后正常更新 Cockpit，随后在“报警渠道 · 全局”配置企业微信群机器人；不需要修改 New API、数据库/Redis 版本、源库权限、Nginx 或现有环境变量。已有监控库迁移不能手工删除或重编号。

## 全量安装实例的更新

本节仅适用于[全量部署手册](deployment.md)创建的七服务实例。命令在 `/root/new-api` 执行，而不是 `source/`。
保留实例根目录的 Compose、项目名、`.env`、整个 `config/`、instance ID 和数据目录；不能再次运行 `init-config.py`、管理员初始化或首次建库脚本。

1. 记录当前 Release 标签及三个 Cockpit 池的镜像，给旧镜像保留单独回退标签。预先核对两个数据库、角色/权限、私有配置及聊天的备份计划。
2. 选择已正式发布的目标版本，下载同版本安装包及校验文件到 `releases/` 的独立目录，校验后先解包到暂存目录，不覆盖正在使用的 `install/`。核对 Compose、角色环境文件、能力文件和迁移差异；预先拉取同版本 GHCR 镜像，不修改基础组件版本。源码实例的获取和构建另见[源码实例更新](deployment-source.md#源码实例的更新)。
3. 在明确的维护窗口排空图片、聊天及管理操作，再停止 `statistics image-studio chat`；不停止或清理 New API、PostgreSQL、两套 Redis。停止写入后备份整个 `data/chat` 和私有配置，并完成数据库 dump；下方示例备份必须非空且能列出内容。
4. 按核对后的差异更新公开安装文件与运行 Compose，将 `.env` 中唯一的 `COCKPIT_IMAGE` 更新为新版本，保留其他参数与密钥并记录新标签。仅用 `--no-build --pull never --no-deps` 重建 `statistics image-studio chat`。先等网页池自动迁移并健康，再启动图片和聊天池。不要运行无服务名的 `down` 或 `up --force-recreate` 波及基础组件。
5. 按[最小验收](deployment.md#9-登录与验收)核对登录、迁移和数据保留。聊天回退受本文末尾的事件格式兼容限制；不能用恢复旧备份无提示覆盖升级后新增数据。

数据库备份示例（`STAMP` 和备份目录在同一片段中建立）：

```bash
cd /root/new-api
umask 077
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
install -d -m 700 "backups/$STAMP"
docker compose exec -T postgres pg_dump -U newapi_owner -Fc new_api > "backups/$STAMP/new-api.dump"
docker compose exec -T postgres pg_dump -U cockpit_monitor -Fc new_api_cockpit > "backups/$STAMP/cockpit.dump"
docker compose exec -T postgres pg_dumpall -U postgres --roles-only > "backups/$STAMP/roles.sql"
test -s "backups/$STAMP/new-api.dump" && test -s "backups/$STAMP/cockpit.dump"
docker compose exec -T postgres pg_restore -l < "backups/$STAMP/new-api.dump" > "backups/$STAMP/new-api.contents.txt"
docker compose exec -T postgres pg_restore -l < "backups/$STAMP/cockpit.dump" > "backups/$STAMP/cockpit.contents.txt"
```

角色备份含密码哈希，必须与配置备份同样保护，不写入公共日志或提交到 Git。上面没有备份聊天、配置和 Redis，不能把数据库 dump 当作完整实例备份。
恢复监控库只影响监控库，不自动撤销已经发生的 New API 配额/KEY 操作或模型费用；恢复源库也必须另行评估业务影响并取得授权。

## 升级前检查

- 记录当前版本或 Git 提交、所有已启用 Cockpit 池的镜像 ID/tag/digest、Compose 和私有 override；给旧镜像保留独立的回退标签，避免源码构建覆盖同名 tag。
- 备份独立监控库、`.env` 及 `NOTIFICATION_ENCRYPTION_KEY`，保存在仓库外的受限目录。
- 核对 New API 查询账号能读取[兼容范围](compatibility.md)中的字段；配额操作需可访问管理 API。
- 确认 New API 支持当前登录协议，浏览器入口同源，后台内部地址可用，见[登录与会话](authentication.md)。
- 更新应用镜像时同时使用配套 Compose，不把当前源码模板与不包含该实现的发布镜像混用。
- 备份图片协议文件，确认回执 Redis 健康且持久卷仍在。缺少图片池时，先按[图片池部署](existing-installation.md#生图工作站请求池)准备配置、权限与回执服务。
- 本文只替换 Cockpit 应用服务，不对 New API、PostgreSQL 或 Redis 项目执行 `down`，不清理回执或业务数据。

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
保持 `statistics`、`image-studio`、已启用的 `chat` 和 `image-studio-receipts` 的服务名与项目名，避免产生另一组容器或新的回执卷。

## 准备与切换

Cockpit v0.4.3 随附 VS Code 插件仍为 0.4.2，插件功能不变。只更新 Cockpit 镜像，不需要修改 New API、PostgreSQL、Redis 或 Nginx。与 v0.4.2 相比新增的唯一监控库迁移为 `012_wecom_webhook.sql`，无新增源库权限。

v0.4.2 已将鹈鹕测试改为流式 SVG，生成最多 300 秒。只有尚未配好该功能的旧实例才需另行核对 [Nginx 示例](../nginx.conf.example)中 `/cockpit/api/intelligence/test` 的精确 location：读取超时 360 秒，关闭缓冲、缓存及 gzip。v0.4.3 升级本身不要求改动 Nginx，不应覆盖现有路由、端口或证书。渠道分流继续使用现有源库只读权限，不新建表、角色或定时任务。

插件从 v0.4.1 起可独立连接 New API，余额不再依赖 Cockpit MCP；已有连接和刷新等待时间保留。KEY 改组继续使用官方 PUT。

先准备新镜像，再停止应用，避免停机期间等待构建或下载。保留 `.env`、端口、网络、协议文件和私有 override，
不要直接覆盖环境参数。下面两种方式选一种，所有命令都在同一个实际 Compose 项目中执行。

源码项目的构建和切换步骤已移至[源码构建部署](deployment-source.md#源码实例的更新)。以下是发布镜像流程。

### 发布镜像

下载目标版本的配套配置，将 `IMAGE_TAG` 改为该正式版本后拉取镜像：

```bash
docker compose -f compose.release.yml pull statistics image-studio
docker compose -f compose.release.yml stop statistics image-studio
docker compose -f compose.release.yml up -d --no-deps --pull never statistics image-studio
docker compose -f compose.release.yml ps
docker compose -f compose.release.yml logs --tail=100 statistics image-studio
```

私有 override 必须在构建、停止和启动命令中始终使用；固定了镜像的 override 也要同步更新所有已启用的 Cockpit 池。
已启用可选聊天池时，以上命令须使用原有 `--profile chat` 和私有 override，并把 `chat` 加入服务列表。切换时先启动 `statistics` 并确认健康，再启动 `image-studio chat`；聊天数据与 instance ID 保持不变。
`--no-deps` 保留已运行的回执 Redis，不用于尚未初始化回执服务的首次部署。

停止前确认同一监控库没有其他应用实例正在发起额度操作。Compose 按服务配置排空请求：
网页池默认 90 秒，图片池 360 秒；不要给图片池套用网页池的停止窗口。
已派发的模型请求不会在升级后续跑或重发；结果不明确时先核对 New API 日志。

## 回执 Redis 的非 root 权限变更

本段仅在目标模板将回执进程改为 `999:1000`、原数据或配置仍归 root 时使用；普通应用升级不要重启 Redis。
先核对镜像的 Redis UID/GID、实际 Compose 挂载和属主，不要把本文路径套到另一套实例。

1. 在维护窗口排空并停止图片池，再停止**回执**服务；保留原项目名、原卷及所有 AOF 文件。禁止 `down -v` 或清空目录。
2. 在停止写入后备份整个回执目录/卷以及配置，保留原属主与权限，确认备份可读；不要只备份 AOF 的某一个文件。
3. 全量实例的绑定目录为 `data/image-studio-receipts`，配置为 `config/image-studio-receipts.conf`。确认都不是软链接，目录及既有文件归 `999:1000`，顶层目录为 `0700`，配置归同一用户且为 `0400`。不要将配置改为所有用户可读，也不要改写原密码。
4. 命名卷实例应先确认原卷名，只在停止的原卷内修正属主；不要创建新卷替换旧回执。卷内旧 AOF 的可读写权限也必须匹配运行用户。
5. 按新模板只启动回执服务，确认健康且能读取旧数据，再重新创建图片池进程。回执 Redis 重启后，旧图片进程会因 epoch 改变停止派发，不能只重启 Redis 而继续使用旧进程。
6. 权限调整失败时保持图片池停止，按记录恢复配置、属主和旧模板；不删除回执来“恢复”。

全量实例的缓存 `redis` 属于 New API 基础组件；若它也需要修正权限，须单独安排 New API 维护，不能顺带用回执服务的重启步骤处理。

## 管理员 PAT 的源库授权

此处不是监控库迁移。采用新式 `nap_` 管理员 PAT 时，在 **New API 源库**以表所有者授予
`user_access_tokens(token_hash,user_id,scopes,expires_at)` 的列级 SELECT，参见[源库最小读权限](existing-installation.md#源库最小读权限)。
全量安装配套 `source-grants.sql` 会在表存在时授权；若上次授权发生在 New API 创建该表之前，升级后还需再执行授权。
不授予整张令牌表的 SELECT 或写权限，不把用户 PAT 明文写进配置，也不因缺少权限回退到旧 PAT 数据库匹配。
源库权限不会由 Cockpit 的监控库迁移自动补齐。

## 数据库迁移

`MONITOR_DATABASE_URL` 必须指向独立监控库。
网页池入口在启动前应用 `migrations/` 中尚未执行的脚本，按编号顺序处理；图片池不执行迁移。
`schema_migrations` 保存文件名，已登记的脚本不会重复执行；当前开发代码的结构由 `001`～`012` 构建；正式版本以配套安装包的 `install/migrations.txt` 为准。
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

- 所有已启用 Cockpit 池使用同一目标镜像且均健康，回执 Redis 保持健康；没有重复项目或意外启动的调度实例。
- 网页池 `http://127.0.0.1:8091/healthz` 返回 `200`，两种定时器就绪；图片池 `http://127.0.0.1:8092/healthz` 返回 `200`，`receipts_ready` 为 `true`。已启用聊天池时，`http://127.0.0.1:8093/healthz` 也应返回 `200`，并验证聊天页可输入消息、附件可选、历史及输出下载可用。端口不同的安装按实际配置检查。
- 刷新页面并重新查询用量，确认日志倍率分行、Token 和消费金额。临时报表详情有效期为 15 分钟，不需要为页面更新清理数据库。
- 核对迁移版本、已归档月份、预算、通知配置和配额规则仍在；验证网页及 API 认证没有被放开。
- 配额执行结果在 `/cockpit/operations/` 核对；探活和普通页面访问不会发额度。
- 如需验证通知，管理员明确点击“发送测试消息”；不要用增减真实用户额度测试部署。
- 确认 Nginx 保留 `/cockpit/` 前缀、原域名和端口；图片生成/编辑两个精确路径走图片池，聊天 API（含 SSE、附件上传/下载）走聊天池，其他请求走网页池。管理页面与 API 文档要求管理员，三个模型工具页允许普通用户登录访问；聊天外的业务 API 也可通过管理员 PAT 调用。
  未登录时可加载登录页和静态资源，业务请求仍受保护。外部调用的路径与确认头按 [API 参考](api.md) 核对。

首次渠道同步会进行一次完整渠道发现，之后只同步当前目录；显式发现命令及快照清理机制
见[性能机制](performance.md)。展示快照不是历史账单，不能用它替代归档验证。

重启会暂时停止两种定时器。余额启动安排未来的 10:00，不立即报警；
配额按已有到期窗口处理，错过周期及中断/不明确请求不补发、不自动重试。

## 回退

1. 按各自停止窗口排空并停止所有已启用 Cockpit 池，确认调度器不再领取任务；不停止或清理回执 Redis。
2. 恢复所有已启用 Cockpit 池的原镜像、配套 Compose 和图片协议文件，保留 `.env`、加密密钥与现有回执卷。
3. 核对该应用与现有监控结构是否兼容。回退镜像不会自动降级数据库，不能仅凭新增表就认定回退兼容；需要回退结构时恢复该版本的匹配备份。
4. 确需数据库恢复时，只恢复目标监控库，并先评估备份后新增数据的损失；不得自动覆盖业务记录或动 New API 原库。
5. 使用原项目启动所有已启用 Cockpit 池，重新验证健康、认证、归档、配置和任务状态。已经成功的额度操作不会撤销，结果未知的图片请求不会重放。

本项目没有自动降级 SQL，也不自动重放结果不明确的额度请求。
遇到迁移或外部请求不确定状态，先保留证据、备份并人工核对，不以清库或反复重发作为修复手段。

源库 PAT 补建函数单独安装与授权，见[用户管理](users.md#安装补建函数与最小权限)。
升级不批量补建 PAT，不修改已有用户和 KEY。


## 可选聊天功能的升级与回退

本地新增代码不会自动升级已发布镜像。采用包含此功能的版本前，按[AI助手配置](chat.md)准备data目录；接入已有 New API 的可选聊天池需显式 chat profile，七服务全量实例不需要 profile；不需要新的SQL迁移。
保留`data/chat`和稳定instance ID，不随容器清理。升级前停止chat写入并一致备份；重建后分别验证每用户完整聊天列表、失败/中断历史、附件及个人设置仍在。
chat回退时先停止新提交、取消并收尾在途轮次，停chat服务并隐藏CHAT_ENABLED入口，保留data；旧版本不理解新schema时禁止继续写，不自动降级/清空。
模型请求断开或超时可能已计费，恢复备份或回退镜像不会撤销费用，也不得自动续跑。旧web/image池与既有监控迁移按原流程处理。

AI 助手多协议/Memory/压缩扩展保留旧日志读取，但新增事件和字段不能由原 v1 二进制直接读取。升级前一致备份整个 chat 数据（含原生连续状态、工作窗口、个人 manifest 与审计）；回退必须停写并使用兼容版本；如确需恢复升级前备份，应另行评估新增记录与删除状态，不能把更换镜像当作无损 schema 回滚。

### 附件删除与数据恢复

上传附件删除后，`control/` 中的删除标记和原件删除状态必须保留；历史继续显示文件名及“已删除”，不能再下载或引用。上下文缓存也不能绕过删除标记重新读取附件。

一般代码回退只恢复镜像、源码及配套配置，不恢复聊天数据、删除标记、回执或账单。不要为了回退页面恢复整个旧 `data/chat`：这会丢失新聊天，并可能使已经删除的附件重新出现。确需灾难恢复时，先停写、另备当前数据，再核对备份后的删除记录与新增事件；旧版本不能识别删除标记时，保持聊天关闭，不能直接接流量。

## 文档与 VS Code 插件

包含此功能的镜像把插件安装包一并发布，在“文档 → 辅助工具”下载，并从 VSIX 覆盖安装。页面显示的是当前镜像内的版本和校验值，不会自动安装到用户 VS Code。模型使用、辅助工具、API 文档为三个平级入口，旧 MCP 书签会转到辅助工具；API 文档仍限管理员。无新数据库迁移或 Nginx 规则。
