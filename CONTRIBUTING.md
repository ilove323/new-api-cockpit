# Contributing

当前维护者和贡献者：[@ilove323](https://github.com/ilove323)。

使用 Python 3.12 或更高版本：

```bash
python3 -m venv .venv
.venv/bin/pip install -e .
.venv/bin/pip install ruff
.venv/bin/python -m unittest discover -s tests
.venv/bin/ruff check src tests
.venv/bin/ruff format --check src tests
node --test tests/*.cjs
```

开发启动：`python -m new_api_statistics.app`。
依赖版本范围继续在 `requirements.txt` 维护，不要求锁文件。
提交前执行 CI 中的检查，修改数据库行为需运行 PostgreSQL 集成测试。
测试必须使用独立数据库和虚构数据，绝不使用生产凭据。

SQL 集成测试使用独立 PostgreSQL 库。在当前终端设置仅属于测试库的
`PGHOST`、`PGPORT`、`PGUSER`、`PGPASSWORD`、`PGDATABASE` 和 `MONITOR_DATABASE_URL` 后运行：

```bash
.venv/bin/python -m unittest discover -s tests
.venv/bin/python tests/check_migrations.py
.venv/bin/python tests/check_balance_database.py
```

定时配额集成测试也可通过 `TEST_SCHEDULE_DATABASE_URL` 指定独立测试库。
测试会创建和删除临时 schema，但不得指向生产库；真实 New API 配额变更均由 fixture/mock 替代。

通过 Issue 说明问题，通过 PR 提交修改；PR 说明行为变化、验证结果及升级影响。
迁移脚本追加到 `src/new_api_statistics/migrations/`，编号递增，不修改已发布迁移。
发版流程见 [releasing.md](docs/releasing.md)。

贡献按项目 Apache-2.0 许可证提交；第三方代码必须保留原许可证与归属。
