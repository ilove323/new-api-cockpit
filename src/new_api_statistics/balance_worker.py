#!/usr/bin/env python3
# Usage: PG*=New API read-only connection, MONITOR_DATABASE_URL=... python -m new_api_statistics.balance_worker
# Legacy standalone compatibility only; normal deployment uses built-in timers.
"""Sleep until 10:00 Beijing time; no minute polling or startup usage checks."""

import logging
import signal
from datetime import datetime, timedelta
from threading import Event

from new_api_statistics import balance


def next_run(now):
    now = now.astimezone(balance.TZ)
    scheduled = now.replace(hour=10, minute=0, second=0, microsecond=0)
    return scheduled if now < scheduled else scheduled + timedelta(days=1)


def log_failure(exc):
    logging.error("Balance check failed: %s", type(exc).__name__)
    try:
        balance.record_failure()
    except Exception:
        logging.error("Unable to persist monitor failure")


def main():
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    stopped = Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stopped.set())
    initialized = False
    if balance.configured():
        try:
            balance.initialize()
            initialized = True
        except Exception as exc:
            log_failure(exc)
    while not stopped.is_set():
        now = datetime.now(balance.TZ)
        scheduled = next_run(now)
        logging.info("Next scheduled balance check: %s", scheduled.isoformat())
        if stopped.wait((scheduled - now).total_seconds()):
            break
        if balance.configured():
            try:
                if not initialized:
                    balance.initialize()
                    initialized = True
                balance.check_all_enabled()
            except Exception as exc:
                log_failure(exc)


if __name__ == "__main__":
    main()
