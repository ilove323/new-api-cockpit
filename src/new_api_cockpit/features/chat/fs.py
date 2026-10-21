"""Small descriptor-relative POSIX filesystem. No user-controlled physical paths.

All components, including the configured root's parents, are opened without
following symlinks. Existing non-directories must be singly-linked regular files.
"""

from contextlib import contextmanager
from datetime import datetime, timezone
import errno
import json
import os
import stat
from uuid import uuid4

from .errors import ChatError


def utcnow():
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )


def canonical_json(value):
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def strict_json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate field")
            result[key] = value
        return result

    def bad_constant(value):
        raise ValueError("non-finite JSON")

    return json.loads(raw, object_pairs_hook=pairs, parse_constant=bad_constant)


def storage_error(exc=None):
    full = isinstance(exc, OSError) and exc.errno in {errno.ENOSPC, errno.EDQUOT}
    return ChatError(
        "存储空间不足。" if full else "聊天存储不可用。",
        507 if full else 503,
        "CHAT_STORAGE_FULL" if full else "CHAT_STORAGE_UNAVAILABLE",
    )


class SafeFS:
    """An owned root fd; callers must close the instance when it is retired."""

    def __init__(self, root):
        root = os.fspath(root)
        if not os.path.isabs(root):
            raise storage_error()
        self.root = root
        self.fd = -1
        fd = os.open("/", self._directory_flags())
        try:
            for component in self._parts(root[1:]):
                nxt = os.open(component, self._directory_flags(), dir_fd=fd)
                os.close(fd)
                fd = nxt
            self._owned_private(fd)
            self.fd = fd
        except (OSError, ChatError) as exc:
            os.close(fd)
            raise storage_error(exc) from None

    @staticmethod
    def _owned_private(fd):
        info = os.fstat(fd)
        if info.st_uid != os.geteuid() or info.st_mode & 0o077:
            raise storage_error()

    @staticmethod
    def _parts(path):
        path = os.fspath(path)
        if not path:
            return []
        parts = path.split("/")
        if path.startswith("/") or any(
            not p or p in {".", ".."} or "\\" in p or "\x00" in p or "%" in p
            for p in parts
        ):
            raise ChatError("存储路径无效。", 400, "CHAT_INVALID_REQUEST")
        return parts

    @staticmethod
    def _directory_flags():
        return os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC

    @staticmethod
    def _regular(fd):
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise storage_error()
        SafeFS._owned_private(fd)
        return info

    @contextmanager
    def directory(self, path=""):
        fd = os.dup(self.fd)
        try:
            for component in self._parts(path):
                nxt = os.open(component, self._directory_flags(), dir_fd=fd)
                try:
                    self._owned_private(nxt)
                except BaseException:
                    os.close(nxt)
                    raise
                os.close(fd)
                fd = nxt
            yield fd
        finally:
            os.close(fd)

    @contextmanager
    def parent(self, path):
        parts = self._parts(path)
        if not parts:
            raise ChatError("存储路径无效。")
        with self.directory("/".join(parts[:-1])) as fd:
            yield fd, parts[-1]

    def open_file(self, path, flags=os.O_RDONLY, mode=0o600):
        """Caller owns fd; O_NONBLOCK avoids blocking on a hostile FIFO."""
        try:
            with self.parent(path) as (parent, name):
                truncate = bool(flags & os.O_TRUNC)
                fd = os.open(
                    name,
                    (flags & ~os.O_TRUNC)
                    | os.O_NOFOLLOW
                    | os.O_CLOEXEC
                    | os.O_NONBLOCK,
                    mode,
                    dir_fd=parent,
                )
                try:
                    self._regular(fd)
                    if truncate:
                        os.ftruncate(fd, 0)
                    return fd
                except BaseException:
                    os.close(fd)
                    raise
        except (FileNotFoundError, FileExistsError):
            raise
        except OSError as exc:
            raise storage_error(exc) from None

    def read_bytes(self, path, max_bytes=67108864):
        fd = self.open_file(path)
        try:
            if self._regular(fd).st_size > max_bytes:
                raise storage_error()
            chunks, size = [], 0
            while True:
                data = os.read(fd, min(65536, max_bytes + 1 - size))
                if not data:
                    break
                size += len(data)
                if size > max_bytes:
                    raise storage_error()
                chunks.append(data)
            self._regular(fd)
            return b"".join(chunks)
        except OSError as exc:
            raise storage_error(exc) from None
        finally:
            os.close(fd)

    def read_json(self, path, max_bytes=1048576):
        try:
            return strict_json(self.read_bytes(path, max_bytes).decode("utf-8"))
        except (UnicodeError, ValueError, RecursionError):
            raise ChatError("聊天记录需要修复。", 503, "CHAT_STORAGE_CORRUPT") from None

    def stat(self, path):
        with self.parent(path) as (fd, name):
            info = os.stat(name, dir_fd=fd, follow_symlinks=False)
            if not stat.S_ISDIR(info.st_mode) and (
                not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
            ):
                raise storage_error()
            return info

    def exists(self, path):
        try:
            self.stat(path)
            return True
        except FileNotFoundError:
            return False
        except OSError as exc:
            raise storage_error(exc) from None

    def listdir(self, path=""):
        try:
            with self.directory(path) as fd:
                return sorted(os.listdir(fd))
        except FileNotFoundError:
            raise
        except OSError as exc:
            raise storage_error(exc) from None

    def mkdir(self, path, parents=False):
        parts = self._parts(path)
        try:
            if parents:
                current = ""
                for part in parts:
                    current = f"{current}/{part}" if current else part
                    self.mkdir(current)
                return
            with self.parent(path) as (fd, name):
                try:
                    os.mkdir(name, 0o700, dir_fd=fd)
                    os.fsync(fd)
                except FileExistsError:
                    check = os.open(name, self._directory_flags(), dir_fd=fd)
                    try:
                        self._owned_private(check)
                    finally:
                        os.close(check)
        except OSError as exc:
            raise storage_error(exc) from None

    @staticmethod
    def write_all(fd, data):
        view = memoryview(data)
        while view:
            written = os.write(fd, view)
            if written <= 0:
                raise OSError(errno.EIO, "short write")
            view = view[written:]

    def atomic_write(self, path, data, *, exclusive=False):
        if not isinstance(data, bytes):
            raise TypeError("atomic_write requires bytes")
        temp = f".atomic-{uuid4()}"
        try:
            with self.parent(path) as (parent, name):
                # Validate existing targets before replacement; never mask links.
                try:
                    info = os.stat(name, dir_fd=parent, follow_symlinks=False)
                    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                        raise storage_error()
                    if exclusive:
                        raise FileExistsError()
                except FileNotFoundError:
                    pass
                fd = os.open(
                    temp,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW,
                    0o600,
                    dir_fd=parent,
                )
                try:
                    try:
                        self.write_all(fd, data)
                        os.fsync(fd)
                    finally:
                        os.close(fd)
                    if exclusive:
                        # linkat is the portable no-replace publication primitive.
                        # Its transient nlink=2 exists only while no event references it.
                        os.link(
                            temp,
                            name,
                            src_dir_fd=parent,
                            dst_dir_fd=parent,
                            follow_symlinks=False,
                        )
                        os.unlink(temp, dir_fd=parent)
                    else:
                        os.replace(temp, name, src_dir_fd=parent, dst_dir_fd=parent)
                    os.fsync(parent)
                finally:
                    try:
                        os.unlink(temp, dir_fd=parent)
                    except FileNotFoundError:
                        pass
        except FileExistsError:
            raise
        except OSError as exc:
            raise storage_error(exc) from None

    def write_json(self, path, value, *, exclusive=False):
        self.atomic_write(path, canonical_json(value), exclusive=exclusive)

    def unlink(self, path, missing_ok=False):
        try:
            with self.parent(path) as (fd, name):
                info = os.stat(name, dir_fd=fd, follow_symlinks=False)
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                    raise storage_error()
                os.unlink(name, dir_fd=fd)
                os.fsync(fd)
        except FileNotFoundError:
            if not missing_ok:
                raise
        except OSError as exc:
            raise storage_error(exc) from None

    def rename(self, src, dst, *, replace=False):
        try:
            with self.parent(src) as (sf, sn), self.parent(dst) as (df, dn):
                self.stat(src)
                if self.exists(dst):
                    if not replace:
                        raise FileExistsError()
                    self.stat(dst)
                os.rename(sn, dn, src_dir_fd=sf, dst_dir_fd=df)
                os.fsync(sf)
                os.fsync(df)
        except FileExistsError:
            raise
        except OSError as exc:
            raise storage_error(exc) from None

    def remove_tree(self, path):
        """No-follow recursive removal, rejecting anomalous entries, not traversing them."""
        with self.directory(path) as fd:
            for name in os.listdir(fd):
                child = f"{path}/{name}"
                info = os.stat(name, dir_fd=fd, follow_symlinks=False)
                if stat.S_ISDIR(info.st_mode):
                    self.remove_tree(child)
                else:
                    self.unlink(child)
        with self.parent(path) as (fd, name):
            os.rmdir(name, dir_fd=fd)
            os.fsync(fd)

    def disk_usage(self, path=""):
        with self.directory(path) as fd:
            total = 0
            for name in os.listdir(fd):
                info = os.stat(name, dir_fd=fd, follow_symlinks=False)
                if stat.S_ISDIR(info.st_mode):
                    total += self.disk_usage(f"{path}/{name}" if path else name)
                elif stat.S_ISREG(info.st_mode) and info.st_nlink == 1:
                    total += info.st_size
                else:
                    raise storage_error()
            return total

    def free_bytes(self):
        info = os.fstatvfs(self.fd)
        return info.f_bavail * info.f_frsize

    def close(self):
        if self.fd >= 0:
            os.close(self.fd)
            self.fd = -1

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
