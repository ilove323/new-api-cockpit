"""Release installation assets are version-locked, public and build-free."""

import hashlib
import importlib.util
import json
from pathlib import Path
import re
import shutil
import tarfile
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "build_release_bundle", ROOT / "deploy/build_release_bundle.py"
)
bundle = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bundle)


class ReleaseBundleTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "checkout"
        self.root.mkdir()
        (self.root / "pyproject.toml").write_text('[project]\nversion = "9.8.7"\n')
        for relative in bundle.PUBLIC_FILES.values():
            target = self.root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / relative, target)
        migrations = self.root / "src/new_api_cockpit/migrations"
        migrations.mkdir(parents=True)
        for name in ("001_fixture.sql", "002_fixture.sql"):
            (migrations / name).write_text("-- synthetic migration\n")

    def build(self, directory="output", tag="v9.8.7"):
        return bundle.build(self.root, tag, Path(self.temp.name) / directory)

    def test_archive_contains_only_matching_public_runtime_files(self):
        (self.root / ".env").write_text("PRIVATE_FIXTURE_DO_NOT_PACKAGE=1\n")
        (self.root / "DEPLOYMENT.private.md").write_text("private fixture\n")
        archive, checksum = self.build()
        self.assertEqual(
            checksum.read_text(),
            hashlib.sha256(archive.read_bytes()).hexdigest()
            + "  "
            + archive.name
            + "\n",
        )
        with tarfile.open(archive) as tar:
            members = tar.getmembers()
            self.assertEqual(
                {m.name for m in members},
                {"install/" + name for name in bundle.PUBLIC_FILES}
                | {
                    "install/release.env",
                    "install/release.json",
                    "install/migrations.txt",
                },
            )
            self.assertTrue(all(m.isfile() and m.mode == 0o644 for m in members))
            metadata = json.load(tar.extractfile("install/release.json"))
            self.assertEqual(metadata["tag"], "v9.8.7")
            self.assertEqual(
                tar.extractfile("install/release.env").read().decode(),
                "COCKPIT_IMAGE=ghcr.io/ilove323/new-api-cockpit:9.8.7\n",
            )
            self.assertEqual(
                tar.extractfile("install/migrations.txt").read().decode(),
                "001_fixture.sql\n002_fixture.sql\n",
            )
            compose = tar.extractfile("install/compose.yml").read().decode()
            self.assertNotRegex(compose, r"(?m)^\s*build:")
            self.assertNotIn("./source", compose)
            self.assertIn("./install/postgres-init.sql", compose)
            self.assertIn("${COCKPIT_IMAGE:?", compose)

    def test_same_inputs_produce_identical_bundles(self):
        first, _ = self.build("one")
        second, _ = self.build("two")
        self.assertEqual(first.read_bytes(), second.read_bytes())

    def test_wrong_or_prerelease_tag_is_rejected_before_writing(self):
        for tag in ("v9.8.6", "v9.8.7-rc.1", "latest", "../9.8.7"):
            with self.subTest(tag=tag), self.assertRaises(ValueError):
                self.build(tag=tag)
        self.assertFalse((Path(self.temp.name) / "output").exists())

    def test_source_symlink_cannot_package_external_files(self):
        path = self.root / "nginx.conf.example"
        path.unlink()
        path.symlink_to(ROOT / "nginx.conf.example")
        with self.assertRaises(ValueError):
            self.build()

    def test_missing_migration_inventory_stops_packaging(self):
        shutil.rmtree(self.root / "src")
        with self.assertRaises(ValueError):
            self.build()

    def test_existing_bundle_is_not_overwritten(self):
        archive, _ = self.build()
        before = archive.read_bytes()
        with self.assertRaises(FileExistsError):
            self.build()
        self.assertEqual(archive.read_bytes(), before)

    def test_source_build_is_a_separate_override_not_release_runtime(self):
        text = (ROOT / "deploy/full-stack/compose.source.yml").read_text()
        roles = re.findall(r"^  ([a-z-]+):$", text, re.M)
        self.assertEqual(set(roles), {"statistics", "image-studio", "chat"})
        self.assertEqual(text.count('context: "./source"'), 3)
        self.assertNotIn("image:", text)
        workflow = (ROOT / ".github/workflows/release.yml").read_text()
        self.assertIn("python deploy/build_release_bundle.py", workflow)
        self.assertIn('"dist/$bundle.sha256"', workflow)

    def test_image_deployment_docs_never_clone_or_build_application_source(self):
        for name in ("deployment.md", "existing-installation.md"):
            text = (ROOT / "docs" / name).read_text()
            blocks = "\n".join(re.findall(r"```bash\n(.*?)```", text, re.S))
            with self.subTest(name=name):
                self.assertNotRegex(blocks, r"git\s+clone")
                self.assertNotRegex(blocks, r"docker compose[^\n]*\sbuild\b")
                self.assertNotIn("--build", blocks)
                self.assertIn("--no-build", blocks)
                self.assertIn("sha256sum -c", blocks)
        guide = (ROOT / "docs/deployment.md").read_text()
        self.assertLess(guide.index("sha256sum -c"), guide.index("tar --extract"))
        self.assertIn("install/migrations.txt", guide)
        self.assertIn("cat install/release.env >> .env", guide)
        source = (ROOT / "docs/deployment-source.md").read_text()
        self.assertIn("-f compose.source.yml build statistics", source)


if __name__ == "__main__":
    unittest.main()
