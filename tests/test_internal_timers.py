"""Timer lifecycle/health/boundary checks, without real quota or notifications."""

import os
import signal
import threading
import unittest
from datetime import datetime
from unittest.mock import MagicMock, patch

from new_api_statistics import balance, gunicorn_conf, quota_worker, runtime, timers
from new_api_statistics.app import app
from new_api_statistics.report import TZ


class InternalTimerTest(unittest.TestCase):
    def test_no_database_means_no_background_threads(self):
        with (
            patch.object(balance, "configured", return_value=False),
            patch.object(timers, "Thread") as make,
        ):
            self.assertFalse(timers.start())
            self.assertTrue(timers.status()["ready"])
            make.assert_not_called()

    def test_start_stop_are_per_process_idempotent_and_health_sees_both_leaders(self):
        a, b = MagicMock(), MagicMock()
        a.ident, b.ident = 1, 2
        a.is_alive.return_value = b.is_alive.return_value = True
        conn = MagicMock()
        conn.__enter__.return_value = conn
        conn.execute.return_value.fetchone.return_value = (2,)
        with (
            patch.object(timers, "_state", None),
            patch.object(balance, "configured", return_value=True),
            patch.object(timers, "Thread", side_effect=[a, b]) as make,
            patch.dict(os.environ, {"MONITOR_DATABASE_URL": "fixture-only"}),
            patch.object(timers.psycopg, "connect", return_value=conn),
        ):
            self.assertTrue(timers.start())
            self.assertFalse(timers.start())
            self.assertEqual(make.call_count, 2)
            self.assertEqual(
                {c.kwargs["name"] for c in make.call_args_list},
                {"balance-timer", "quota-timer"},
            )
            self.assertTrue(timers.status()["ready"])
            conn.execute.return_value.fetchone.return_value = (1,)
            self.assertFalse(timers.status()["ready"])
            a.is_alive.return_value = False
            self.assertFalse(timers.status()["ready"])
            a.is_alive.return_value = True
            timers.request_stop()
            self.assertFalse(timers.status()["ready"])
            a.is_alive.return_value = b.is_alive.return_value = False
            self.assertTrue(timers.stop(timeout=1))
            self.assertIsNone(timers._state)
            self.assertTrue(timers.stop())

    def test_timed_out_shutdown_does_not_start_a_second_timer_set(self):
        threads = [MagicMock(), MagicMock()]
        for t in threads:
            t.ident = 1
            t.is_alive.return_value = True
        state = {"pid": os.getpid(), "stopped": threading.Event(), "threads": threads}
        with (
            patch.object(timers, "_state", state),
            patch.object(balance, "configured", return_value=True),
            self.assertLogs(level="WARNING"),
        ):
            self.assertFalse(timers.stop(timeout=0))
            self.assertFalse(timers.start())
            self.assertIs(timers._state, state)

    def test_gunicorn_hooks_stop_claiming_before_existing_signal_handler(self):
        previous = MagicMock()
        events = []
        with (
            patch.object(gunicorn_conf.signal, "getsignal", return_value=previous),
            patch.object(gunicorn_conf.signal, "signal") as install,
            patch.object(timers, "start") as start,
            patch.object(
                timers, "request_stop", side_effect=lambda: events.append("stop")
            ),
            patch.object(timers, "stop") as join,
        ):
            previous.side_effect = lambda *_: events.append("gunicorn")
            gunicorn_conf.post_worker_init(MagicMock())
            start.assert_called_once()
            self.assertEqual(
                {c.args[0] for c in install.call_args_list},
                {signal.SIGTERM, signal.SIGINT, signal.SIGQUIT},
            )
            install.call_args_list[0].args[1](signal.SIGTERM, None)
            self.assertEqual(events, ["stop", "gunicorn"])
            gunicorn_conf.worker_exit(None, None)
            join.assert_called_once()

    def test_health_requires_timer_readiness_without_authentication(self):
        with patch.object(
            timers, "status", return_value={"configured": True, "ready": False}
        ):
            response = app.test_client().get("/healthz")
            self.assertEqual(response.status_code, 503)
        with patch.object(
            timers, "status", return_value={"configured": True, "ready": True}
        ):
            self.assertEqual(app.test_client().get("/healthz").status_code, 200)

    def test_balance_timer_has_no_startup_cost_query_and_standby_does_no_work(self):
        for acquired in (True, False):
            with self.subTest(acquired=acquired):
                leader = MagicMock()
                leader.__enter__.return_value = leader
                leader.execute.return_value.fetchone.return_value = {
                    "acquired": acquired
                }
                stopped = MagicMock()
                stopped.is_set.return_value = False
                stopped.wait.return_value = True
                with (
                    patch.object(balance, "require_schema"),
                    patch.object(balance, "connect", return_value=leader),
                    patch.object(timers, "datetime") as clock,
                    patch.object(balance, "check_all_enabled") as check,
                ):
                    clock.now.return_value = datetime(2026, 10, 1, 21, tzinfo=TZ)
                    timers.run_balance(stopped)
                    check.assert_not_called()
                    self.assertEqual(
                        stopped.wait.call_args.args[0], 30 if acquired else 10
                    )

    def test_balance_timer_checks_only_due_boundary_then_waits_for_next_day(self):
        leader = MagicMock()
        leader.__enter__.return_value = leader
        leader.execute.return_value.fetchone.return_value = {"acquired": True}
        stopped = MagicMock()
        stopped.is_set.return_value = False
        stopped.wait.side_effect = [False, True]
        before = datetime(2026, 10, 1, 9, 59, 59, tzinfo=TZ)
        due = datetime(2026, 10, 1, 10, 0, 1, tzinfo=TZ)
        with (
            patch.object(balance, "require_schema"),
            patch.object(balance, "connect", return_value=leader),
            patch.object(timers, "datetime") as clock,
            patch.object(balance, "check_all_enabled") as check,
        ):
            clock.now.side_effect = [before, before, before, due, due]
            timers.run_balance(stopped)
            check.assert_called_once_with(now=due, _stopped=stopped)
            self.assertEqual([c.args[0] for c in stopped.wait.call_args_list], [1, 30])

    def test_quota_shutdown_is_quiet_and_failures_are_sanitized(self):
        stopped = threading.Event()

        def interrupted(_):
            stopped.set()
            raise InterruptedError("not for logs")

        with (
            patch.object(quota_worker, "serve", side_effect=interrupted),
            patch.object(quota_worker.LOG, "error") as log,
        ):
            quota_worker.run(stopped)
            log.assert_not_called()
        stopped = MagicMock()
        stopped.is_set.side_effect = [False, False, True]
        with (
            patch.object(
                quota_worker, "serve", side_effect=ValueError("private-value")
            ),
            self.assertLogs(level="ERROR") as captured,
        ):
            quota_worker.run(stopped)
        self.assertNotIn("private-value", "".join(captured.output))
        stopped.wait.assert_called_once_with(5)

    def test_runtime_preserves_custom_gunicorn_configuration(self):
        with (
            patch.object(balance, "configured", return_value=False),
            patch.object(
                runtime.sys,
                "argv",
                ["runtime", "gunicorn", "--config=custom.py", "module:app"],
            ),
            patch.object(runtime.os, "execvp") as run,
        ):
            runtime.main()
        run.assert_called_once_with(
            "gunicorn", ["gunicorn", "--config=custom.py", "module:app"]
        )


