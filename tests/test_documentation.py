"""Repository documentation boundaries; no network or production data."""

from pathlib import Path
import re
import unittest
from urllib.parse import unquote


ROOT = Path(__file__).resolve().parents[1]


def public_markdown():
    return [
        *(p for p in ROOT.glob("*.md") if p.name != "DEPLOYMENT.private.md"),
        *(ROOT / "docs").rglob("*.md"),
        ROOT / ".github/pull_request_template.md",
    ]


def heading_ids(text):
    counts = {}
    result = set()
    for heading in re.findall(r"^#{1,6}\s+(.+)$", text, flags=re.MULTILINE):
        slug = re.sub(r"[^\w\s-]", "", heading.lower()).replace(" ", "-")
        count = counts.get(slug, 0)
        counts[slug] = count + 1
        result.add(slug + (f"-{count}" if count else ""))
    return result


class DocumentationTest(unittest.TestCase):
    def test_local_document_links_and_sections_exist(self):
        for document in public_markdown():
            for link in re.findall(r"\]\(([^)\s]+)\)", document.read_text()):
                if link.startswith(("https:", "http:", "mailto:")):
                    continue
                filename, _, fragment = unquote(link).partition("#")
                target = document.parent / filename if filename else document
                with self.subTest(document=document.name, link=link):
                    self.assertTrue(target.exists(), "broken local document link")
                    if fragment and target.suffix == ".md":
                        self.assertIn(fragment, heading_ids(target.read_text()))

    def test_history_and_unimplemented_plans_are_not_current_documents(self):
        self.assertFalse((ROOT / "CHANGELOG.md").exists())
        self.assertFalse((ROOT / "docs/optimization-plan.md").exists())
        self.assertEqual(list((ROOT / "docs/releases").glob("*.md")), [])
        for document in public_markdown():
            with self.subTest(document=document.name):
                self.assertNotRegex(
                    document.read_text(),
                    r"CHANGELOG\.md|optimization-plan\.md|releases/v\d",
                )

    def test_current_architecture_documents_existing_tables_and_migrations(self):
        architecture = (ROOT / "docs/architecture.md").read_text()
        migrations = sorted((ROOT / "src/new_api_statistics/migrations").glob("*.sql"))
        self.assertEqual(
            [p.name[:3] for p in migrations],
            [f"{i:03}" for i in range(1, 9)],
        )
        for migration in migrations:
            tables = re.findall(
                r"CREATE TABLE\s+(?:IF NOT EXISTS\s+)?(\w+)",
                migration.read_text(),
                flags=re.IGNORECASE,
            )
            for table in tables:
                with self.subTest(table=table):
                    self.assertIn(f"`{table}`", architecture)
        for filename in ("docker-compose.yml", "compose.release.yml"):
            compose = (ROOT / filename).read_text()
            self.assertNotIn("  quota-worker:", compose)
            self.assertNotIn("  balance-worker:", compose)

    def test_release_workflow_keeps_history_on_github_not_in_documents(self):
        workflow = (ROOT / ".github/workflows/release.yml").read_text()
        self.assertIn('gh release create "$RELEASE_TAG"', workflow)
        self.assertIn("--verify-tag --generate-notes", workflow)
        self.assertNotIn("docs/releases/", workflow)
        self.assertNotIn("--notes-file", workflow)


if __name__ == "__main__":
    unittest.main()
