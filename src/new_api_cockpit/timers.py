"""Application-owned timers: start after fork, one DB leader for each job.

Imports and HTTP requests never start threads. Timers are started by the web
process lifecycle. Source queries/notifications occur only when a job is due.
"""

import logging
import os
import time
from datetime import datetime, timedelta
from threading import Event, Lock, Thread

import psycopg

from new_api_cockpit import balance, quota_timer
from new_api_cockpit.locks import BALANCE_SCHEDULER_LOCK, QUOTA_SCHEDULER_LOCK

LOG = logging.getLogger(__name__)
_guard = Lock()
_state = None
STOP_TIMEOUT = 60


def next_balance_run(now):
    now = now.astimezone(balance.TZ)
    scheduled = now.replace(hour=10, minute=0, second=0, microsecond=0)
    return scheduled if now < scheduled else scheduled + timedelta(days=1)


def record_balance_failure(exc):
    LOG.error("Balance check failed: %s", type(exc).__name__)
    try:
        balance.record_failure()
    except Exception:
        LOG.error("Unable to persist monitor failure")


def run_balance(stopped):
    # Initial startup always chooses a future 10:00, never a first balance check.
    # Standby processes retain this boundary so leadership handover at 10:00
    # does not silently skip the day's check.
    scheduled = next_balance_run(datetime.now(balance.TZ))
    LOG.info("Next scheduled balance check: %s", scheduled.isoformat())
    while not stopped.is_set():
        try:
            balance.require_schema()
            with balance.connect() as leader:
                leader.autocommit = True
                acquired = leader.execute(
                    "SELECT pg_try_advisory_lock(%s) AS acquired",
                    (BALANCE_SCHEDULER_LOCK,),
                ).fetchone()["acquired"]
                if not acquired:
                    if stopped.wait(10):
                        return
                    continue
                leader.execute(
                    "SELECT set_config('application_name',%s,false)",
                    (f"cockpit-balance-timer:{os.getpid()}",),
                )
                LOG.info("Balance timer leadership acquired.")
                while not stopped.is_set():
                    # Liveness only: no billing/user/log query during waiting.
                    leader.execute("SELECT 1")
                    now = datetime.now(balance.TZ)
                    if now >= scheduled:
                        due = scheduled
                        scheduled = next_balance_run(now)
                        if stopped.is_set():
                            return
                        # Do not catch up days missed while disconnected.
                        if now.date() == due.date():
                            try:
                                balance.check_all_enabled(now=now, _stopped=stopped)
                            except Exception as exc:
                                record_balance_failure(exc)
                        LOG.info(
                            "Next scheduled balance check: %s", scheduled.isoformat()
                        )
                    if stopped.wait(
                        min(
                            30,
                            max(
                                0.01,
                                (scheduled - datetime.now(balance.TZ)).total_seconds(),
                            ),
                        )
                    ):
                        return
        except Exception as exc:
            if stopped.is_set():
                return
            LOG.error("Balance timer interrupted (%s)", type(exc).__name__)
            stopped.wait(5)


def start():
    """Start once per web process; no configured monitor means no timers."""
    global _state
    if not balance.configured():
        return False
    with _guard:
        if _state is not None and _state["pid"] == os.getpid():
            return False
        stopped = Event()
        threads = [
            Thread(
                target=run_balance, args=(stopped,), name="balance-timer", daemon=True
            ),
            Thread(
                target=quota_timer.run,
                args=(stopped,),
                name="quota-timer",
                daemon=True,
            ),
        ]
        _state = {"pid": os.getpid(), "stopped": stopped, "threads": threads}
        for thread in threads:
            thread.start()
        LOG.info("Application balance/quota timers started in web process.")
        return True


def stop(timeout=STOP_TIMEOUT):
    """Stop new work, allow the current five-user wave to persist its results."""
    global _state
    with _guard:
        state = _state
        if state is None or state["pid"] != os.getpid():
            return True
        state["stopped"].set()
    # Wake LISTEN immediately, outside signal handling. This only emits a
    # metadata notification; it never queries consumption or changes quotas.
    try:
        with psycopg.connect(
            os.environ["MONITOR_DATABASE_URL"],
            connect_timeout=1,
            options="-c statement_timeout=1000",
        ) as conn:
            conn.execute(
                "SELECT pg_notify(%s,'')", (quota_timer.schedules.NOTIFY_CHANNEL,)
            )
    except Exception:
        pass  # The scheduler's bounded notification wait remains the fallback.
    deadline = time.monotonic() + timeout
    for thread in state["threads"]:
        if thread.ident is not None:
            thread.join(max(0, deadline - time.monotonic()))
    finished = not any(t.is_alive() for t in state["threads"])
    with _guard:
        if finished and _state is state:
            _state = None
    if not finished:
        LOG.warning(
            "Timer shutdown deadline reached; interrupted quota results require reconciliation."
        )
    return finished


def request_stop():
    """Signal-safe nonblocking request; process exit joins the timer threads."""
    state = _state
    if state and state["pid"] == os.getpid():
        state["stopped"].set()


def status():
    """Health covers local timer threads AND both global leader sessions."""
    if not balance.configured():
        return {"configured": False, "ready": True}
    with _guard:
        state = _state
        alive = bool(
            state
            and state["pid"] == os.getpid()
            and not state["stopped"].is_set()
            and all(t.is_alive() for t in state["threads"])
        )
    if not alive:
        return {"configured": True, "ready": False, "threads_running": False}
    try:
        with psycopg.connect(
            os.environ["MONITOR_DATABASE_URL"],
            connect_timeout=1,
            options="-c default_transaction_read_only=on -c statement_timeout=1000",
        ) as conn:
            count = conn.execute(
                """SELECT count(DISTINCT objid) FROM pg_locks WHERE locktype='advisory'
                AND granted AND classid=0 AND objsubid=1
                AND database=(SELECT oid FROM pg_database WHERE datname=current_database())
                AND objid=ANY(%s::oid[])""",
                ([BALANCE_SCHEDULER_LOCK, QUOTA_SCHEDULER_LOCK],),
            ).fetchone()[0]
    except Exception:
        count = 0
    return {
        "configured": True,
        "ready": count == 2,
        "threads_running": True,
        "leaders_active": count == 2,
    }
