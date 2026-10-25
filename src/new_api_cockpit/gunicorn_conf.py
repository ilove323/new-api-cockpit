"""Fork-safe application lifecycle; included automatically by runtime startup."""

import signal
import os

from new_api_cockpit import timers

# Never log the query string or credential-bearing headers, including rejected
# MCP requests that mistakenly place a PAT in the URL. Operators overriding
# access_log_format must preserve this boundary.
access_log_format = '%(h)s %(t)s "%(m)s %(U)s %(H)s" %(s)s %(b)s %(L)s'

# Give a current five-user wave time to finish before process/container shutdown.
graceful_timeout = {"image": 330, "chat": 20}.get(
    os.environ.get("COCKPIT_PROCESS_ROLE"), 75
)


def post_worker_init(worker):
    if os.environ.get("COCKPIT_PROCESS_ROLE") == "chat":
        from new_api_cockpit.auth import verify_session
        from new_api_cockpit.composition import get_chat_service

        service = get_chat_service(verify_session)
        for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGQUIT):
            previous = signal.getsignal(sig)

            def shutdown_chat(signum, frame, previous=previous):
                service.request_stop()
                if callable(previous):
                    previous(signum, frame)

            signal.signal(sig, shutdown_chat)
        return
    if os.environ.get("COCKPIT_PROCESS_ROLE") == "image":
        from new_api_cockpit import image_studio

        image_studio.initialize_capacity()
        return
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
    if os.environ.get("COCKPIT_PROCESS_ROLE") == "chat":
        from new_api_cockpit.composition import shutdown_chat_services

        shutdown_chat_services()
        return
    if os.environ.get("COCKPIT_PROCESS_ROLE") == "image":
        return
    timers.stop()