@unittest.skipUnless(
    os.environ.get("MONITOR_DATABASE_URL"), "disposable PostgreSQL required"
)
class GunicornTimerIntegrationTest(unittest.TestCase):
    def test_real_two_worker_startup_handover_and_shutdown(self):
        """A new disposable DB has no quota rules; NEVER invokes upstream mutations."""
        import json
        import signal
        import socket
        import subprocess
        import sys
        import tempfile
        import time
        import urllib.error
        import urllib.request
        import uuid
        from pathlib import Path
        from psycopg import sql
        from psycopg.conninfo import make_conninfo
        import psycopg
        from new_api_statistics.locks import (
            BALANCE_SCHEDULER_LOCK,
            QUOTA_SCHEDULER_LOCK,
        )

        dsn = os.environ["MONITOR_DATABASE_URL"]
        with psycopg.connect(dsn, autocommit=True) as admin:
            allowed = admin.execute(
                "SELECT rolcreatedb OR rolsuper FROM pg_roles WHERE rolname=current_user"
            ).fetchone()[0]
            if not allowed:
                self.skipTest("needs an isolated PostgreSQL fixture role with CREATEDB")
            db = "timers_fixture_" + uuid.uuid4().hex
            admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(db)))
        newdsn = make_conninfo(dsn, dbname=db)
        self.addCleanup(self.drop_db, dsn, db)
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        env = dict(os.environ, MONITOR_DATABASE_URL=newdsn)
        env["PATH"] = (
            str(Path(sys.executable).parent) + os.pathsep + env.get("PATH", "")
        )
        root = Path(__file__).resolve().parents[1]
        env["PYTHONPATH"] = str(root / "src") + os.pathsep + env.get("PYTHONPATH", "")
        with tempfile.TemporaryDirectory(prefix="statistics-timer-test-") as folder:
            with open(Path(folder) / "web.log", "w+") as log:
                proc = subprocess.Popen(
                    [
                        sys.executable,
                        "-m",
                        "new_api_statistics.runtime",
                        "gunicorn",
                        "--bind",
                        f"127.0.0.1:{port}",
                        "--workers",
                        "2",
                        "--threads",
                        "2",
                        "new_api_statistics.app:app",
                    ],
                    cwd=root,
                    env=env,
                    stdout=log,
                    stderr=log,
                    start_new_session=True,
                )
                try:

                    def healthy():
                        try:
                            with urllib.request.urlopen(
                                f"http://127.0.0.1:{port}/healthz", timeout=2
                            ) as response:
                                return json.load(response)["timers"]["ready"]
                        except (urllib.error.URLError, TimeoutError, KeyError):
                            return False

                    def owners():
                        with psycopg.connect(newdsn) as c:
                            return c.execute(
                                """SELECT l.objid,a.application_name FROM pg_locks l
                            JOIN pg_stat_activity a ON a.pid=l.pid WHERE l.locktype='advisory'
                            AND l.granted AND l.classid=0 AND l.objsubid=1
                            AND l.database=(SELECT oid FROM pg_database WHERE datname=current_database())
                            AND l.objid=ANY(%s::oid[])""",
                                ([BALANCE_SCHEDULER_LOCK, QUOTA_SCHEDULER_LOCK],),
                            ).fetchall()

                    deadline = time.monotonic() + 25
                    while time.monotonic() < deadline and not healthy():
                        self.assertIsNone(
                            proc.poll(), "web process unexpectedly exited"
                        )
                        time.sleep(0.1)
                    self.assertTrue(healthy(), "web/timers never became ready")
                    before = owners()
                    self.assertEqual(len(before), 2)
                    quota_owner = next(
                        name for lock, name in before if lock == QUOTA_SCHEDULER_LOCK
                    )
                    worker_pid = int(quota_owner.rsplit(":", 1)[1])
                    parent = (
                        subprocess.check_output(
                            ["ps", "-p", str(worker_pid), "-o", "ppid="]
                        )
                        .decode()
                        .strip()
                    )
                    self.assertEqual(int(parent), proc.pid)
                    os.kill(worker_pid, signal.SIGTERM)
                    deadline = time.monotonic() + 25
                    changed = False
                    while time.monotonic() < deadline:
                        now = owners()
                        changed = bool(
                            len(now) == 2
                            and next(
                                name
                                for lock, name in now
                                if lock == QUOTA_SCHEDULER_LOCK
                            )
                            != quota_owner
                        )
                        if changed and healthy():
                            break
                        time.sleep(0.1)
                    self.assertTrue(
                        changed and healthy(),
                        "remaining/new web worker did not take over timers",
                    )
                    with psycopg.connect(newdsn) as c:
                        self.assertEqual(
                            c.execute(
                                "SELECT count(*) FROM quota_schedule_runs"
                            ).fetchone()[0],
                            0,
                        )
                        self.assertEqual(
                            c.execute(
                                "SELECT count(*) FROM balance_daily_runs"
                            ).fetchone()[0],
                            0,
                        )
                finally:
                    if proc.poll() is None:
                        os.killpg(proc.pid, signal.SIGTERM)
                        try:
                            proc.wait(timeout=15)
                        except subprocess.TimeoutExpired:
                            os.killpg(proc.pid, signal.SIGKILL)
                            proc.wait(timeout=5)
                    # A dead worker must not retain either leadership lock.
                    self.assertEqual(owners(), [])

    @staticmethod
    def drop_db(dsn, db):
        import psycopg
        from psycopg import sql

        with psycopg.connect(dsn, autocommit=True) as c:
            c.execute(
                sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(db))
            )
