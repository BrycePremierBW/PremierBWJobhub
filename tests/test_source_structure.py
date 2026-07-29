import ast
import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]


class SourceStructureTests(unittest.TestCase):
    def test_application_modules_parse(self):
        for name in ("pb_jobhub_app.py", "pb_jobhub_visual_scheduler.py", "jobhub_core.py"):
            ast.parse((ROOT / name).read_text(encoding="utf-8"), filename=name)

    def test_no_duplicate_top_level_definitions(self):
        tree = ast.parse((ROOT / "pb_jobhub_app.py").read_text(encoding="utf-8"))
        seen = set()
        duplicates = set()
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                if node.name in seen:
                    duplicates.add(node.name)
                seen.add(node.name)
        self.assertEqual(duplicates, set())

    def test_sensitive_backup_files_are_not_part_of_release_package(self):
        unsafe = [
            str(item.relative_to(ROOT))
            for item in ROOT.rglob("*")
            if (
                item.name.endswith((".bak", ".pyc"))
                or item.name.startswith(("PB_JobHub_Install_", "RUN_INSTALL_"))
                or item.name in {"__pycache__", ".testdeps"}
            )
        ]
        self.assertEqual(unsafe, [])

    def test_main_app_has_mobile_viewport_guards(self):
        source = (ROOT / "pb_jobhub_app.py").read_text(encoding="utf-8")
        self.assertIn('initial_sidebar_state="auto"', source)
        self.assertIn("PB_JOBHUB_MOBILE_VIEWPORT_FIX", source)
        self.assertIn('div[data-testid="stHorizontalBlock"]', source)
        self.assertIn("max-width: 100vw !important", source)
        self.assertIn("-webkit-overflow-scrolling: touch !important", source)


if __name__ == "__main__":
    unittest.main()
