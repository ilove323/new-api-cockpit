"""Chat role never migrates or runs timers, even with monitor configuration."""

import os
import importlib
import unittest
from unittest.mock import MagicMock, patch

from new_api_cockpit import runtime, gunicorn_conf, balance, timers


class ChatLifecycleTest(unittest.TestCase):
    def test_paid_step_session_outage_is_not_misreported_as_invalid_login(self):
        from new_api_cockpit.auth import LoginRequired, LoginForbidden, AuthUnavailable
        from new_api_cockpit.composition import VerifiedSession
        from new_api_cockpit.features.chat.errors import ChatError
        from chat_fixtures import access

        for failure, status, code in (
            (LoginRequired("private-error"), 401, "AUTH_REQUIRED"),
            (LoginForbidden("private-error"), 403, "AUTH_FORBIDDEN"),
            (AuthUnavailable("private-error"), 503, "AUTH_UNAVAILABLE"),
        ):
            with self.subTest(code=code), self.assertRaises(ChatError) as caught:
                VerifiedSession(MagicMock(side_effect=failure)).revalidate(access())
            self.assertEqual(
                (caught.exception.status, caught.exception.code), (status, code)
            )
            self.assertNotIn("private-error", str(caught.exception))

    def test_development_entrypoint_also_respects_chat_role(self):
        host = importlib.import_module("new_api_cockpit.app")
        with (
            patch.dict(os.environ, {"COCKPIT_PROCESS_ROLE": "chat"}),
            patch.object(host.app, "run"),
            patch("new_api_cockpit.composition.get_chat_service") as service,
            patch.object(balance, "initialize") as migrate,
            patch.object(timers, "start") as start,
            patch.object(timers, "stop") as stop,
        ):
            host.run_development()
        migrate.assert_not_called()
        start.assert_not_called()
        stop.assert_not_called()
        service.return_value.shutdown.assert_called_once()

    def test_chat_startup_skips_all_monitor_initialization(self):
        with (
            patch.dict(
                os.environ,
                {
                    "COCKPIT_PROCESS_ROLE": "chat",
                    "MONITOR_DATABASE_URL": "synthetic-only",
                },
            ),
            patch.object(runtime.sys, "argv", ["runtime"]),
            patch.object(balance, "initialize") as migrate,
            patch.object(timers, "start") as start,
            patch("new_api_cockpit.composition.get_chat_service") as get,
            patch.object(gunicorn_conf.signal, "signal"),
            patch.object(gunicorn_conf.signal, "getsignal", return_value=None),
        ):
            runtime.main()
            gunicorn_conf.post_worker_init(MagicMock())
            migrate.assert_not_called()
            start.assert_not_called()
            get.assert_called_once()

    def test_unknown_role_does_not_fall_back_to_web(self):
        with (
            patch.dict(os.environ, {"COCKPIT_PROCESS_ROLE": "unknown"}),
            patch.object(runtime.os, "execvp") as execute,
            self.assertLogs(level="ERROR"),
            self.assertRaises(SystemExit),
        ):
            runtime.main()
        execute.assert_not_called()

    def test_chat_exit_uses_chat_shutdown_not_web_timers(self):
        with (
            patch.dict(os.environ, {"COCKPIT_PROCESS_ROLE": "chat"}),
            patch("new_api_cockpit.composition.shutdown_chat_services") as stop,
            patch.object(timers, "stop") as timer,
        ):
            gunicorn_conf.worker_exit(None, None)
        stop.assert_called_once()
        timer.assert_not_called()


if __name__ == "__main__":
    unittest.main()
