"""Create a NEW instance's private configuration; never reset existing data.

Uses only the Python standard library (3.6+). Run as root on the target host.
No administrator password or model API key is generated, accepted or saved.
"""

import argparse
import base64
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
from uuid import uuid4


def initialize(root, scheme, instance_id, subnet):
    root = Path(root)
    if not root.is_absolute() or root == Path("/") or root.is_symlink():
        raise ValueError("Use an absolute, non-symlink instance directory")
    if root.resolve() != root:
        raise ValueError("Instance path must not contain symlink ancestors")
    if scheme not in {"http", "https"} or not re.fullmatch(
        r"[a-z0-9][a-z0-9_-]{0,63}", instance_id
    ):
        raise ValueError("Invalid scheme or stable instance ID")
    network = ipaddress.ip_network(subnet, strict=True)
    private_blocks = [
        ipaddress.ip_network(value)
        for value in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
    ]
    if (
        network.version != 4
        or network.prefixlen != 24
        or not any(
            network.network_address in block and network.broadcast_address in block
            for block in private_blocks
        )
    ):
        raise ValueError("Choose a non-overlapping private IPv4 /24 network")
    if any(
        (root / name).is_symlink()
        for name in ("config", "data", "logs", "backups", "releases")
    ):
        raise ValueError("Managed directories must not be symlinks")
    config = root / "config"
    if (config.exists() and any(config.iterdir())) or (root / ".env").exists():
        raise ValueError("Configuration exists; refusing to replace instance secrets")
    for name in ("data", "logs"):
        if (root / name).exists() and any((root / name).iterdir()):
            raise ValueError("Data exists; use the upgrade/restore procedure instead")
    root.mkdir(parents=True, exist_ok=True)
    root.chmod(0o700)
    for name in (
        "config",
        "config/nginx",
        "data",
        "data/postgres",
        "data/new-api",
        "data/redis",
        "data/image-studio-receipts",
        "data/chat",
        "logs",
        "logs/new-api",
        "backups",
        "releases",
    ):
        directory = root / name
        directory.mkdir(parents=True, exist_ok=True)
        directory.chmod(0o700)
    for name in ("redis", "image-studio-receipts"):
        os.chown(str(root / "data" / name), 999, 1000)
    os.chown(str(root / "data/chat"), 10001, 10001)

    def write(path, data, mode=0o600, owner=None):
        path = Path(path)
        with path.open("xb") as stream:
            stream.write(data.encode())
            stream.flush()
            os.fsync(stream.fileno())
        path.chmod(mode)
        if owner:
            os.chown(str(path), *owner)

    def env(path, values):
        write(
            path,
            "".join(key + "=" + str(value) + "\n" for key, value in values.items()),
        )

    admin, source, reader, monitor, cache, receipts = (
        secrets.token_hex(32) for _ in range(6)
    )
    secure = "true" if scheme == "https" else "false"
    env(root / ".env", {"APP_SUBNET": str(network)})
    env(
        config / "postgres.env",
        {
            "POSTGRES_USER": "postgres",
            "POSTGRES_DB": "postgres",
            "POSTGRES_PASSWORD": admin,
            "POSTGRES_INITDB_ARGS": "--auth-host=scram-sha-256",
            "SOURCE_DB_PASSWORD": source,
            "READER_DB_PASSWORD": reader,
            "MONITOR_DB_PASSWORD": monitor,
            "TZ": "Asia/Shanghai",
        },
    )
    env(
        config / "new-api.env",
        {
            "SQL_DSN": "postgresql://newapi_owner:"
            + source
            + "@postgres:5432/new_api?sslmode=disable",
            "REDIS_CONN_STRING": "redis://:" + cache + "@redis:6379/0",
            "SESSION_SECRET": secrets.token_hex(48),
            "CRYPTO_SECRET": secrets.token_hex(48),
            "SESSION_COOKIE_SECURE": secure,
            "TRUSTED_PROXIES": str(network) + ",127.0.0.1/32",
            "GENERATE_DEFAULT_TOKEN": "false",
            "ERROR_LOG_ENABLED": "true",
            "BATCH_UPDATE_ENABLED": "true",
            "SQL_MAX_OPEN_CONNS": "50",
            "SQL_MAX_IDLE_CONNS": "10",
            "TZ": "Asia/Shanghai",
        },
    )
    shared = {
        "NEW_API_INTERNAL_URL": "http://new-api:3000",
        "COCKPIT_COOKIE_SECURE": secure,
        "CHAT_ENABLED": "true",
        "CHAT_INSTANCE_ID": instance_id,
        "CHAT_DATA_DIR": "/app/data/chat",
        "CHAT_CAPABILITIES_FILE": "/app/config/chat-capabilities.json",
        "CHAT_MAX_CONCURRENT_RUNS": "2",
        "CHAT_TOTAL_STORAGE_BYTES": "10737418240",
        "TZ": "Asia/Shanghai",
    }
    database = {
        "PGHOST": "postgres",
        "PGPORT": "5432",
        "PGDATABASE": "new_api",
        "PGUSER": "cockpit_reader",
        "PGPASSWORD": reader,
        "MONITOR_DATABASE_URL": "postgresql://cockpit_monitor:"
        + monitor
        + "@postgres:5432/new_api_cockpit?sslmode=disable",
        "IMAGE_STUDIO_CAPABILITIES_FILE": "/app/config/image-studio-capabilities.json",
        "IMAGE_STUDIO_REDIS_URL": "redis://:"
        + receipts
        + "@image-studio-receipts:6379/0",
        "IMAGE_STUDIO_RECEIPT_NAMESPACE": "cockpit:image-receipts:v1",
        "IMAGE_STUDIO_RECEIPT_TTL": "86400",
    }
    for filename, role in (
        ("web.env", "web"),
        ("image.env", "image"),
        ("chat.env", "chat"),
    ):
        values = dict(shared)
        values["COCKPIT_PROCESS_ROLE"] = role
        if role != "chat":
            values.update(database)
        if role == "web":
            values["COCKPIT_MCP_ENABLED"] = "true"
            values["NOTIFICATION_ENCRYPTION_KEY"] = base64.urlsafe_b64encode(
                os.urandom(32)
            ).decode()
        env(config / filename, values)
    for name, password, fsync, memory, policy in (
        ("redis", cache, "everysec", "128mb", "allkeys-lru"),
        ("image-studio-receipts", receipts, "always", "64mb", "noeviction"),
    ):
        text = (
            "bind 0.0.0.0\nprotected-mode yes\nport 6379\ndir /data\n"
            "appendonly yes\nappendfsync "
            + fsync
            + "\nmaxmemory "
            + memory
            + "\nmaxmemory-policy "
            + policy
            + '\nsave ""\nrequirepass '
            + password
            + "\n"
        )
        write(config / (name + ".conf"), text, 0o400, (999, 1000))
        env(config / (name + "-health.env"), {"REDISCLI_AUTH": password})
    for filename, value in (
        ("chat-capabilities.json", {"schema_version": 2, "models": {}}),
        (
            "image-studio-capabilities.json",
            {"schema": 2, "profiles": {}, "channels": {}, "models": {}},
        ),
    ):
        write(config / filename, json.dumps(value, indent=2) + "\n", 0o644)
    write(
        config / "instance.json",
        json.dumps(
            {"instance_id": instance_id, "scheme": scheme, "subnet": str(network)}
        ),
    )
    return {"configuration_created": True, "instance_id": instance_id, "scheme": scheme}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default="/root/new-api")
    parser.add_argument("--scheme", choices=("http", "https"), required=True)
    parser.add_argument("--instance-id", default="cockpit-" + uuid4().hex)
    parser.add_argument("--subnet", default="172.28.241.0/24")
    args = parser.parse_args()
    if os.geteuid() != 0:
        parser.error("Run as root on the target server, not on your workstation")
    os.umask(0o077)
    print(json.dumps(initialize(args.root, args.scheme, args.instance_id, args.subnet)))


if __name__ == "__main__":
    main()
