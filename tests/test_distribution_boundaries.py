"""Build/archive only synthetic checkouts; never read private workspace files."""

import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class DistributionBoundariesTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="cockpit-distribution-fixture-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / "checkout"
        self.root.mkdir()
        for filename in ("MANIFEST.in", ".gitattributes"):
            shutil.copyfile(ROOT / filename, self.root / filename)
        self.private = (
            ".env",
            ".env.production",
            "SITES.local.md",
            "sites.local.md",
            "DEPLOYMENT.private.md",
            "frontend/studio/.env",
            "frontend/studio/.env.development",
            "frontend/studio/settings.local.json",
            "frontend/studio/notes.private.md",
            "frontend/studio/fixture.pem",
            "frontend/studio/secrets/auth.json",
            "frontend/studio/credentials/auth.json",
        )
        self.public = (".env.example", "frontend/studio/src/public.ts")
        for name in self.private + self.public:
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("synthetic fixture only\n")

    def execute(self, *arguments, env=None):
        result = subprocess.run(
            arguments,
            cwd=self.root,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=60,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result.stdout.strip()

    def assert_members(self, names):
        for private in self.private:
            self.assertNotIn(private, names)
        for public in self.public:
            self.assertIn(public, names)

    @unittest.skipUnless(importlib.util.find_spec("build"), "Requires build tooling")
    def test_sdist_excludes_untracked_private_files_from_the_frontend_graft(self):
        (self.root / "pyproject.toml").write_text(
            '[build-system]\nrequires=["setuptools>=77"]\n'
            'build-backend="setuptools.build_meta"\n'
            '[project]\nname="distribution-fixture"\nversion="1.0.0"\n'
            '[tool.setuptools.packages.find]\nwhere=["src"]\n'
        )
        package = self.root / "src/distribution_fixture"
        package.mkdir(parents=True)
        (package / "__init__.py").write_text("")
        self.execute(
            sys.executable,
            "-m",
            "build",
            "--sdist",
            "--no-isolation",
            env=dict(os.environ, PIP_NO_INDEX="1"),
        )
        with tarfile.open(next((self.root / "dist").glob("*.tar.gz"))) as archive:
            names = {
                name.split("/", 1)[1] for name in archive.getnames() if "/" in name
            }
        self.assert_members(names)

    @unittest.skipUnless(shutil.which("git"), "Requires Git archive tooling")
    def test_git_archive_excludes_even_force_added_private_paths(self):
        environment = dict(
            os.environ, GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull
        )
        self.execute("git", "init", "--template=", env=environment)
        self.execute("git", "add", "--force", ".", env=environment)
        tree = self.execute("git", "write-tree", env=environment)
        self.execute("git", "archive", "--output=fixture.tar", tree, env=environment)
        with tarfile.open(self.root / "fixture.tar") as archive:
            self.assert_members(set(archive.getnames()))


if __name__ == "__main__":
    unittest.main()
