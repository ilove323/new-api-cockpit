# Contributing

当前维护者和贡献者：[@ilove323](https://github.com/ilove323)。

使用 Python 3.12 或更高版本：

```bash
python3 -m venv .venv
.venv/bin/pip install -e .
.venv/bin/pip install ruff build
.venv/bin/python -m unittest discover -s tests
.venv/bin/ruff check src tests
.venv/bin/ruff format --check src tests
node --test tests/*.cjs
npm ci --prefix frontend/studio --ignore-scripts
npm --prefix frontend/studio run check
npx --yes --package typescript@6.0.3 tsc -p tests/tsconfig.studio.json
npx --yes --package typescript@6.0.3 tsc -p frontend/studio/tsconfig.json
.venv/bin/python -m build
```

开发启动：`python -m new_api_cockpit.app`。
Python 依赖版本范围继续在 `requirements.txt` 维护。工作站构建工具单独使用 `frontend/studio/package-lock.json` 锁定。
提交前执行 CI 中的检查，修改数据库行为需运行 PostgreSQL 集成测试。
测试必须使用独立数据库和虚构数据，绝不使用生产凭据。

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

通过 Issue 说明问题，通过 PR 提交修改；PR 说明行为变化、验证结果及升级影响。
Python 单元与 PostgreSQL 集成测试统一由 `unittest discover` 执行，不需要额外的手工检查脚本。
迁移脚本追加到 `src/new_api_cockpit/migrations/`，编号递增，不修改已发布迁移。
发版流程见 [releasing.md](docs/releasing.md)。

贡献按项目 Apache-2.0 许可证提交；第三方代码必须保留原许可证与归属。
