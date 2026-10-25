# New API：只修改 KEY 配置

此目录的补丁基于 QuantumNous/new-api **v1.0.0-rc.47**，提交
[`e835b1bfc43b565d625537830dbfb19328925134`](https://github.com/QuantumNous/new-api/tree/e835b1bfc43b565d625537830dbfb19328925134)。
它不是官方镜像已有的功能，也不是管理员跨用户 KEY 接口。

## 修复范围

旧接口 `PUT /api/token/` 接收整个 KEY 配置，其中的 `remain_quota` 是绝对值。
客户端先查再写仍可能覆盖期间发生的扣费。

补丁新增 **`PATCH /api/token/:id`**，由现有 `UserAuth` 验证身份，
新式 PAT 需要 `api_key:write`，只允许修改属于当前认证用户的 KEY。
Cockpit 管理其他用户 KEY 时，仍按原流程取得并使用所属用户的 PAT；没有新增管理员代管接口，也没有修改 PAT 创建、撤销或退役策略。

支持字段：`name`、`expired_time`、`model_limits_enabled`、`model_limits`、
`allow_ips`、`allow_user_quota_query`、`group`、`cross_group_retry`、`auto_groups`。
额度、已用量、无限额度、归属、密钥和状态字段直接拒绝；缺省字段不以零值覆盖。
分组沿用所属用户的可选组规则，非 `auto` 分组清除自动组设置。

服务端事务锁定目标 KEY，只更新配置列。
Redis 只更新配置，不清空或重建正在使用的额度哈希，因此未落库的预扣也不会被 SQL 旧值覆盖。
写入中断时，下次读取核对已提交的配置，不重复修改；缓存异常返回失败，不降级为完整 PUT。
新增路由沿用操作审计，不记录完整 KEY、PAT 或请求正文。没有数据库结构迁移。

**主动设置或增减 KEY 限额仍使用原接口，不在这次并发改组修复范围内。**
New API 自己的网页或其他客户端若仍使用完整 PUT，也不因安装补丁而自动改变行为。

## 应用补丁

在独立的 New API 源码检出目录中操作，不在站点的数据目录里执行：

```sh
git clone --branch v1.0.0-rc.47 --depth 1 https://github.com/QuantumNous/new-api.git new-api-patched
cd new-api-patched
test "$(git rev-parse HEAD)" = e835b1bfc43b565d625537830dbfb19328925134
git apply --check /path/to/new-api-cockpit/patches/new-api/token-config.patch
git apply /path/to/new-api-cockpit/patches/new-api/token-config.patch
```

随后按 New API 原有构建方式制作单独标记的镜像。不要把自建镜像标记为未修改的官方版本。

```sh
docker build -t new-api:1.0.0-rc.47-cockpit-config1 .
```

此处没有已发布的预构建修复镜像；不能只更新 Cockpit、继续使用原官方镜像，就声称服务端修复已生效。
升级其他 New API 版本时需要重新核对、移植并验证，不能跳过补丁冲突。

发布和部署时先备份配置及数据，再更新 New API，最后更新 Cockpit 和 VSIX。
新客户端遇到原服务端返回 404/405 时提示需要配套更新，不再通过旧 PUT 重试。
回退应使用升级前的成套镜像及插件；回退后旧客户端的并发覆盖风险仍然存在。
补丁不改表结构，不应为回退恢复旧账本或覆盖升级期间的正常扣费。

## 验证

要求 Git、curl、tar 和与上游 `go.mod` 匹配的 Go 工具链。
脚本在自己的临时目录下载固定提交、检查并应用补丁，不读取站点清单或已有源码配置。

```sh
bash patches/new-api/verify.sh
# 另需原生 Linux/amd64 Docker 环境；创建并清理独立实例：
bash patches/new-api/verify.sh --databases
```

默认运行真实 SQLite 的配置、扣费保留和回滚/中断恢复，以及控制器归属、PAT scope、路由登记回归。
第二条另用 PostgreSQL 16、MySQL 8.4 和 Redis 7 的无网络容器，
通过固定 Unix socket 和全新空数据库验证实际行锁及缓存预扣，不接受外部数据库 DSN。
每个数据库最多使用 1 CPU、768 MiB，Redis 64 MiB、测试进程 512 MiB；两个数据库顺序运行。
脚本只删除本次创建的容器、卷和下载的镜像，并核对已有容器的启动状态未变。

认证边界参照 OWASP 的[逐请求授权与默认拒绝](https://cheatsheetseries.owasp.org/cheatsheets/Authorization_Cheat_Sheet.html)、
[认证与凭证传输](https://cheatsheetseries.owasp.org/cheatsheets/Authentication_Cheat_Sheet.html)及
[会话管理](https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html)：
不改变登录/会话协议，沿用现有身份校验，新增权限只允许写本人的 KEY，不放宽账户权限，不记录可用凭据。
这些检查不是对整个上游系统的合规认证。

## 许可证

New API 及此目录的上游代码补丁适用 **AGPL-3.0**，保留上游 [LICENSE](LICENSE) 和 [NOTICE](NOTICE)。
这不改变 Cockpit 自有代码的许可证。
对外提供修改后的 New API 服务或镜像时，应同时提供与实际构建一致的完整对应源码、补丁与许可声明；仅给 Cockpit 源码链接不足以代替修改后 New API 的对应源码。
