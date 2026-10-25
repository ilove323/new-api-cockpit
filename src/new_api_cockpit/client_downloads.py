"""Immutable, build-time client packages. No external downloads or user paths."""

from dataclasses import dataclass
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import re
import zipfile


BUNDLE_DIRECTORY = Path(__file__).with_name("bundled_clients")


@dataclass(frozen=True)
class VSCodeRelease:
    version: str
    filename: str
    size: int
    sha256: str
    path: Path

    @property
    def display_size(self):
        return f"{self.size / 1024:.1f} KB"


@lru_cache(maxsize=1)
def vscode_release():
    """Expose only a verified local VSIX; missing/corrupt builds stay unavailable."""
    try:
        manifest = BUNDLE_DIRECTORY / "vscode-extension.json"
        if manifest.stat().st_size > 4096:
            return None
        data = json.loads(manifest.read_text(encoding="utf-8"))
        version = data["version"]
        if not isinstance(version, str) or not re.fullmatch(r"\d+\.\d+\.\d+", version):
            return None
        filename = f"new-api-cockpit-balance-{version}.vsix"
        if data["filename"] != filename:
            return None
        path = BUNDLE_DIRECTORY / filename
        size = data["size"]
        if type(size) is not int or not 0 < size <= 64 * 1024 * 1024:
            return None
        if path.stat().st_size != size:
            return None
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if data["sha256"] != digest:
            return None
        with zipfile.ZipFile(path) as archive:
            entry = archive.getinfo("extension/package.json")
            if entry.file_size > 64 * 1024:
                return None
            package = json.loads(archive.read(entry))
            if (
                not isinstance(package, dict)
                or package.get("name") != "new-api-cockpit-balance"
                or package.get("publisher") != "ilove323"
                or package.get("version") != version
            ):
                return None
        return VSCodeRelease(version, filename, size, digest, path)
    except (OSError, ValueError, KeyError, TypeError, RuntimeError, zipfile.BadZipFile):
        return None
