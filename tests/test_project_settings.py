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
from qgis.testing import start_app

ROOT = Path(__file__).resolve().parents[1]
if "landxml_plugin" not in sys.modules:
    spec = importlib.util.spec_from_file_location(
        "landxml_plugin", ROOT / "__init__.py", submodule_search_locations=[str(ROOT)]
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)



def panel_wrappers(panel):
    """Dialog wrappers by name: an attribute up to QGIS 4.2, a method in 4.4 (as compat.panel_wrappers)."""
    wrappers = panel.wrappers
    wrappers = wrappers() if callable(wrappers) else wrappers
    if isinstance(wrappers, dict):
        return wrappers
    return {wrapper.parameterDefinition().name(): wrapper for wrapper in wrappers}      # QGIS 4.4: a list

class ProjectSettingsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = start_app()
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
        return panel_wrappers(dialog.mainWidget())[name].parameterValue()

    def saved(self, name="prepare_landxml"):
        return json.loads(self.project.readEntry("LandXML", "processing/" + name, "{}")[0])

    def test_scene_road_switch_greys_road_inputs_and_keeps_old_selections(self):
        def enabled(dialog, name):
            wrapper = panel_wrappers(dialog.mainWidget())[name]
            widget = wrapper.wrappedWidget() if hasattr(wrapper, "wrappedWidget") else None
            return (widget or wrapper.widget).isEnabled()

        dialog = self.dialog("landxml_3d_scene", {"SYN_CARRIAGEWAY": 9.5, "SYN_GENERATE": False})
        self.assertFalse(self.value(dialog, "SYN_GENERATE"))
        for name in ("SYNTHETIC", "SYN_CARRIAGEWAY", "SYN_MAX_WIDTH", "SYN_STRUCTURES", "SYN_MATCH_PROFILE"):
            self.assertFalse(enabled(dialog, name), name)
        self.assertTrue(enabled(dialog, "CORRIDOR"))                       # scene area stays available
        dialog.setParameters({"SYN_CARRIAGEWAY": 9.5, "SYN_GENERATE": True})
        self.assertTrue(enabled(dialog, "SYN_CARRIAGEWAY"))
        self.assertEqual(self.value(dialog, "SYN_CARRIAGEWAY"), 9.5)       # toggling keeps the value
        dialog.setParameters({"SYN_GENERATE": False, "SYN_CARRIAGEWAY": 9.5})
        self.assertEqual(self.value(dialog, "SYN_CARRIAGEWAY"), 9.5)
        # history or saved settings from before the switch: a chosen road still means "generate"
        old = self.dialog("landxml_3d_scene", {"SYNTHETIC": "Main"})
        self.assertTrue(self.value(old, "SYN_GENERATE"))
        self.assertTrue(enabled(old, "SYNTHETIC"))
        algorithm = next(a for a in self.provider.algorithms() if a.name() == "landxml_3d_scene")
        labels = {d.name(): d.description() for d in algorithm.parameterDefinitions()}
        self.assertTrue(labels["CORRIDOR"].startswith("Limit scene area along alignment"))
        self.assertIn("does not generate a road", labels["CORRIDOR"])
        self.assertTrue(labels["CORRIDOR_WIDTH"].startswith("Surrounding terrain width each side (0 = Auto)"))
        self.assertTrue(labels["SYN_CARRIAGEWAY"].startswith("Road: total carriageway width"))
        self.assertTrue(labels["SYN_MAX_WIDTH"].startswith("Road: maximum batter search distance"))
        self.assertEqual(algorithm.parameterDefinition("CORRIDOR_WIDTH").defaultValue(), 0.0)
        spin = panel_wrappers(dialog.mainWidget())["CORRIDOR_WIDTH"].wrappedWidget()
        self.assertEqual(spin.specialValueText(), "Auto")

    def test_dimensional_labels_refresh_without_converting_values(self):
        from qgis.PyQt.QtWidgets import QLabel

        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "feet.xml"
            path.write_text(
                '<LandXML><Units><Imperial linearUnit="USSurveyFoot"/></Units></LandXML>'
            )
            dialog = self.dialog(
                "landxml_profile_cut_fill",
                {"INPUT": str(path), "VERTICAL_UNIT": 3, "INTERVAL": 15.0},
            )

            def label(prefix):
                return next(
                    l.text()
                    for l in dialog.findChildren(QLabel)
                    if l.text().startswith(prefix)
                )

            self.assertIn("[ftUS]", label("Table station interval"))
            self.assertIn("[ftUS]", label("Flag fills deeper than"))
            self.assertIn("[ftUS]", label("Level-section estimate: formation width"))
            dialog.setParameters({"VERTICAL_UNIT": 1})
            self.assertIn("[m]", label("Flag fills deeper than"))
            self.assertIn("[ftUS]", label("Table station interval"))
            self.assertEqual(self.value(dialog, "INTERVAL"), 15.0)
            dialog.setParameters({"INPUT": ""})
            self.assertIn("[unknown]", label("Table station interval"))

    def test_custom_criteria_and_output_crs_unit_labels(self):
        from qgis.PyQt.QtWidgets import QLabel

        dialog = self.dialog(
            "landxml_design_review",
            {"CRITERIA_FILE": str(ROOT / "criteria/aashto_2018_us_customary.json")},
        )
        labels = lambda: [l.text() for l in dialog.findChildren(QLabel)]
        self.assertTrue(
            any(t.startswith("Design speed (") and "[mph]" in t for t in labels())
        )
        dialog.setParameters({"CRITERIA_FILE": str(ROOT / "criteria/aashto_2018.json")})
        self.assertTrue(
            any(t.startswith("Design speed (") and "[km/h]" in t for t in labels())
        )
        dialog = self.dialog(
            "landxml_grading_quantities", {"OUTPUT_CRS": "EPSG:2277", "VOLUME_UNITS": 0}
        )
        self.assertTrue(
            any(t.startswith("Civil 3D cut volume") and "[CY]" in t for t in labels())
        )
        dialog.setParameters({"VOLUME_UNITS": 2})
        self.assertTrue(
            any(t.startswith("Civil 3D cut volume") and "[CF]" in t for t in labels())
        )

    def test_dimensional_input_bases_match_the_tool_contract(self):
        expected = {
            "prepare_landxml": {"PROFILE_INTERVAL": "source horizontal", "LEFT_WIDTH": "source horizontal"},
            "landxml_station_points": {"INTERVAL": "source horizontal"},
            "landxml_profiles_to_vector": {"POINT_INTERVAL": "source horizontal", "MAP_OFFSET": "source horizontal"},
            "landxml_complete_road_design": {"RESOLUTION": "output CRS", "CONTOUR_INTERVAL": "source elevation"},
            "landxml_profile_cut_fill": {"INTERVAL": "source horizontal", "FORMATION_WIDTH": "source horizontal", "HIGH_FILL": "source elevation"},
            "landxml_corridor_quantities": {"MAX_SPACING": "source horizontal"},
            "landxml_cross_section_sheets": {"HALF_WIDTH": "source horizontal"},
            "landxml_design_review": {"MAX_CHANGE": "source elevation", "DESIGN_SPEED": "criteria speed"},
            "landxml_basin_storage": {"FREEBOARD": "source elevation", "REQUIRED_VOLUME": "selected volume"},
            "landxml_grading_quantities": {"CIVIL3D_AREA": "output CRS units²", "CIVIL3D_CUT": "selected volume"},
            "landxml_platform_levels": {"MIN_AREA": "output CRS units²"},
            "landxml_earthwork_balance": {"TOPSOIL_DEPTH": "source elevation"},
            "landxml_3d_scene": {"STATION_FROM": "source horizontal", "SYN_CARRIAGEWAY": "output CRS", "WATER": "source elevation"},
        }
        for algorithm in self.provider.algorithms():
            contract = dict(expected.get(algorithm.name(), {}))
            if algorithm.parameterDefinition("DX") is not None:
                contract["DX"] = "output CRS"
            for name, units in contract.items():
                self.assertIn(units, algorithm.parameterDefinition(name).description(), (algorithm.name(), name))

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
