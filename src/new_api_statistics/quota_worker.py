"""Independent single-leader scheduler; no Gunicorn timers or startup quota catch-up."""

import logging
import signal
from datetime import datetime
from threading import Event

from new_api_statistics import balance
from new_api_statistics import quota_schedule as schedules
from new_api_statistics import quota_schedule_executor as executor
from new_api_statistics.report import TZ

LOG = logging.getLogger(__name__)


def serve(stopped):
    schedules.initialize()
    with balance.connect() as leader:
        leader.autocommit = True
        acquired = leader.execute(
            "SELECT pg_try_advisory_lock(%s) AS acquired", (schedules.LEADER_LOCK,)
        ).fetchone()["acquired"]
        if not acquired:
            LOG.info("Another quota scheduler is active; standby.")
            stopped.wait(10)
            return
        leader.execute("LISTEN " + schedules.NOTIFY_CHANNEL)
        schedules.recover_interrupted()

        def check():
            if stopped.is_set():
                raise InterruptedError("Quota worker is stopping")
            # A dead session means the advisory lock was lost.
            leader.execute("SELECT 1")

        while not stopped.is_set():
            check()
            runs = schedules.claim_due(datetime.now(TZ))
            for run_id in runs:
                check()
                executor.execute(run_id, check)
            due = schedules.next_due()
            # LISTEN/NOTIFY wakes configuration changes immediately. The timeout
            # bounds signal response and reconciles missed notifications; it reads
            # only schedule metadata, never balances, users or billing logs.
            wait = (
                min(30, max(0.01, (due - datetime.now(TZ)).total_seconds()))
                if due
                else 30
            )
            for _ in leader.notifies(timeout=wait, stop_after=1):
                break


def main():
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    stopped = Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stopped.set())
    if not balance.configured():
        LOG.error("MONITOR_DATABASE_URL is required for the quota scheduler.")
        raise SystemExit(1)
    while not stopped.is_set():
        try:
            serve(stopped)
        except Exception as exc:
            # No credentials, SQL, response bodies or database URLs in logs.
            LOG.error(
                "Quota scheduler interrupted (%s); no mutations will be replayed.",
                type(exc).__name__,
            )
            stopped.wait(5)


if __name__ == "__main__":
    main()
