"""Road-review presets and default GeoPackage style persistence in real QGIS."""

import sqlite3
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

from . import test_qgis_processing as runtime
from qgis.PyQt.QtCore import Qt
from qgis.core import QgsExpression, QgsExpressionContext, QgsFeature, QgsProcessingContext, QgsProcessingFeedback, QgsVectorLayer
from landxml_plugin.styles import PRESETS, apply_preset, station_text


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

    def test_station_labels_follow_declared_source_units(self):
        for unit in ("foot", "internationalFoot", "USSurveyFoot"):
            for station, label in ((5000, "50+00"), (700, "7+00"), (70000, "700+00"),
                                   (80180, "801+80"), (999.999, "10+00"), (-700, "-7+00"), (712.5, "7+12.50")):
                self.assertEqual(station_text(station, unit), label)
        self.assertEqual(station_text(5000, "meter"), "5+000")
        self.assertEqual(station_text(712.5, "meter"), "0+712.50")
        self.assertEqual(station_text(5000, None), "5000")

    def test_contour_labels_and_surface_palettes(self):
        for name, color, pen_style in (("Prepared Existing Ground", "#a6611a", Qt.PenStyle.DashLine),
                                       ("Prepared Proposed Terrain", "#0072b2", Qt.PenStyle.SolidLine)):
            layer = QgsVectorLayer('LineString?field=elev:double', name, "memory")
            apply_preset(layer, "contours", 0.1, surface_name=name)
            minor = layer.renderer().rootRule().children()[1].symbol()
            self.assertEqual(minor.color().name(), color)
            self.assertEqual(minor.symbolLayer(0).penStyle(), pen_style)
            self.assertGreaterEqual(minor.width(), 0.25)
            expression = QgsExpression(layer.labeling().settings().fieldName)
            self.assertFalse(expression.hasParserError())
            for elevation, label in ((3650, "3650"), (3650.5, "3650.5")):
                feature = QgsFeature(layer.fields())
                feature["elev"] = elevation
                context = QgsExpressionContext()
                context.setFeature(feature)
                context.setFields(layer.fields())
                self.assertEqual(expression.evaluate(context), label)

    def test_station_exports_persist_declared_unit_stationing(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "feet.xml"
            source.write_text(Path(runtime.CORRIDOR).read_text().replace(
                '<Metric linearUnit="meter" verticalLinearUnit="meter" areaUnit="squareMeter" volumeUnit="cubicMeter"/>',
                '<Imperial linearUnit="usSurveyFoot" verticalLinearUnit="usSurveyFoot" areaUnit="squareFoot" volumeUnit="cubicFoot"/>',
            ).replace('staStart="0" length="200"', 'staStart="700" length="200"'))
            for name in ("landxml_station_points", "landxml_complete_road_design"):
                params = self.parameters(str(source))
                params["OUTPUT_CRS"] = runtime.QgsCoordinateReferenceSystem("EPSG:2277")
                if name == "landxml_station_points":
                    output = str(Path(directory) / "stations.gpkg")
                    params["OUTPUT"] = output
                else:
                    params["OUTPUT_DIR"] = str(Path(directory) / "complete")
                    for parameter in self.algorithms[name].parameterDefinitions():
                        if parameter.name().startswith("INCLUDE_"):
                            params[parameter.name()] = False
                    params["INCLUDE_STATIONS"] = True
                    output = str(Path(params["OUTPUT_DIR"]) / "feet_GIS.gpkg") + "|layername=station_points"
                _, success = self.algorithms[name].run(params, self.context, self.feedback)
                self.assertTrue(success, self.feedback.textLog())
                layer = QgsVectorLayer(output, "stations", "ogr")
                first = min(layer.getFeatures(), key=lambda feature: feature["station"])
                self.assertEqual(first["station"], 700)
                self.assertEqual(first["station_label"], "7+00")
                self.assertEqual(layer.labeling().settings().fieldName, "station_label")
                first, layer = None, None
                self.context.temporaryLayerStore().removeAllMapLayers()

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
            self.assertEqual(layer.labeling().settings().fieldName, "station_label")
            self.assertTrue(all("+" in feature["station_label"] for feature in layer.getFeatures()))
            self.assertEqual(layer.renderer().symbol().color().name(), "#333333")
            layer = None
            for contour in Path(directory).glob("*_Contours.gpkg"):
                layer = QgsVectorLayer(str(contour) + "|layername=contours", "contours", "ogr")
                self.assertEqual(layer.renderer().type(), "RuleRenderer")
                self.assertTrue(layer.labelsEnabled())
                expected = "#a6611a" if "surface_01" in contour.name else "#0072b2"
                self.assertEqual(layer.renderer().rootRule().children()[1].symbol().color().name(), expected)
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
                if kind == "station_points":
                    self.assertEqual(layer.labeling().settings().fieldName, "station_label")
                    self.assertTrue(all("+" in feature["station_label"] for feature in layer.getFeatures()))
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
