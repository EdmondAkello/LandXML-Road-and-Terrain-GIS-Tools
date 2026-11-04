"""Release packaging includes new runtime modules before they are staged."""

from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from tools import build_release


class ReleaseTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("git"), "Release packaging test requires the Git CLI")
    def test_untracked_runtime_module_is_packaged(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            subprocess.run(["git", "init", "--quiet", str(root)], check=True)
            (root / ".gitignore").write_text("private/\n", encoding="utf-8")
            (root / "metadata.txt").write_text("[general]\nversion=1.0\n", encoding="utf-8")
            (root / "landxml").mkdir()
            module = root / "landxml" / "quantity_units.py"
            module.write_text("VOLUME_UNIT = 'CY'\n", encoding="utf-8")
            (root / "private").mkdir()
            (root / "private" / "notes.txt").write_text("excluded", encoding="utf-8")
            (root / "AGENTS.md").write_text("contributor instructions", encoding="utf-8")
            (root / "tests").mkdir()
            (root / "tests" / "test_example.py").write_text("excluded", encoding="utf-8")
            (root / "graphify-out").mkdir()
            for name in ("graph.json", "graph.html"):
                (root / "graphify-out" / name).write_text("developer snapshot", encoding="utf-8")
            subprocess.run(
                ["git", "add", ".gitignore", "metadata.txt", "AGENTS.md", "graphify-out/graph.json"],
                cwd=root, check=True,
            )
            with patch.object(build_release, "ROOT", str(root)):
                archive = build_release.build(str(root / "dist"))
            with zipfile.ZipFile(archive) as zipped:
                self.assertEqual(set(zipped.namelist()), {
                    f"{build_release.PACKAGE}/metadata.txt",
                    f"{build_release.PACKAGE}/landxml/quantity_units.py",
                })
                self.assertEqual(zipped.read(f"{build_release.PACKAGE}/landxml/quantity_units.py"), module.read_bytes())
