"""Stable-inode, process-wide POSIX leases. Never unlink lock files."""

import errno
import fcntl
import os
import time
import re

from .errors import ChatError
from .fs import storage_error
from .schemas import identifier, integer


class FileLock:
    def __init__(
        self, fs, path, *, code="CHAT_CONVERSATION_BUSY", status=409, wait_seconds=0
    ):
        self.fd = -1
        self.path = path
        # Separate exclusive creation from opening an existing inode. Darwin
        # can report ENOENT for racing openat(O_CREAT|O_NOFOLLOW); no-follow
        # validation is preserved on every bounded attempt.
        for attempt in range(3):
            try:
                try:
                    fd = fs.open_file(path, os.O_RDWR | os.O_CREAT | os.O_EXCL)
                except FileExistsError:
                    fd = fs.open_file(path, os.O_RDWR)
                break
            except FileNotFoundError:
                if attempt == 2:
                    raise storage_error() from None
        try:
            deadline = time.monotonic() + wait_seconds
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError as exc:
                    if (
                        exc.errno not in {errno.EACCES, errno.EAGAIN}
                        or time.monotonic() >= deadline
                    ):
                        raise
                    time.sleep(min(0.01, max(0, deadline - time.monotonic())))
            self.fd = fd
        except OSError as exc:
            os.close(fd)
            if exc.errno in {errno.EACCES, errno.EAGAIN}:
                raise ChatError(
                    "资源正在使用，请稍后手动重试。", status, code
                ) from None
            raise storage_error(exc) from None

    def close(self):
        if self.fd >= 0:
            fd, self.fd = self.fd, -1
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def profile_lock(fs, instance_id, user_id):
    integer(user_id, 1, 2147483647)
    if not isinstance(instance_id, str) or not re.fullmatch(
        r"[a-z0-9][a-z0-9_-]{0,63}", instance_id
    ):
        raise ChatError("存储实例标识无效。", 503, "CHAT_CONFIG_INVALID")
    fs.mkdir(f"{instance_id}/.locks/users", parents=True)
    return FileLock(
        fs,
        f"{instance_id}/.locks/users/{user_id}.profile.lock",
        code="CHAT_PROFILE_BUSY",
    )


class ConversationLease:
    def __init__(self, store, scope, *, run=False):
        store._check_scope(scope)
        self.store, self.scope, self.run = store, scope, run
        self.poisoned = False
        self.closed = False
        self.reservation = None
        self._locks = []
        self.run_event_bytes = 0
        self._state = None
        root = f"{store.prefix}/.locks"
        try:
            if run:
                self._locks.append(
                    FileLock(
                        store.fs,
                        f"{root}/users/{scope.user_id}.run.lock",
                        code="CHAT_USER_BUSY",
                    )
                )
                for slot in range(store.settings.max_concurrent_runs):
                    try:
                        self._locks.append(
                            FileLock(
                                store.fs,
                                f"{root}/slots/{slot}.lock",
                                code="CHAT_CAPACITY_BUSY",
                                status=429,
                            )
                        )
                        break
                    except ChatError as exc:
                        if exc.code != "CHAT_CAPACITY_BUSY":
                            raise
                else:
                    raise ChatError("运行容量已满。", 429, "CHAT_CAPACITY_BUSY")
            identifier(scope.cid)
            self._locks.append(
                FileLock(
                    store.fs, f"{root}/conversations/{scope.user_id}.{scope.cid}.lock"
                )
            )
        except BaseException:
            self.close()
            raise

    def validate(self, store, scope):
        if self.closed or self.store is not store or self.scope != scope:
            raise ChatError("会话租约无效。", 503, "CHAT_STORAGE_UNAVAILABLE")
        if self.poisoned:
            raise ChatError("聊天记录需先恢复。", 503, "CHAT_STORAGE_CORRUPT")

    def close(self):
        if self.closed:
            return
        self.closed = True
        error = None
        try:
            if self.reservation is not None:
                self.reservation.close()
        except Exception as exc:
            error = exc
        finally:
            for lock in reversed(self._locks):
                try:
                    lock.close()
                except Exception as exc:
                    if error is None:
                        error = exc
            self._locks.clear()
        if error is not None:
            raise error

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
