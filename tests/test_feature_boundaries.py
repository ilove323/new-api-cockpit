"""Feature directories must not acquire cross-business dependencies."""

import ast
import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1] / "src" / "new_api_cockpit"
FORBIDDEN = {
    "app",
    "composition",
    "manual",
    "intelligence",
    "balance",
    "quota",
    "quota_schedule",
    "quota_timer",
    "report",
    "report_snapshots",
    "quality",
    "user_management",
    "operation_records",
    "notifications",
    "timers",
    "image_studio",
    "image_contracts",
    "image_transport",
    "image_results",
    "image_receipts",
}


class FeatureBoundariesTest(unittest.TestCase):
    def test_features_have_no_cross_business_imports(self):
        for feature in ("chat", "image_studio"):
            for path in (ROOT / "features" / feature).rglob("*.py"):
                package = ".".join(
                    ("new_api_cockpit", *path.relative_to(ROOT).parts[:-1])
                )
                tree = ast.parse(path.read_text())
                targets = []
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        targets.extend(a.name for a in node.names)
                    elif isinstance(node, ast.ImportFrom):
                        base = (
                            importlib.util.resolve_name(
                                "." * node.level + (node.module or ""), package
                            )
                            if node.level
                            else node.module or ""
                        )
                        targets.extend(
                            [base, *(base + "." + a.name for a in node.names)]
                        )
                    elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                        self.assertNotEqual(node.func.id, "__import__", str(path))
                for target in targets:
                    with self.subTest(file=str(path), target=target):
                        if target.startswith("new_api_cockpit."):
                            parts = target.split(".")
                            self.assertNotIn(parts[1], FORBIDDEN)
                            self.assertNotEqual(parts[1], "integrations")
                            if parts[1] == "features" and len(parts) > 2:
                                self.assertEqual(parts[2], feature)

    def test_platform_and_integrations_never_import_features(self):
        for folder in ("platform", "integrations"):
            for path in (ROOT / folder).rglob("*.py"):
                for node in ast.walk(ast.parse(path.read_text())):
                    if isinstance(node, ast.ImportFrom) and node.module:
                        self.assertNotIn("features", node.module.split("."), str(path))


if __name__ == "__main__":
    unittest.main()
