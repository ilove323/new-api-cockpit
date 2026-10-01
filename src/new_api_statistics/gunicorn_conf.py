"""Fork-safe application lifecycle; included automatically by runtime startup."""

import signal

from new_api_statistics import timers

# Give a current five-user wave time to finish before process/container shutdown.
graceful_timeout = 75


def post_worker_init(worker):
    # Stop claiming jobs immediately, before Gunicorn drains HTTP requests.
    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGQUIT):
        previous = signal.getsignal(sig)

        def shutdown(signum, frame, previous=previous):
            timers.request_stop()
            if callable(previous):
                previous(signum, frame)

        signal.signal(sig, shutdown)
    timers.start()


def worker_exit(server, worker):
    timers.stop()
