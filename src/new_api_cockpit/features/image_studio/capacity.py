"""Image-only capacity leases and fail-closed database epoch checks."""

from contextlib import contextmanager
import os
from threading import Lock

import psycopg

from .ports import StudioError

# Persisted PostgreSQL advisory lock identities; never renumber on a move.
IMAGE_STUDIO_USER_NAMESPACE = 90216327
IMAGE_STUDIO_SLOT_NAMESPACE = 90216328


_guard = Lock()
_database_epoch = None
_capacity_failed = False


def process_role():
    return os.environ.get("COCKPIT_PROCESS_ROLE", "web")


def _monitor_connection():
    if not os.environ.get("MONITOR_DATABASE_URL"):
        raise StudioError("生图工作站需要配置监控数据库以控制并发。", 503)
    return psycopg.connect(
        os.environ["MONITOR_DATABASE_URL"],
        connect_timeout=3,
        autocommit=True,
        options="-c statement_timeout=2000 -c application_name=cockpit-image-request",
    )


def _check_epoch(conn):
    global _database_epoch, _capacity_failed
    epoch = conn.execute("SELECT pg_postmaster_start_time()").fetchone()[0]
    with _guard:
        if _capacity_failed or (
            _database_epoch is not None and epoch != _database_epoch
        ):
            _capacity_failed = True
            raise StudioError("图片请求池需要重新启动，请稍后再试。", 503)
        _database_epoch = epoch


def initialize_capacity():
    """Read-only startup check: image workers never run schema migrations/timers."""
    global _capacity_failed
    try:
        with _monitor_connection() as conn:
            _check_epoch(conn)
        return True
    except (psycopg.Error, StudioError):
        with _guard:
            _capacity_failed = True
        return False


def health():
    try:
        with _monitor_connection() as conn:
            _check_epoch(conn)
        return {"ready": True, "role": "image"}
    except (psycopg.Error, StudioError):
        return {"ready": False, "role": "image"}


@contextmanager
def capacity(user):
    """Acquire before multipart parsing. Never wait or return a locked connection."""
    global _capacity_failed
    if process_role() != "image":
        raise StudioError("图片请求池尚未接入，请检查工作站的反向代理配置。", 503)
    if type(user.get("id")) is not int or not 1 <= user["id"] <= 2147483647:
        raise StudioError("当前账号无效，请重新登录。", 403)
    if _capacity_failed:
        raise StudioError("图片请求池暂不可用，请重新启动后再试。", 503)
    conn = None
    owned = False
    slot = None
    try:
        conn = _monitor_connection()
        _check_epoch(conn)
        owned = conn.execute(
            "SELECT pg_try_advisory_lock(%s,%s)",
            (IMAGE_STUDIO_USER_NAMESPACE, user["id"]),
        ).fetchone()[0]
        if not owned:
            raise StudioError(
                "当前账号已有图片请求正在执行。", 429, "IMAGE_STUDIO_BUSY"
            )
        for index in range(2):
            if conn.execute(
                "SELECT pg_try_advisory_lock(%s,%s)",
                (IMAGE_STUDIO_SLOT_NAMESPACE, index),
            ).fetchone()[0]:
                slot = index
                break
        if slot is None:
            raise StudioError(
                "图片工作站正在忙，请稍后再试。", 429, "IMAGE_STUDIO_BUSY"
            )
        yield
    except psycopg.Error:
        with _guard:
            _capacity_failed = True
        raise StudioError("图片请求池的并发控制连接不可用。", 503) from None
    finally:
        if conn is not None:
            try:
                if slot is not None:
                    conn.execute(
                        "SELECT pg_advisory_unlock(%s,%s)",
                        (IMAGE_STUDIO_SLOT_NAMESPACE, slot),
                    )
                if owned:
                    conn.execute(
                        "SELECT pg_advisory_unlock(%s,%s)",
                        (IMAGE_STUDIO_USER_NAMESPACE, user["id"]),
                    )
            except psycopg.Error:
                with _guard:
                    _capacity_failed = True
            finally:
                conn.close()
