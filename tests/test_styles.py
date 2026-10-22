"""Road-review presets and default GeoPackage style persistence in real QGIS."""

import sqlite3
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

from . import test_qgis_processing as runtime
from qgis.core import QgsExpression, QgsExpressionContext, QgsFeature, QgsProcessingContext, QgsProcessingFeedback, QgsVectorLayer
from landxml_plugin.styles import PRESETS, apply_preset


class StyleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        runtime.QgisProcessingTests.setUpClass()
        cls.algorithms = runtime.QgisProcessingTests.algorithms

    def setUp(self):
        self.context = QgsProcessingContext()
        self.feedback = QgsProcessingFeedback()

    def tearDown(self):
        self.context.temporaryLayerStore().removeAllMapLayers()

    def parameters(self, path):
        return {"INPUT": path, "METHOD": 0, "OUTPUT_CRS": runtime.QgsCoordinateReferenceSystem("EPSG:32637")}

    def test_presets_support_expected_geometry_and_available_fields(self):
        for kind in PRESETS:
            geometry = "Point" if kind in ("station_points", "profile_controls", "section_points") else "LineString"
            layer = QgsVectorLayer(geometry + '?field=surface_name:string&field=station:double&field=elev:double', kind, "memory")
            apply_preset(layer, kind, 2)
            self.assertIsNotNone(layer.renderer(), kind)
            if kind == "station_points":
                self.assertTrue(layer.labelsEnabled())
            if kind == "contours":
                self.assertEqual(layer.renderer().type(), "RuleRenderer")
                self.assertIn('/ 10', layer.renderer().rootRule().children()[0].filterExpression())

    def test_surface_categories_use_source_attributes(self):
        layer = QgsVectorLayer('LineString?field=surface_name:string', "sections", "memory")
        apply_preset(layer, "cross_sections")
        expression = QgsExpression(layer.renderer().classAttribute())
        self.assertFalse(expression.hasParserError(), expression.parserErrorString())
        for name, expected in (("EG", "Existing ground"), ("Prepared Existing Ground", "Existing ground"),
                               ("FG", "Design / finished"), ("Road Datum", "Datum / subgrade"), ("Terrain A", "Other")):
            feature = QgsFeature(layer.fields())
            feature["surface_name"] = name
            context = QgsExpressionContext()
            context.setFeature(feature)
            context.setFields(layer.fields())
            self.assertEqual(expression.evaluate(context), expected)

    def test_complete_package_styles_are_saved_and_reloaded_without_map_loading(self):
        with tempfile.TemporaryDirectory() as directory:
            params = self.parameters(runtime.CORRIDOR)
            params.update(OUTPUT_DIR=directory, INCLUDE_DEM=False, INCLUDE_CONTOURS=True, RESOLUTION=1, CONTOUR_INTERVAL=1)
            with patch.object(self.feedback, "pushWarning") as warnings:
                result, success = self.algorithms["landxml_complete_road_design"].run(params, self.context, self.feedback)
            self.assertTrue(success)
            self.assertEqual(result["OUTPUT_DIR"], directory)
            self.assertFalse(any("style" in str(call).lower() for call in warnings.call_args_list))
            package = Path(directory) / "corridor_synthetic_GIS.gpkg"
            database = sqlite3.connect(package)
            rows = database.execute('SELECT f_table_name, useAsDefault, styleQML FROM layer_styles').fetchall()
            database.close()
            tables = {row[0] for row in rows}
            self.assertIn("alignments", tables)
            self.assertIn("cross_sections", tables)
            self.assertIn("station_points", tables)
            self.assertTrue(all(row[1] == 1 for row in rows))
            layer = QgsVectorLayer(str(package) + "|layername=station_points", "reopened", "ogr")
            self.assertTrue(layer.labelsEnabled())
            self.assertEqual(layer.renderer().symbol().color().name(), "#333333")
            layer = None
            for contour in Path(directory).glob("*_Contours.gpkg"):
                layer = QgsVectorLayer(str(contour) + "|layername=contours", "contours", "ogr")
                self.assertEqual(layer.renderer().type(), "RuleRenderer")
                self.assertTrue(layer.labelsEnabled())
                layer = None
            self.context.temporaryLayerStore().removeAllMapLayers()

    def test_individual_extraction_styles_memory_and_geopackage_outputs(self):
        with tempfile.TemporaryDirectory() as directory:
            for name, kind in (("landxml_alignments_to_vector", "alignments"),
                               ("landxml_station_points", "station_points"),
                               ("landxml_surface_boundary", "surface_boundary"),
                               ("landxml_3d_centerlines", "centerlines_3d"),
                               ("landxml_profiles_to_vector", "profiles"),
                               ("landxml_cross_sections", "cross_sections"),
                               ("landxml_feature_lines", "feature_lines"),
                               ("landxml_breaklines", "breaklines")):
                params = self.parameters(runtime.CORRIDOR)
                output = str(Path(directory) / (kind + ".gpkg"))
                params["OUTPUT"] = output
                if kind == "profiles":
                    params["CONTROL_POINTS"] = str(Path(directory) / "controls.gpkg")
                result, success = self.algorithms[name].run(params, self.context, self.feedback)
                self.assertTrue(success, name)
                layer = QgsVectorLayer(output, kind, "ogr")
                self.assertTrue(layer.isValid())
                self.assertTrue(layer.renderer().type() in ("singleSymbol", "categorizedSymbol"))
                if kind == "profiles":
                    self.assertTrue(layer.labelsEnabled())
                    self.assertEqual(layer.labeling().settings().fieldName, "label_text")
                    controls = QgsVectorLayer(params["CONTROL_POINTS"], "controls", "ogr")
                    self.assertTrue(controls.labelsEnabled())
                    self.assertEqual(controls.labeling().settings().fieldName, "label_text")
                    controls = None
                database = sqlite3.connect(output)
                self.assertEqual(database.execute("SELECT count(*) FROM layer_styles WHERE useAsDefault=1").fetchone()[0], 1)
                database.close()
                layer = None
                self.context.temporaryLayerStore().removeAllMapLayers()
            params = self.parameters(runtime.CORRIDOR)
            params["OUTPUT"] = "memory:"
            result, success = self.algorithms["landxml_station_points"].run(params, self.context, self.feedback)
            self.assertTrue(success)
            self.assertTrue(self.context.getMapLayer(result["OUTPUT"]).labelsEnabled())
            self.context.temporaryLayerStore().removeAllMapLayers()


if __name__ == "__main__":
    unittest.main()
