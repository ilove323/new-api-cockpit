"""Small fixed-origin HTTP connection pool; no response/authentication cache.

Only callers that consumed and validated a response may return a connection.
The pool never retries a request, forwards cookies or stores raw credentials.
"""

import atexit
from collections import OrderedDict
from contextlib import contextmanager
from dataclasses import dataclass, field
import hashlib
import hmac
from http.client import HTTPException
import os
import threading
import time


@dataclass(eq=False)
class Entry:
    key: tuple = field(repr=False)
    born: float
    pid: int
    connection: object = field(default=None, repr=False)
    idle_since: float = 0


@dataclass
class Borrowed:
    connection: object = field(repr=False)
    reusable: bool = False


class ConnectionPool:
    def __init__(
        self,
        *,
        maximum=16,
        per_scope=4,
        idle_seconds=30,
        lifetime_seconds=120,
        clock=time.monotonic,
    ):
        self.maximum, self.per_scope = maximum, per_scope
        if not 1 <= per_scope <= maximum or idle_seconds < 0 or lifetime_seconds < 0:
            raise ValueError("Invalid HTTP connection pool limits")
        self.idle_seconds, self.lifetime_seconds = idle_seconds, lifetime_seconds
        self.clock = clock
        self.pid = os.getpid()
        self._salt = os.urandom(32)
        self._condition = threading.Condition()
        self._entries = set()
        self._idle = OrderedDict()
        self._revision = 0
        self._closed = False

    def _after_fork(self):
        if self.pid == os.getpid():
            return
        inherited = list(self._entries)
        # Never acquire an inherited lock that another parent thread held.
        self._condition = threading.Condition()
        self._entries, self._idle = set(), OrderedDict()
        self.pid = os.getpid()
        self._salt, self._revision, self._closed = os.urandom(32), 0, False
        self._close(inherited)

    def scope(self, origin, role, user_id, kind, credential, factory):
        self._after_fork()
        digest = hmac.new(self._salt, credential.encode(), hashlib.sha256).digest()
        return (origin, role, user_id, kind, digest, factory)

    @staticmethod
    def _close(entries):
        for entry in entries:
            if entry.connection is not None:
                try:
                    entry.connection.close()
                except (OSError, HTTPException):
                    pass

    def _remove(self, entry):
        self._entries.discard(entry)
        self._idle.pop(entry, None)
        self._revision += 1

    def _take(self, key, factory, deadline):
        self._after_fork()
        while True:
            retired, selected = [], None
            with self._condition:
                now = self.clock()
                if self._closed:
                    raise OSError("HTTP connection pool closed")
                if now >= deadline:
                    raise TimeoutError("HTTP connection deadline")
                for entry in list(self._idle):
                    if (
                        now - entry.idle_since >= self.idle_seconds
                        or now - entry.born >= self.lifetime_seconds
                    ):
                        self._remove(entry)
                        retired.append(entry)
                for entry in reversed(self._idle):
                    if entry.key == key:
                        selected = entry
                        self._idle.pop(entry)
                        self._revision += 1
                        break
                if (
                    selected is None
                    and sum(entry.key == key for entry in self._entries)
                    < self.per_scope
                ):
                    if len(self._entries) >= self.maximum and self._idle:
                        entry = next(iter(self._idle))
                        self._remove(entry)
                        retired.append(entry)
                    if len(self._entries) < self.maximum:
                        selected = Entry(key, now, self.pid)
                        self._entries.add(selected)
                        self._revision += 1
                revision = self._revision
            self._close(retired)
            if selected is not None:
                if selected.connection is None:
                    try:
                        selected.connection = factory(
                            max(0.001, deadline - self.clock())
                        )
                    except BaseException:
                        self._release(selected, False)
                        raise
                return selected
            with self._condition:
                # A release between selection and waiting must not be lost.
                if revision == self._revision and not self._closed:
                    self._condition.wait(max(0, deadline - self.clock()))

    def _release(self, entry, reusable):
        self._after_fork()
        close = True
        with self._condition:
            if entry in self._entries:
                now = self.clock()
                if (
                    reusable
                    and not self._closed
                    and now - entry.born < self.lifetime_seconds
                ):
                    entry.idle_since = now
                    self._idle[entry] = None
                    self._revision += 1
                    close = False
                else:
                    self._remove(entry)
                self._condition.notify_all()
        if close:
            self._close([entry])

    @contextmanager
    def connection(self, key, factory, deadline):
        entry = self._take(key, factory, deadline)
        lease = Borrowed(entry.connection)
        try:
            yield lease
        except BaseException:
            lease.reusable = False
            raise
        finally:
            self._release(entry, lease.reusable)

    def close(self):
        self._after_fork()
        with self._condition:
            self._closed = True
            retired = list(self._idle)
            for entry in retired:
                self._remove(entry)
            self._condition.notify_all()
        self._close(retired)


@contextmanager
def one_shot(factory, deadline):
    connection = factory(max(0.001, deadline - time.monotonic()))
    try:
        yield Borrowed(connection)
    finally:
        connection.close()


DEFAULT_POOL = ConnectionPool()
atexit.register(DEFAULT_POOL.close)
