# Contributing

当前维护者和贡献者：[@ilove323](https://github.com/ilove323)。

使用 Python 3.12 或更高版本：

```bash
python3 -m venv .venv
.venv/bin/pip install -e .
.venv/bin/pip install ruff build
.venv/bin/python -m unittest discover -s tests
.venv/bin/ruff check src tests deploy
.venv/bin/ruff format --check src tests deploy
node --test tests/*.cjs
npm ci --prefix frontend/studio --ignore-scripts
npm --prefix frontend/studio run check
npx --yes --package typescript@6.0.3 tsc -p tests/tsconfig.studio.json
npx --yes --package typescript@6.0.3 tsc -p frontend/studio/tsconfig.json
.venv/bin/python -m build
```

开发启动：`python -m new_api_cockpit.app`。
Python 依赖版本范围继续在 `requirements.txt` 维护。工作站构建工具单独使用 `frontend/studio/package-lock.json` 锁定。
VS Code 插件源码独立放在 `vsix/`；Docker 的 Node 构建阶段打包 VSIX，Python 包和运行镜像只携带安装包及清单，不带 Node 或 node_modules。开发检查与打包：`npm ci --prefix vsix --ignore-scripts`、`npm run --prefix vsix check`、`npm test --prefix vsix`、`npm run --prefix vsix package`。源码开发需要系统下载时执行 `npm run --prefix vsix bundle` 后重启应用。VS Code 宿主检查及虚构身份的 MCP 联调见[插件说明](vsix/README.md#开发与打包)。
提交前执行 CI 中的检查，修改数据库行为需运行 PostgreSQL 集成测试。
测试必须使用独立数据库和虚构数据，绝不使用生产凭据。

账户 MCP 的协议联调使用独立 SDK 环境，不给应用增加运行时依赖，不访问真实 New API 或模型服务：

```sh
python3 -m venv /tmp/cockpit-mcp-sdk
/tmp/cockpit-mcp-sdk/bin/pip install mcp==2.3.0
MCP_SDK_PYTHON=/tmp/cockpit-mcp-sdk/bin/python .venv/bin/python -m unittest discover -s tests -p 'test_account_mcp_sdk.py'
```

该测试在 loopback 随机端口启动短生命周期 fixture，验证现代客户端自动回退到支持的 MCP 生命周期、列工具与两项查询。
`test_account_mcp.py` 另验证 PAT/模型 KEY/会话边界和读权限；配置隔离 PostgreSQL 后会实际执行用户隔离、缓存归一化与时间边界 SQL。

SQL 集成测试使用独立 PostgreSQL 库。在当前终端设置仅属于测试库的
`PGHOST`、`PGPORT`、`PGUSER`、`PGPASSWORD`、`PGDATABASE` 和 `MONITOR_DATABASE_URL` 后运行：

```bash
export TEST_SCHEDULE_DATABASE_URL="$MONITOR_DATABASE_URL"
.venv/bin/python -m unittest discover -s tests
```

定时配额与用户/KEY 管理集成测试使用 `TEST_SCHEDULE_DATABASE_URL`；与 CI 一样指向同一个隔离测试库。
这些测试会创建和删除临时 schema，但不得指向生产库；真实 New API 配额变更均由 fixture/mock 替代。

`test_internal_timers.py` 还包含真实 Gunicorn 双进程启动、领导者接管和退出回归：
它根据 `MONITOR_DATABASE_URL` 连接隔离 PostgreSQL，为测试新建随机名称的数据库，结束后仅删除自己创建的库。
测试角色必须在**隔离测试实例**具有 `CREATEDB`，否则该项明确跳过；不要因此提升生产账号权限。
临时库不创建配额规则，不调用真实额度修改或报警接口。其余 DOM 测试不启动浏览器。

工作站协议、画布像素和错误响应测试均使用虚构素材，不调用模型。三个前端边界模块通过 JSDoc 做类型检查；复用的画板、视图及合成源码另做 TypeScript 检查。构建工具只用于开发，运行镜像使用预编译的本地 JavaScript。
修改 `frontend/studio` 后运行 `npm --prefix frontend/studio run build`，同时更新生成的静态资源；上游源码与许可证见[源码维护](docs/image-studio-sources.md)。
跨进程回执测试需要本机 Redis 7 可执行文件；测试自行启动临时的回环实例及 AOF 目录，不连接现有 Redis：

```bash
IMAGE_STUDIO_TEST_REDIS_BINARY="$(command -v redis-server)" .venv/bin/python -m unittest discover -s tests -p test_image_receipts.py
```

没有这个环境变量时，只跳过真实 Redis 用例；其他回执测试仍执行。网页验收使用本地虚构 KEY、模型和截获的响应，不将真实生图放入 CI。

本地浏览器回归涵盖原位文字、标注缩放/擦除、竖横图输出、局部合成和大图预览。安装 Playwright 后运行（使用本机 Chromium 时设置 `STUDIO_BROWSER_EXECUTABLE`）：

```bash
node tests/browser/studio-size-regression.cjs
```

默认只连本机测试页面、截获响应，不登录生产账号，不收费。

## AI 助手离线回归

聊天与生图的边界检查、HTTP 契约和文件/跨进程测试都包含在前述 `unittest discover` 中；前端回归包含在 `node --test tests/*.cjs` 中。
未配置独立 PostgreSQL 或 Redis 时，对应环境测试明确跳过，不应临时接入生产库来消除跳过。

聊天重点覆盖本人会话和账号隔离、完整历史分页与崩溃恢复、三协议连续状态、有界文件/公网工具、自动 Memory 的来源与版本保护、上下文压缩，以及 50 MiB 上传与超限拒绝。
旧模块兼容别名、HTML 解析器回调和 Gunicorn 生命周期钩子均有实际调用，不应仅因缺少直接函数引用而删除。

可选的真实浏览器回归使用本地 Flask 和合成上游；安装 Playwright 后运行：

```bash
node tests/browser/chat-integration.cjs
node tests/browser/chat-v2-integration.cjs
```

默认使用本机 Chrome，可通过 `COCKPIT_BROWSER_EXECUTABLE` 指定 Chromium 路径，或用 `COCKPIT_PYTHON` 指定项目 Python。
Playwright 必须能通过标准 Node 模块查找找到；单独安装开发工具时，可用 `NODE_PATH` 指向其 `node_modules`，测试不依赖维护者机器的目录。
脚本自行启动本地网页/聊天池和临时文件目录，不登录真实站点、不使用个人 Chrome 配置、不调用付费模型；结束时清理自身进程和测试数据。
前者覆盖文件选择自动建聊天、50 MiB 上传/下载一致性和用户隔离；后者覆盖三协议、记忆/压缩、历史与当前界面默认值。

运行镜像使用 Linux/POSIX 的文件锁和本地存储；本机检查通过不等于 Linux 镜像、真实供应商计费或具体站点部署已验收。

智力测试预览的离线浏览器回归：`node tests/browser/intelligence-preview-regression.cjs`。它模拟 HTTP/HTTPS 页面、缺少 `randomUUID`、双标签页、重播和页面恢复，所有模型响应均为虚构数据；使用本机 Chromium 时设置 `COCKPIT_BROWSER_EXECUTABLE`。

通过 Issue 说明问题，通过 PR 提交修改；PR 说明行为变化、验证结果及升级影响。
Python 单元与 PostgreSQL 集成测试统一由 `unittest discover` 执行，不需要额外的手工检查脚本。
迁移脚本追加到 `src/new_api_cockpit/migrations/`，编号递增，不修改已发布迁移。
发版流程见 [releasing.md](docs/releasing.md)。

贡献按项目 Apache-2.0 许可证提交；第三方代码必须保留原许可证与归属。
