"""Package public, version-matched install files without application source/secrets."""

import argparse
import gzip
import hashlib
from io import BytesIO
import json
from pathlib import Path
import re
import tarfile
import tomllib

PUBLIC_FILES = {
    name: "deploy/full-stack/" + name
    for name in (
        "compose.yml",
        "init-config.py",
        "bootstrap-admin.py",
        "postgres-init.sql",
        "source-grants.sql",
        "nginx-http.conf",
    )
} | {
    "nginx.conf.example": "nginx.conf.example",
    "image-studio-capabilities.example.json": "image-studio-capabilities.example.json",
    "source_pat_function.sql": "sql/source_pat_function.sql",
}


def build(root, tag, output):
    root, output = Path(root).resolve(), Path(output)
    version = tomllib.loads((root / "pyproject.toml").read_text())["project"]["version"]
    if not re.fullmatch(r"v\d+\.\d+\.\d+", tag) or tag != "v" + version:
        raise ValueError("Stable release tag must match pyproject.toml")
    files = {}
    for name, relative in PUBLIC_FILES.items():
        source = root / relative
        if source.is_symlink() or not source.resolve().is_relative_to(root):
            raise ValueError("Public install files must stay within the checkout")
        files[name] = source.read_bytes()
    migrations = sorted(
        p.name for p in (root / "src/new_api_cockpit/migrations").glob("*.sql")
    )
    if not migrations:
        raise ValueError("Missing monitoring migration inventory")
    image = "ghcr.io/ilove323/new-api-cockpit:" + version
    files["release.env"] = ("COCKPIT_IMAGE=" + image + "\n").encode()
    files["release.json"] = (
        json.dumps({"tag": tag, "version": version, "cockpit_image": image}, indent=2)
        + "\n"
    ).encode()
    files["migrations.txt"] = ("\n".join(migrations) + "\n").encode()
    output.mkdir(parents=True, exist_ok=True)
    archive = output / ("new-api-cockpit-" + tag + "-deploy.tar.gz")
    # Fixed headers make the same public inputs produce byte-identical bundles.
    with archive.open("xb") as raw:
        with gzip.GzipFile(fileobj=raw, filename="", mode="wb", mtime=0) as compressed:
            with tarfile.open(fileobj=compressed, mode="w") as tar:
                for name, body in sorted(files.items()):
                    info = tarfile.TarInfo("install/" + name)
                    info.size, info.mode, info.mtime = len(body), 0o644, 0
                    tar.addfile(info, BytesIO(body))
    checksum = archive.with_name(archive.name + ".sha256")
    with checksum.open("x") as stream:
        stream.write(
            hashlib.sha256(archive.read_bytes()).hexdigest()
            + "  "
            + archive.name
            + "\n"
        )
    return archive, checksum


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--output-dir", type=Path, default=Path("dist"))
    args = parser.parse_args()
    archive, checksum = build(
        Path(__file__).resolve().parents[1], args.tag, args.output_dir
    )
    print(json.dumps({"archive": str(archive), "checksum": str(checksum)}))


if __name__ == "__main__":
    main()
