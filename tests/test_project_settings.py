"""Project-local Processing inputs, exercised with real QGIS dialogs."""

# ruff: noqa: E402 - bootstrap QGIS Processing and the plugin package

import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from qgis.core import QgsApplication, QgsCoordinateReferenceSystem, QgsProject

ROOT = Path(__file__).resolve().parents[1]
if "landxml_plugin" not in sys.modules:
    spec = importlib.util.spec_from_file_location(
        "landxml_plugin", ROOT / "__init__.py", submodule_search_locations=[str(ROOT)]
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)


class ProjectSettingsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QgsApplication.instance()
        if cls.app is None:
            cls.app = QgsApplication([], True)
            cls.app.initQgis()
        sys.path.append(str(Path(QgsApplication.pkgDataPath()) / "python" / "plugins"))
        from landxml_plugin.provider import LandXMLTinToGeoTIFFProvider

        cls.provider = LandXMLTinToGeoTIFFProvider()
        cls.provider.loadAlgorithms()

    def setUp(self):
        self.project = QgsProject.instance()
        self.project.clear()
        self.dialogs = []

    def tearDown(self):
        for dialog in self.dialogs:
            dialog.close()
            dialog.deleteLater()
        self.project.clear()

    def dialog(self, name="prepare_landxml", values=None):
        algorithm = next(a for a in self.provider.algorithms() if a.name() == name).create()
        dialog = algorithm.createCustomParametersWidget()
        if values is not None:
            dialog.setParameters(values)
        self.dialogs.append(dialog)
        return dialog

    def value(self, dialog, name):
        return dialog.mainWidget().wrappers[name].parameterValue()

    def saved(self, name="prepare_landxml"):
        return json.loads(self.project.readEntry("LandXML", "processing/" + name, "{}")[0])

    def test_round_trip_project_file_and_input_types(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "road.qgz"
            terrain = Path(folder) / "terrain.xml"
            terrain.write_text("<LandXML/>")
            self.project.setFileName(str(path))
            dialog = self.dialog(values={
                "INPUT": str(terrain), "EXISTING_FILE": str(terrain),
                "ALIGNMENT": "Road A", "EXISTING_SURFACE": "Ground",
                "PROFILE_INTERVAL": 12.5, "SECTION_INTERVAL": 25,
                "HORIZONTAL_UNIT": 3, "VERTICAL_UNIT": 3, "CONFIRM_MATCHING": True,
                "OUTPUT": str(Path(folder) / "prepared.xml"),
            })
            dialog.algorithmFinished.emit(True, {})
            saved = self.saved()
            self.assertEqual(saved["INPUT"], "./terrain.xml")
            self.assertNotIn("OUTPUT", saved)
            self.assertNotIn("CONFIRM_MATCHING", saved)
            self.assertTrue(self.project.isDirty())
            self.assertTrue(self.project.write())
            self.project.clear()
            self.assertTrue(self.project.read(str(path)))
            restored = self.dialog(values={})
            self.assertEqual(Path(self.value(restored, "INPUT")), terrain)
            self.assertEqual(Path(self.value(restored, "EXISTING_FILE")), terrain)
            self.assertEqual(self.value(restored, "ALIGNMENT"), "Road A")
            self.assertEqual(self.value(restored, "EXISTING_SURFACE"), "Ground")
            self.assertEqual(self.value(restored, "SECTION_INTERVAL"), 25)
            self.assertEqual(self.value(restored, "PROFILE_INTERVAL"), 12.5)
            self.assertEqual(self.value(restored, "HORIZONTAL_UNIT"), 3)
            self.assertFalse(self.value(restored, "CONFIRM_MATCHING"))
            self.assertNotEqual(self.value(restored, "OUTPUT"), str(Path(folder) / "prepared.xml"))

    def test_explicit_parameters_override_saved_inputs(self):
        self.dialog(values={"ALIGNMENT": "Road A", "SECTION_INTERVAL": 25}).algorithmFinished.emit(True, {})
        dialog = self.dialog(values={"ALIGNMENT": "Road B"})
        self.assertEqual(self.value(dialog, "ALIGNMENT"), "Road B")
        self.assertEqual(self.value(dialog, "SECTION_INTERVAL"), 25)

    def test_unsuccessful_and_closed_dialogs_do_not_save(self):
        dialog = self.dialog(values={"ALIGNMENT": "Road A"})
        dialog.algorithmFinished.emit(False, {})
        self.assertEqual(self.saved(), {})
        dialog.close()
        self.assertEqual(self.saved(), {})

    def test_project_switch_and_algorithm_isolation(self):
        old = self.dialog(values={"ALIGNMENT": "Road A"})
        old.algorithmFinished.emit(True, {})
        self.assertNotEqual(self.value(self.dialog("landxml_design_review"), "ALIGNMENT"), "Road A")
        self.project.clear()
        old.algorithmFinished.emit(True, {})
        self.assertEqual(self.saved(), {})
        self.assertNotEqual(self.value(self.dialog(), "ALIGNMENT"), "Road A")

    def test_invalid_saved_json_is_ignored(self):
        for raw in ("not json", "[]", '{"OUTPUT":"old.xml","UNKNOWN":1}'):
            self.project.writeEntry("LandXML", "processing/prepare_landxml", raw)
            dialog = self.dialog()
            self.assertEqual(self.value(dialog, "SECTION_INTERVAL"), 20)

    def test_crs_and_criteria_are_restored(self):
        dialog = self.dialog("landxml_design_review", {
            "STANDARD": 1, "DESIGN_SPEED": 55, "OUTPUT_CRS": QgsCoordinateReferenceSystem("EPSG:26916")
        })
        dialog.algorithmFinished.emit(True, {})
        restored = self.dialog("landxml_design_review")
        self.assertEqual(self.value(restored, "STANDARD"), 1)
        self.assertEqual(self.value(restored, "DESIGN_SPEED"), 55)
        self.assertEqual(self.value(restored, "OUTPUT_CRS").authid(), "EPSG:26916")

    def test_all_algorithms_have_project_dialogs_and_keep_script_defaults(self):
        for algorithm in self.provider.algorithms():
            dialog = self.dialog(algorithm.name())
            self.assertTrue(hasattr(dialog, "_remember"), algorithm.name())
        self.dialog(values={"SECTION_INTERVAL": 25}).algorithmFinished.emit(True, {})
        algorithm = next(a for a in self.provider.algorithms() if a.name() == "prepare_landxml").create()
        self.assertEqual(algorithm.parameterDefinition("SECTION_INTERVAL").defaultValue(), 20)

    def test_actual_successful_run_remembers_inputs(self):
        source = str(ROOT / "tests" / "fixtures" / "civil3d" / "road_minimal.xml")
        dialog = self.dialog("inspect_landxml", {"INPUT": source})
        finished = []
        dialog.algorithmFinished.connect(lambda success, _results: finished.append(success))
        dialog.runAlgorithm()
        deadline = time.monotonic() + 15
        while not finished and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.01)
        self.assertEqual(finished, [True])
        self.assertEqual(Path(self.saved("inspect_landxml")["INPUT"]), Path(source))


if __name__ == "__main__":
    unittest.main()
