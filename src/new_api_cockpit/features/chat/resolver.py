"""Killable DNS lookup: the helper receives only a host and port, never KEYs.

No thread-pool DNS: a timed-out libc resolver must not outlive the run lease.
The helper uses an isolated interpreter and an empty environment.
"""

import ipaddress
import json
import socket
import subprocess
import sys
import time

from .errors import ChatError

_RESOLVER_CODE = (
    "import json,socket,sys; "
    "a=socket.getaddrinfo(sys.argv[1],int(sys.argv[2]),type=socket.SOCK_STREAM)[0]; "
    "print(json.dumps([a[0],a[1],a[2],a[4]]))"
)


def resolve_address(host, port, deadline, abort):
    """Return (family, socktype, protocol, numeric sockaddr), or fail once.

    deadline is an absolute monotonic connect deadline, bounded by the run.
    A single address is selected; neither HTTP nor connect is retried here.
    """
    abort.check()
    if time.monotonic() >= deadline:
        raise ChatError("上游地址解析超时。", 504, "CHAT_UPSTREAM_ERROR")
    process = None
    try:
        process = subprocess.Popen(
            [sys.executable, "-I", "-c", _RESOLVER_CODE, host, str(port)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env={},
            close_fds=True,
        )
        while True:
            abort.check()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ChatError("上游地址解析超时。", 504, "CHAT_UPSTREAM_ERROR")
            try:
                output, _ = process.communicate(timeout=min(remaining, 0.05))
                break
            except subprocess.TimeoutExpired:
                continue
        abort.check()
        if time.monotonic() >= deadline:
            raise ChatError("上游地址解析超时。", 504, "CHAT_UPSTREAM_ERROR")
        if process.returncode or len(output) > 4096:
            raise ValueError()
        family, kind, protocol, address = json.loads(output)
        if (
            family not in (socket.AF_INET, socket.AF_INET6)
            or kind != socket.SOCK_STREAM
            or type(protocol) is not int
            or not isinstance(address, list)
            or len(address) != (2 if family == socket.AF_INET else 4)
            or address[1] != port
        ):
            raise ValueError()
        # A helper result must never send the socket layer back into DNS.
        if not isinstance(address[0], str) or "%" in address[0]:
            raise ValueError()
        numeric = ipaddress.ip_address(address[0])
        if numeric.version != (4 if family == socket.AF_INET else 6):
            raise ValueError()
        if any(type(part) is not int or part < 0 for part in address[1:]):
            raise ValueError()
        return family, kind, protocol, tuple(address)
    except ChatError:
        raise
    except (OSError, ValueError, TypeError, IndexError, UnicodeError):
        raise ChatError("无法解析固定上游地址。", 502, "CHAT_UPSTREAM_ERROR") from None
    finally:
        if process is not None:
            if process.poll() is None:
                process.kill()
            process.communicate()
